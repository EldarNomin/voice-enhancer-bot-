import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.quality import check_media
from voice_enhancer.benchmark import CRITERIA, inspect, summarize


class ProbeFFmpeg:
    ffmpeg_bin = "ffmpeg"

    def __init__(self, source: dict, result: dict) -> None:
        self.source = source
        self.result = result
        self.decoded = False

    async def probe(self, path: Path) -> dict:
        return self.source if path.name == "source.mp4" else self.result

    async def _run(self, *args: str) -> str:
        self.decoded = True
        return ""


@pytest.mark.asyncio
async def test_quality_check_preserves_video_properties_and_decodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voice_enhancer.application.quality.measure_sample_peak", AsyncMock(return_value=-1.0)
    )
    video = {
        "codec_type": "video",
        "width": 1080,
        "height": 1920,
        "r_frame_rate": "30/1",
        "codec_name": "h264",
        "duration": "10.0",
    }
    source = {"format": {"duration": "10.0"}, "streams": [video]}
    result = {
        "format": {"duration": "10.03"},
        "streams": [video, {"codec_type": "audio", "sample_rate": "48000", "duration": "10.02"}],
    }
    ffmpeg = ProbeFFmpeg(source, result)
    checks = await check_media(ffmpeg, Path("source.mp4"), Path("result.mp4"), MediaKind.VIDEO)
    assert checks["duration_drift_ms"] == 30
    assert checks["av_duration_drift_ms"] == 20
    assert ffmpeg.decoded


@pytest.mark.asyncio
async def test_quality_check_rejects_duration_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "voice_enhancer.application.quality.measure_sample_peak", AsyncMock(return_value=-1.0)
    )
    source = {"format": {"duration": "10"}, "streams": []}
    result = {
        "format": {"duration": "9"},
        "streams": [{"codec_type": "audio", "sample_rate": "48000"}],
    }
    with pytest.raises(ValueError, match="Duration drift"):
        await check_media(
            ProbeFFmpeg(source, result), Path("source.mp4"), Path("result.m4a"), MediaKind.AUDIO
        )


def test_summary_counts_blind_wins_against_original(tmp_path: Path) -> None:
    samples = [
        {"clip_id": "clip001", "sample_id": "A", "provider": "deepfilter"},
        {"clip_id": "clip001", "sample_id": "B", "provider": "original"},
    ]
    (tmp_path / "mapping.private.json").write_text(
        json.dumps({"samples": samples}), encoding="utf-8"
    )
    with (tmp_path / "ratings.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["reviewer", "clip_id", "sample_id", *CRITERIA])
        writer.writeheader()
        writer.writerow(
            {
                "reviewer": "one",
                "clip_id": "clip001",
                "sample_id": "A",
                **dict.fromkeys(CRITERIA, 5),
            }
        )
        writer.writerow(
            {
                "reviewer": "one",
                "clip_id": "clip001",
                "sample_id": "B",
                **dict.fromkeys(CRITERIA, 3),
            }
        )
    report = summarize(tmp_path)
    assert report["providers"]["deepfilter"]["versus_original"] == {
        "paired_reviews": 1,
        "wins": 1,
        "ties": 0,
        "losses": 0,
    }


def test_summary_rejects_partial_scores(tmp_path: Path) -> None:
    (tmp_path / "mapping.private.json").write_text(
        json.dumps({"samples": [{"clip_id": "clip001", "sample_id": "A", "provider": "original"}]}),
        encoding="utf-8",
    )
    with (tmp_path / "ratings.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["reviewer", "clip_id", "sample_id", *CRITERIA])
        writer.writeheader()
        writer.writerow({"clip_id": "clip001", "sample_id": "A", "naturalness": "4"})
    with pytest.raises(ValueError, match="noise_reduction"):
        summarize(tmp_path)


def test_inspect_reports_technical_checks_without_quality_claim(tmp_path: Path) -> None:
    samples = [
        {"clip_id": "clip001", "sample_id": "A", "provider": "original", "checks": {}},
        {
            "clip_id": "clip001",
            "sample_id": "B",
            "provider": "deepfilter",
            "checks": {
                "decodable": True,
                "sample_rate_hz": 48000,
                "duration_drift_ms": -10,
                "sample_peak_dbfs": -1.2,
                "compute_seconds": 2.0,
            },
        },
    ]
    (tmp_path / "mapping.private.json").write_text(
        json.dumps({"samples": samples}), encoding="utf-8"
    )
    report = inspect(tmp_path)
    assert report["clips"] == 1
    assert report["human_ratings_complete"] is False
    assert report["providers"]["deepfilter"]["maximum_absolute_duration_drift_ms"] == 10
