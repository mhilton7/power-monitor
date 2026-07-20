from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"


def main() -> None:
    checked = 0
    for line in (RELEASE / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = (RELEASE / name).resolve()
        if RELEASE.resolve() not in path.parents or not path.is_file():
            raise AssertionError(f"unsafe or missing release artifact: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"checksum mismatch: {name}")
        checked += 1
    print(f"Verified {checked} release artifact checksums")


if __name__ == "__main__":
    main()
