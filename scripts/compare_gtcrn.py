"""Optional CPU comparator: pip install sherpa-onnx==1.13.8 and .[benchmark].

Download gtcrn_simple.onnx from the official sherpa-onnx speech-enhancement-models
release. This is an offline experiment, not a selected production provider.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf
from compare_audio import read, si_sdr
from pystoi import stoi

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor

MODEL_SHA256 = "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534"


def run(root: Path, model: Path) -> None:
    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("Unexpected GTCRN model checksum")
    started = time.monotonic()
    config = sherpa_onnx.OfflineSpeechDenoiserConfig(
        model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
            gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(model)),
            num_threads=1,
            provider="cpu",
            debug=False,
        )
    )
    denoiser = sherpa_onnx.OfflineSpeechDenoiser(config)
    startup = time.monotonic() - started
    fixtures = [
        {**r, "language": "en", "condition": "real_noise"}
        for r in json.loads((root / "voicebank-manifest.json").read_text())
    ]
    fixtures += json.loads((root / "russian-manifest.json").read_text())
    results = []
    ffmpeg = FFmpegProcessor()
    profile = profile_for(Preset.NATURAL)
    for fixture in fixtures:
        folder = root / fixture["id"]
        noisy = np.ascontiguousarray(read(folder / "noisy.wav"), dtype=np.float32)
        ref = read(folder / "clean.wav")
        started = time.monotonic()
        enhanced = denoiser(noisy, 16000)
        elapsed = time.monotonic() - started
        output = np.array(enhanced.samples)
        sf.write(folder / "gtcrn.wav", output, enhanced.sample_rate, subtype="PCM_16")
        size = min(len(output), len(ref))
        results.append(
            {
                "id": fixture["id"],
                "language": fixture["language"],
                "condition": fixture["condition"],
                "variant": "gtcrn",
                "stoi": float(stoi(ref[:size], output[:size], 16000)),
                "si_sdr_db": si_sdr(ref[:size], output[:size]),
                "seconds": len(ref) / 16000,
                "compute_seconds": elapsed,
                "rtf": elapsed / (len(ref) / 16000),
                "startup_seconds": startup,
                "duration_drift_ms": (len(output) - len(ref)) / 16,
                "peak": float(np.max(np.abs(output))),
            }
        )
        # Also evaluate the actual bot's post-processing, not just a raw model.
        dsp_path = folder / "gtcrn_dsp.m4a"
        started = time.monotonic()
        asyncio.run(ffmpeg.enhance_audio(folder / "gtcrn.wav", dsp_path, profile))
        elapsed_dsp = elapsed + time.monotonic() - started
        decoded = folder / "metric_gtcrn.wav"
        asyncio.run(
            ffmpeg._run(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(dsp_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(decoded),
            )
        )
        signal = read(decoded)
        size = min(len(signal), len(ref))
        results.append(
            {
                **results[-1],
                "variant": "gtcrn_dsp",
                "stoi": float(stoi(ref[:size], signal[:size], 16000)),
                "si_sdr_db": si_sdr(ref[:size], signal[:size]),
                "compute_seconds": elapsed_dsp,
                "rtf": elapsed_dsp / (len(ref) / 16000),
                "duration_drift_ms": (len(signal) - len(ref)) / 16,
                "peak": float(np.max(np.abs(signal))),
            }
        )
        decoded.unlink()
        print(fixture["id"], flush=True)
    (root / "metrics-gtcrn.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/audio-comparison"))
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.model)
