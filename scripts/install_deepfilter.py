"""Install an official, version- and checksum-pinned Linux DeepFilterNet binary."""

import hashlib
import platform
import sys
import urllib.request
from pathlib import Path

VERSION = "0.5.6"
ASSETS = {
    "x86_64": (
        "x86_64-unknown-linux-musl",
        "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da",
    ),
    "aarch64": (
        "aarch64-unknown-linux-gnu",
        "14e02a1c0028f3ca0bdf83b62b3336e56ba0556894ef295a95e8573f06557166",
    ),
}


def install(destination: Path) -> None:
    if platform.system() != "Linux" or platform.machine() not in ASSETS:
        raise RuntimeError("Installer supports Linux x86_64 and aarch64 only")
    asset, expected = ASSETS[platform.machine()]
    url = (
        f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{VERSION}/"
        f"deep-filter-{VERSION}-{asset}"
    )
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("DeepFilterNet binary checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.chmod(0o755)


if __name__ == "__main__":
    install(Path(sys.argv[1]))
