"""Isolated optional GTCRN inference process. Requires the [gtcrn] extra."""

import argparse
import hashlib
from pathlib import Path

MODEL_SHA256 = "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534"


def enhance(source: Path, target: Path, model: Path) -> None:
    import numpy as np
    import sherpa_onnx
    import soundfile as sf

    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("GTCRN model checksum mismatch")
    samples, sr = sf.read(source, dtype="float32", always_2d=True)
    # GTCRN is a mono, 16 kHz model. This is deliberate and documented.
    mono = np.ascontiguousarray(samples.mean(axis=1))
    config = sherpa_onnx.OfflineSpeechDenoiserConfig(
        model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
            gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(model)),
            num_threads=1,
            provider="cpu",
            debug=False,
        )
    )
    if not config.validate():
        raise ValueError("Invalid GTCRN configuration")
    result = sherpa_onnx.OfflineSpeechDenoiser(config)(mono, sr)
    # File extension is provider-independent (.audio) in the common pipeline.
    sf.write(target, result.samples, result.sample_rate, format="WAV", subtype="PCM_16")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    enhance(args.source, args.target, args.model)
