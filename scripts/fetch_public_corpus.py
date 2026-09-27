"""Fetch a reproducible noisy-speech listening corpus from a public dataset.

The dataset card declares CC0; verify upstream rights before redistributing audio.
Media is kept under the git-ignored media/ directory.
"""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

DATASET = "haydarkadioglu/speech-noise-dataset"
DATASET_PAGE = f"https://huggingface.co/datasets/{DATASET}"
ROWS_API = "https://datasets-server.huggingface.co/rows"


def fetch(output: Path, *, start: int, count: int, stride: int) -> None:
    if count < 1 or stride < 1:
        raise ValueError("count and stride must be positive")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "corpus_manifest.json"
    if manifest_path.exists():
        raise ValueError(f"Manifest already exists: {manifest_path}")
    entries = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for index in range(count):
            row_index = start + index * stride
            response = client.get(
                ROWS_API,
                params={
                    "dataset": DATASET,
                    "config": "default",
                    "split": "train",
                    "offset": row_index,
                    "length": 1,
                },
            )
            response.raise_for_status()
            record = response.json()["rows"][0]
            row = record["row"]
            if record["row_idx"] != row_index or row["label"] != "noisy_speech":
                raise ValueError(f"Row {row_index} is not a noisy speech sample")
            audio_url = row["audio"][0]["src"]
            if not audio_url.startswith("https://datasets-server.huggingface.co/cached-assets/"):
                raise ValueError(f"Unexpected audio host for row {row_index}")
            destination = output / f"public_noisy_{row_index:04d}.wav"
            if not destination.exists():
                partial = destination.with_suffix(".wav.part")
                with client.stream("GET", audio_url) as download:
                    download.raise_for_status()
                    with partial.open("wb") as target:
                        for chunk in download.iter_bytes():
                            target.write(chunk)
                partial.replace(destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            entries.append(
                {
                    "file": destination.name,
                    "dataset": DATASET,
                    "dataset_page": DATASET_PAGE,
                    "split": "train",
                    "row_index": row_index,
                    "source_filename": row["filename"],
                    "license_declared_by_dataset": "CC0-1.0",
                    "sha256": digest,
                }
            )
            print(f"{index + 1}/{count}: {destination.name}", flush=True)
    manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("media/corpus"))
    parser.add_argument("--start", type=int, default=1450)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--stride", type=int, default=45)
    args = parser.parse_args()
    fetch(args.output, start=args.start, count=args.count, stride=args.stride)


if __name__ == "__main__":
    main()
