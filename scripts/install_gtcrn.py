"""Download the pinned official sherpa-onnx export of GTCRN (MIT upstream)."""

import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speech-enhancement-models/gtcrn_simple.onnx"
)
SHA256 = "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534"


def install(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=90) as response:
        content = response.read(2 * 1024**2 + 1)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("GTCRN download checksum mismatch")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temp = Path(output.name)
        output.write(content)
    try:
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Installed checksum-verified GTCRN: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    install(parser.parse_args().destination)
