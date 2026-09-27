"""Prepare an anonymous listening test and summarize human ratings.

Usage: python -m voice_enhancer.benchmark prepare --corpus media/corpus --output media/benchmark
       python -m voice_enhancer.benchmark inspect --output media/benchmark
       python -m voice_enhancer.benchmark summarize --output media/benchmark
"""

import argparse
import asyncio
import csv
import json
import random
import re
import shutil
from pathlib import Path

from voice_enhancer.application.media_validation import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaKind,
)
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.application.quality import check_media
from voice_enhancer.config import settings
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.providers import select_provider

CRITERIA = (
    "noise_reduction",
    "naturalness",
    "echo_handling",
    "body",
    "intelligibility",
    "artifact_control",
    "overall_preference",
)
MIN_CORPUS_SIZE = 20


def _kind(path: Path) -> MediaKind | None:
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    return None


async def _listening_sample(ffmpeg: FFmpegProcessor, source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg._run(
        ffmpeg.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output),
    )


async def prepare(
    corpus: Path,
    output: Path,
    *,
    provider_names: list[str],
    preset: Preset,
    seed: int,
    allow_small: bool,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
    deepfilter_bin: str | None = None,
) -> None:
    clips = sorted(path for path in corpus.iterdir() if path.is_file() and _kind(path))
    if len(clips) < MIN_CORPUS_SIZE and not allow_small:
        raise ValueError(f"Benchmark needs at least {MIN_CORPUS_SIZE} clips; found {len(clips)}")
    if not clips:
        raise ValueError("Corpus contains no supported media")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty to avoid mixing benchmark runs")
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg = FFmpegProcessor(
        ffmpeg_bin or settings.ffmpeg_bin,
        ffprobe_bin or settings.ffprobe_bin,
        settings.ffmpeg_timeout_seconds,
    )
    profile = profile_for(preset)
    rng = random.Random(seed)
    manifest: list[dict] = []
    rows: list[dict] = []
    for index, source in enumerate(clips, start=1):
        clip_id = f"clip{index:03d}"
        kind = _kind(source)
        assert kind is not None
        variants: list[tuple[str, Path, dict]] = [("original", source, {})]
        for provider_name in provider_names:
            run_dir = output / "runs" / clip_id / provider_name
            run_dir.mkdir(parents=True)
            run_source = run_dir / f"source{source.suffix.lower()}"
            shutil.copyfile(source, run_source)
            provider = select_provider(
                provider_name,
                elevenlabs_api_key=settings.elevenlabs_api_key,
                deepfilter_bin=deepfilter_bin or settings.deepfilter_bin,
            )
            processed = await MediaProcessingService(ffmpeg, provider).process(
                run_source, kind=kind, profile=profile
            )
            checks = await check_media(ffmpeg, source, processed.output_path, kind)
            checks["compute_seconds"] = round(processed.compute_seconds, 3)
            variants.append((provider_name, processed.output_path, checks))
        rng.shuffle(variants)
        for sample_index, (provider_name, variant, checks) in enumerate(variants):
            sample_id = chr(ord("A") + sample_index)
            sample_path = output / "samples" / clip_id / f"{sample_id}.m4a"
            await _listening_sample(ffmpeg, variant, sample_path)
            manifest.append(
                {
                    "clip_id": clip_id,
                    "source_name": source.name,
                    "sample_id": sample_id,
                    "provider": provider_name,
                    "checks": checks,
                }
            )
            rows.append(
                {
                    "reviewer": "",
                    "clip_id": clip_id,
                    "sample_id": sample_id,
                    **dict.fromkeys(CRITERIA, ""),
                }
            )
    (output / "mapping.private.json").write_text(
        json.dumps({"preset": preset.value, "seed": seed, "samples": manifest}, indent=2),
        encoding="utf-8",
    )
    with (output / "ratings.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["reviewer", "clip_id", "sample_id", *CRITERIA])
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"Prepared {len(clips)} clips. Share samples/ and ratings.csv; keep mapping.private.json hidden."
    )


def summarize(output: Path) -> dict:
    manifest = json.loads((output / "mapping.private.json").read_text(encoding="utf-8"))
    mapping = {(row["clip_id"], row["sample_id"]): row["provider"] for row in manifest["samples"]}
    ratings: dict[str, list[dict[str, int]]] = {}
    paired: dict[tuple[str, str], dict[str, int]] = {}
    with (output / "ratings.csv").open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if not any(row.get(field) for field in CRITERIA):
                continue
            key = (row["clip_id"], row["sample_id"])
            if key not in mapping:
                raise ValueError(f"Unknown sample: {key}")
            values = {}
            for field in CRITERIA:
                raw = row.get(field, "")
                if not re.fullmatch(r"[1-5]", raw or ""):
                    raise ValueError(f"{key}: {field} must be an integer from 1 to 5")
                values[field] = int(raw)
            provider = mapping[key]
            pair_key = (row.get("reviewer", ""), row["clip_id"])
            if provider in paired.setdefault(pair_key, {}):
                raise ValueError(
                    f"Duplicate rating for reviewer/clip/provider: {pair_key}, {provider}"
                )
            paired[pair_key][provider] = values["overall_preference"]
            ratings.setdefault(provider, []).append(values)
    if not ratings:
        raise ValueError("No completed ratings")

    def mean_compute_seconds(provider: str) -> float | None:
        durations = [
            sample.get("checks", {}).get("compute_seconds")
            for sample in manifest["samples"]
            if sample["provider"] == provider
        ]
        measured = [duration for duration in durations if duration is not None]
        return round(sum(measured) / len(measured), 2) if measured else None

    report = {
        "rated_samples": sum(map(len, ratings.values())),
        "providers": {
            provider: {
                "rated_samples": len(values),
                "mean_scores": {
                    field: round(sum(row[field] for row in values) / len(values), 2)
                    for field in CRITERIA
                },
                "mean_compute_seconds": mean_compute_seconds(provider),
            }
            for provider, values in sorted(ratings.items())
        },
    }
    for provider, details in report["providers"].items():
        if provider == "original":
            continue
        comparisons = [
            (scores[provider], scores["original"])
            for scores in paired.values()
            if provider in scores and "original" in scores
        ]
        details["versus_original"] = {
            "paired_reviews": len(comparisons),
            "wins": sum(candidate > original for candidate, original in comparisons),
            "ties": sum(candidate == original for candidate, original in comparisons),
            "losses": sum(candidate < original for candidate, original in comparisons),
        }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def inspect(output: Path) -> dict:
    """Summarize processing checks without pretending they measure listening quality."""
    manifest = json.loads((output / "mapping.private.json").read_text(encoding="utf-8"))
    samples = manifest["samples"]
    providers = sorted({row["provider"] for row in samples if row["provider"] != "original"})
    report = {
        "clips": len({row["clip_id"] for row in samples}),
        "blind_samples": len(samples),
        "human_ratings_complete": False,
        "providers": {},
    }
    for provider in providers:
        checks = [row["checks"] for row in samples if row["provider"] == provider]
        report["providers"][provider] = {
            "processed_clips": len(checks),
            "decodable_clips": sum(bool(row.get("decodable")) for row in checks),
            "sample_rate_hz": sorted({row["sample_rate_hz"] for row in checks}),
            "maximum_absolute_duration_drift_ms": max(
                abs(row["duration_drift_ms"]) for row in checks
            ),
            "maximum_sample_peak_dbfs": max(
                (row["sample_peak_dbfs"] for row in checks if row["sample_peak_dbfs"] is not None),
                default=None,
            ),
            "mean_compute_seconds": round(
                sum(row["compute_seconds"] for row in checks) / len(checks), 2
            ),
        }
    (output / "technical_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = commands.add_parser("prepare")
    prepare_cmd.add_argument("--corpus", type=Path, required=True)
    prepare_cmd.add_argument("--output", type=Path, required=True)
    prepare_cmd.add_argument(
        "--providers",
        nargs="+",
        choices=("ffmpeg", "deepfilter", "elevenlabs"),
        default=["ffmpeg", "deepfilter"],
    )
    prepare_cmd.add_argument("--preset", type=Preset, choices=list(Preset), default=Preset.STUDIO)
    prepare_cmd.add_argument("--seed", type=int, default=1)
    prepare_cmd.add_argument("--allow-small", action="store_true")
    prepare_cmd.add_argument("--ffmpeg-bin")
    prepare_cmd.add_argument("--ffprobe-bin")
    prepare_cmd.add_argument("--deepfilter-bin")
    summary_cmd = commands.add_parser("summarize")
    summary_cmd.add_argument("--output", type=Path, required=True)
    inspect_cmd = commands.add_parser("inspect")
    inspect_cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        asyncio.run(
            prepare(
                args.corpus,
                args.output,
                provider_names=args.providers,
                preset=args.preset,
                seed=args.seed,
                allow_small=args.allow_small,
                ffmpeg_bin=args.ffmpeg_bin,
                ffprobe_bin=args.ffprobe_bin,
                deepfilter_bin=args.deepfilter_bin,
            )
        )
    elif args.command == "inspect":
        print(json.dumps(inspect(args.output), indent=2))
    else:
        print(json.dumps(summarize(args.output), indent=2))


if __name__ == "__main__":
    main()
