#!/usr/bin/env python3
"""Inspect one tested release artifact and compare registry bytes."""

from __future__ import annotations

import email.parser
import hashlib
import hmac
import json
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import NamedTuple

VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
MAX_METADATA_BYTES = 1024 * 1024


class VerificationError(ValueError):
    pass


class ReleasePackages(NamedTuple):
    version: str
    tag: str
    extension_version: str
    wheel: Path
    extension_api: Path
    frontend_host: Path


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(matches) != 1 or archive.getinfo(matches[0]).file_size > MAX_METADATA_BYTES:
                raise VerificationError("The wheel metadata is malformed.")
            value = archive.read(matches[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, KeyError) as exc:
        raise VerificationError("The wheel metadata is malformed.") from exc
    metadata = email.parser.Parser().parsestr(value)
    return str(metadata.get("Name") or ""), str(metadata.get("Version") or "")


def _npm_metadata(path: Path) -> tuple[str, str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            matches = [
                member for member in archive.getmembers() if member.name == "package/package.json"
            ]
            if len(matches) != 1 or matches[0].size > MAX_METADATA_BYTES or not matches[0].isfile():
                raise VerificationError("The npm package metadata is malformed.")
            stream = archive.extractfile(matches[0])
            if stream is None:
                raise VerificationError("The npm package metadata is malformed.")
            value = json.load(stream)
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("The npm package metadata is malformed.") from exc
    if not isinstance(value, dict):
        raise VerificationError("The npm package metadata is malformed.")
    return str(value.get("name") or ""), str(value.get("version") or "")


def inspect_artifact(directory: Path) -> ReleasePackages:
    files = sorted(
        path for path in directory.iterdir() if path.is_file() and not path.name.startswith(".")
    )
    if len(files) != 3:
        raise VerificationError("The release artifact must contain exactly three package files.")
    wheels = [path for path in files if path.suffix == ".whl"]
    tarballs = [path for path in files if path.name.endswith(".tgz")]
    if len(wheels) != 1 or len(tarballs) != 2:
        raise VerificationError("The release artifact has an unexpected package shape.")

    wheel_name, version = _wheel_metadata(wheels[0])
    if wheel_name != "skein-agents" or not VERSION_RE.fullmatch(version):
        raise VerificationError("The release artifact has an invalid wheel identity.")

    npm = {_npm_metadata(path): path for path in tarballs}
    api_matches = [
        (package_version, path)
        for (name, package_version), path in npm.items()
        if name == "@miloctl/skein-extension-api"
    ]
    host_matches = [
        (package_version, path)
        for (name, package_version), path in npm.items()
        if name == "@miloctl/skein-frontend-host"
    ]
    if len(api_matches) != 1 or len(host_matches) != 1:
        raise VerificationError("The release artifact has an invalid npm package identity.")
    extension_version, extension_api = api_matches[0]
    host_version, frontend_host = host_matches[0]
    if host_version != version or not VERSION_RE.fullmatch(extension_version):
        raise VerificationError("The wheel and frontend host versions do not agree.")
    return ReleasePackages(
        version,
        f"v{version}",
        extension_version,
        wheels[0],
        extension_api,
        frontend_host,
    )


def _digest(path: Path, algorithm: str) -> bytes:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).digest()


def compare_registry_bytes(release: ReleasePackages, registry: Path) -> None:
    for source, algorithm in (
        (release.wheel, "sha256"),
        (release.extension_api, "sha512"),
        (release.frontend_host, "sha512"),
    ):
        pulled = registry / source.name
        if not pulled.is_file() or not hmac.compare_digest(
            _digest(source, algorithm), _digest(pulled, algorithm)
        ):
            raise VerificationError(f"Registry bytes do not match {source.name}.")


def _write_outputs(release: ReleasePackages) -> None:
    values = {
        "version": release.version,
        "tag": release.tag,
        "extension_version": release.extension_version,
        "wheel_filename": release.wheel.name,
        "extension_filename": release.extension_api.name,
        "host_filename": release.frontend_host.name,
    }
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    else:
        print(json.dumps(values, sort_keys=True))


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "inspect":
            _write_outputs(inspect_artifact(Path(sys.argv[2])))
            return 0
        if len(sys.argv) == 4 and sys.argv[1] == "compare":
            release = inspect_artifact(Path(sys.argv[2]))
            compare_registry_bytes(release, Path(sys.argv[3]))
            return 0
        raise VerificationError(
            "Usage: verify_release_packages.py inspect DIR | compare ARTIFACT REGISTRY"
        )
    except (OSError, VerificationError) as exc:
        print(f"verify-release-packages: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
