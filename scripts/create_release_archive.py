"""Create a source-frozen release archive with current verified evidence.

``git archive`` is intentionally used as the source of every application file.
Release evidence is generated after the source commit is frozen, however, so a
plain archive of that commit would contain the previous release's evidence.  This
tool replaces only the release artifacts named by the current checksum inventory
and the inventory itself while copying the Git archive member-for-member.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _evidence_names(release: Path) -> set[str]:
    inventory = release / "checksums.sha256"
    names = {"checksums.sha256"}
    for line_number, line in enumerate(
        inventory.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            raise ValueError(f"invalid checksum line {line_number}")
        name = parts[1]
        if Path(name).name != name or name.endswith((".tar.gz", ".zip")):
            raise ValueError(f"invalid pre-archive evidence entry: {name!r}")
        if name in names:
            raise ValueError(f"duplicate release evidence entry: {name}")
        if not (release / name).is_file():
            raise ValueError(f"missing release evidence file: {name}")
        names.add(name)
    if names == {"checksums.sha256"}:
        raise ValueError("release evidence inventory is empty")
    return names


def create_archive(
    *,
    root: Path,
    release: Path,
    version: str,
    release_commit: str,
    output: Path,
) -> int:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("release version must use semantic x.y.z form")
    release_commit = release_commit.lower()
    if not COMMIT_PATTERN.fullmatch(release_commit):
        raise ValueError("release commit must be a full 40-character SHA-1")
    if output.exists():
        raise FileExistsError(f"release archive already exists: {output}")

    evidence_names = _evidence_names(release)
    prefix = f"power-monitor-server-{version}/"
    replacements = {
        f"{prefix}release/{name}": (release / name).read_bytes()
        for name in evidence_names
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    source_descriptor, source_name = tempfile.mkstemp(
        prefix=".pm-release-source-", suffix=".tar", dir=output.parent
    )
    partial_descriptor, partial_name = tempfile.mkstemp(
        prefix=".pm-release-output-", suffix=".tar.gz", dir=output.parent
    )
    os.close(partial_descriptor)
    source_path = Path(source_name)
    partial_path = Path(partial_name)
    try:
        with os.fdopen(source_descriptor, "w+b") as source_stream:
            subprocess.run(  # noqa: S603 - fixed Git archive command
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "archive",
                    "--format=tar",
                    f"--prefix={prefix}",
                    release_commit,
                ],
                cwd=root,
                check=True,
                stdout=source_stream,
            )
            source_stream.flush()
            source_stream.seek(0)

            replaced: set[str] = set()
            with (
                tarfile.open(fileobj=source_stream, mode="r:") as source,
                partial_path.open("wb") as compressed_output,
                gzip.GzipFile(
                    fileobj=compressed_output, mode="wb", mtime=0
                ) as gzip_output,
                tarfile.open(
                    fileobj=gzip_output,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                    pax_headers=source.pax_headers,
                ) as destination,
            ):
                for source_member in source:
                    member = copy.copy(source_member)
                    replacement = replacements.get(member.name)
                    if replacement is not None:
                        if not member.isfile():
                            raise ValueError(
                                "release evidence is not a regular file in Git: "
                                f"{member.name}"
                            )
                        member.size = len(replacement)
                        destination.addfile(member, io.BytesIO(replacement))
                        replaced.add(member.name)
                        continue

                    file_object = (
                        source.extractfile(source_member) if member.isfile() else None
                    )
                    try:
                        destination.addfile(member, file_object)
                    finally:
                        if file_object is not None:
                            file_object.close()

        missing = sorted(set(replacements) - replaced)
        if missing:
            raise ValueError(
                "release evidence is not tracked by the frozen source commit: "
                + ", ".join(missing)
            )
        partial_path.replace(output)
    finally:
        source_path.unlink(missing_ok=True)
        partial_path.unlink(missing_ok=True)
    return len(replaced)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or RELEASE / f"power-monitor-server-{args.version}.tar.gz"
    replaced = create_archive(
        root=ROOT,
        release=RELEASE,
        version=args.version,
        release_commit=args.release_commit,
        output=output,
    )
    print(f"Created {output} with {replaced} current release evidence files")


if __name__ == "__main__":
    main()
