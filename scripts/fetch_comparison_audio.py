"""Fetch 24 paired VoiceBank-DEMAND clips and 6 Russian FLEURS clips (CC BY 4.0).

Selection is deterministic, spread across the English test set. Russian selection
uses the first six WAV members of the official archive; it is not representative.
Only the beginning of the large tar.gz is streamed; no full corpus download.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def get(url):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(url, timeout=90)
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise
            time.sleep(2)


def fetch_pair(root: Path, index: int) -> dict:
    url = (
        "https://datasets-server.huggingface.co/rows?"
        "dataset=JacobLinCool%2FVoiceBank-DEMAND-16k&config=default&split=test&"
        f"offset={index}&length=1"
    )
    with get(url) as response:
        row = json.load(response)["rows"][0]["row"]
    name = row["id"]
    if not name.replace("_", "").isalnum():
        raise ValueError("Unexpected sample name")
    folder = root / name
    folder.mkdir(exist_ok=True)
    for key in ("clean", "noisy"):
        dest = folder / f"{key}.wav"
        if not dest.exists():
            with get(row[key][0]["src"]) as response:
                content = response.read(10 * 1024**2 + 1)
            if len(content) > 10 * 1024**2:
                raise ValueError("Unexpected sample size")
            dest.write_bytes(content)
    print(name, flush=True)
    return {
        "id": name,
        "row": index,
        "dataset": "JacobLinCool/VoiceBank-DEMAND-16k",
        "split": "test",
        "license": "CC-BY-4.0",
        "files": {
            key: hashlib.sha256((folder / f"{key}.wav").read_bytes()).hexdigest()
            for key in ("clean", "noisy")
        },
    }


def fetch(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                lambda index: fetch_pair(root, index), [round(i * 823 / 23) for i in range(24)]
            )
        )
    (root / "voicebank-manifest.json").write_text(json.dumps(rows, indent=2))
    base = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/ru_ru"
    folder = root / "fleurs-ru"
    folder.mkdir(exist_ok=True)
    if len(list(folder.glob("*.wav"))) < 6:
        count = 0
        with (
            get(base + "/audio/test.tar.gz") as response,
            tarfile.open(fileobj=response, mode="r|gz") as archive,
        ):
            for member in archive:
                if member.isfile() and member.name.endswith(".wav"):
                    if member.size > 10 * 1024**2:
                        raise ValueError("Unexpected FLEURS sample size")
                    dest = folder / Path(member.name).name
                    dest.write_bytes(archive.extractfile(member).read())
                    print(dest.name, flush=True)
                    count += 1
                    if count >= 6:
                        break
    with get(base + "/test.tsv") as response:
        metadata = response.read().decode()
    selected = {p.name for p in folder.glob("*.wav")}
    rows = [
        {
            "id": row[0],
            "filename": row[1],
            "text": row[2],
            "gender": row[-1],
            "sha256": hashlib.sha256((folder / row[1]).read_bytes()).hexdigest(),
        }
        for row in csv.reader(metadata.splitlines(), delimiter="\t")
        if row[1] in selected
    ]
    (root / "fleurs-manifest.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/audio-comparison"))
    fetch(parser.parse_args().root)
