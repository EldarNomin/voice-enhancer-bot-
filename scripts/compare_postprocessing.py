"""Compare repeated afftdn on/off using cached raw AI outputs; no paid API calls."""

import argparse
import asyncio
import csv
import json
from pathlib import Path

from compare_audio import read
from pystoi import stoi

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor


async def run(root: Path, output: Path) -> None:
    fixtures = [
        {**row, "language": "en", "condition": "real_noise"}
        for row in json.loads((root / "voicebank-manifest.json").read_text())
    ] + json.loads((root / "russian-manifest.json").read_text())
    ffmpeg = FFmpegProcessor()
    rows = []
    for fixture in fixtures:
        folder = root / fixture["id"]
        ref = read(folder / "clean.wav")
        for provider in ("deepfilter", "gtcrn"):
            row = {key: fixture[key] for key in ("id", "language", "condition")}
            row["provider"] = provider
            for denoise in (True, False):
                candidate = folder / f"{provider}_post_{denoise}.m4a"
                await ffmpeg.enhance_audio(
                    folder / f"{provider}.wav", candidate,
                    profile_for(Preset.NATURAL), denoise=denoise,
                )
                decoded = folder / "post_metric.wav"
                await ffmpeg._run("ffmpeg", "-v", "error", "-y", "-i", str(candidate),
                                  "-ar", "16000", "-ac", "1", str(decoded))
                signal = read(decoded)
                size = min(len(ref), len(signal))
                row["stoi_double_denoise" if denoise else "stoi_single_denoise"] = float(
                    stoi(ref[:size], signal[:size], 16000)
                )
                decoded.unlink()
            rows.append(row)
        print(fixture["id"], flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for provider in ("deepfilter", "gtcrn"):
        for language in ("en", "ru"):
            group = [row for row in rows if row["provider"] == provider
                     and row["language"] == language]
            print(provider, language, {
                key: sum(row[key] for row in group) / len(group)
                for key in ("stoi_double_denoise", "stoi_single_denoise")
            })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/audio-comparison"))
    parser.add_argument("--output", type=Path,
                        default=Path("docs/audio-comparison/postprocessing.csv"))
    args = parser.parse_args()
    asyncio.run(run(args.root, args.output))
