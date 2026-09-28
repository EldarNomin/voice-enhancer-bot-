"""Reproducible signal benchmark. Install .[benchmark]; fetch_comparison_audio.py first.

Metrics describe paired signal fidelity/intelligibility, not listener preference.
Russian mixtures derive recorded noise from VoiceBank-DEMAND pairs (noisy - clean).
"""

import argparse
import asyncio
import csv
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from pystoi import stoi
from scipy.signal import fftconvolve, resample_poly

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.providers import DeepFilterNetProvider


def read(path: Path) -> np.ndarray:
    audio, sr = sf.read(path, always_2d=True)
    signal = audio.mean(axis=1)
    if sr != 16000:
        from math import gcd

        divisor = gcd(sr, 16000)
        signal = resample_poly(signal, 16000 // divisor, sr // divisor)
    return signal


def si_sdr(reference: np.ndarray, candidate: np.ndarray) -> float:
    ref = reference - reference.mean()
    test = candidate - candidate.mean()
    projected = ref * np.dot(test, ref) / max(np.dot(ref, ref), 1e-12)
    return float(
        10 * np.log10((np.sum(projected**2) + 1e-12) / (np.sum((test - projected) ** 2) + 1e-12))
    )


def prepare_russian(root: Path) -> list[dict]:
    rows = []
    pairs = json.loads((root / "voicebank-manifest.json").read_text())
    for index, source in enumerate(sorted((root / "fleurs-ru").glob("*.wav"))):
        clean = read(source)
        pair = root / pairs[index * 3]["id"]
        noise = read(pair / "noisy.wav") - read(pair / "clean.wav")
        noise = np.tile(noise, int(np.ceil(len(clean) / len(noise))))[: len(clean)]
        for condition in ("clean", "noise5", "noise15", "echo"):
            folder = root / f"ru{index + 1}_{condition}"
            folder.mkdir(exist_ok=True)
            ref = clean.copy()
            if condition == "clean":
                noisy = clean.copy()
            elif condition == "echo":
                impulse = np.zeros(8001)
                impulse[[0, 1280, 3040, 5440, 8000]] = [1, 0.45, 0.28, 0.16, 0.08]
                noisy = fftconvolve(clean, impulse)[: len(clean)]
            else:
                snr = int(condition.removeprefix("noise"))
                gain = np.sqrt(np.mean(clean**2) / max(np.mean(noise**2), 1e-12))
                noisy = clean + noise * gain * 10 ** (-snr / 20)
            # Joint attenuation preserves the specified SNR and avoids clipped fixtures.
            scale = min(1, 0.95 / max(np.max(np.abs(noisy)), np.max(np.abs(ref)), 1e-9))
            sf.write(folder / "clean.wav", ref * scale, 16000, subtype="PCM_16")
            sf.write(folder / "noisy.wav", noisy * scale, 16000, subtype="PCM_16")
            rows.append(
                {
                    "id": folder.name,
                    "language": "ru",
                    "condition": condition,
                    "source": source.name,
                    "noise_source": pair.name,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
    (root / "russian-manifest.json").write_text(json.dumps(rows, indent=2))
    return rows


async def run(root: Path, binary: str) -> None:
    fixtures = [
        {**row, "language": "en", "condition": "real_noise"}
        for row in json.loads((root / "voicebank-manifest.json").read_text())
    ]
    fixtures += prepare_russian(root)
    ffmpeg = FFmpegProcessor()
    deepfilter = DeepFilterNetProvider(binary)
    profile = profile_for(Preset.NATURAL)
    results = []
    for fixture in fixtures:
        folder = root / fixture["id"]
        source = folder / "noisy.wav"
        extracted = folder / "input48.wav"
        df = folder / "deepfilter.wav"
        baseline = folder / "ffmpeg.m4a"
        combined = folder / "deepfilter_dsp.m4a"
        await ffmpeg.extract_audio(source, extracted)
        timings = {}
        start = time.monotonic()
        await ffmpeg.enhance_audio(extracted, baseline, profile)
        timings["ffmpeg"] = time.monotonic() - start
        start = time.monotonic()
        await deepfilter.enhance(extracted, profile=profile, output_path=df)
        timings["deepfilter"] = time.monotonic() - start
        start = time.monotonic()
        await ffmpeg.enhance_audio(df, combined, profile)
        timings["deepfilter_dsp"] = timings["deepfilter"] + time.monotonic() - start
        ref = read(folder / "clean.wav")
        for variant, path in [
            ("input", source),
            ("ffmpeg", baseline),
            ("deepfilter", df),
            ("deepfilter_dsp", combined),
        ]:
            decoded = folder / f"metric_{variant}.wav"
            await ffmpeg._run(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(decoded),
            )
            signal = read(decoded)
            length = min(len(ref), len(signal))
            results.append(
                {
                    "id": fixture["id"],
                    "language": fixture["language"],
                    "condition": fixture["condition"],
                    "variant": variant,
                    "stoi": float(stoi(ref[:length], signal[:length], 16000)),
                    "si_sdr_db": si_sdr(ref[:length], signal[:length]),
                    "seconds": len(ref) / 16000,
                    "duration_drift_ms": (len(signal) - len(ref)) / 16,
                    "peak": float(np.max(np.abs(signal))),
                    "compute_seconds": timings.get(variant, 0),
                    "rtf": timings.get(variant, 0) / (len(ref) / 16000),
                }
            )
            decoded.unlink()
        extracted.unlink()
        print(fixture["id"], flush=True)
        (root / "metrics.json").write_text(json.dumps(results, indent=2))
    with (root / "metrics.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/audio-comparison"))
    parser.add_argument("--deepfilter-bin", default="deep-filter")
    args = parser.parse_args()
    asyncio.run(run(args.root, args.deepfilter_bin))
