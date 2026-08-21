import os
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.version import Version


def metadata(wheel: str):
    root = Path(wheel)
    with ZipFile(root) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
        return archive.read(name).decode()


base = metadata(os.environ["BASE_WHEEL"])
extension = metadata(os.environ["EXTENSION_WHEEL"])
assert "Version: 0.1.0" in base
requirement = Requirement(
    next(
        line.removeprefix("Requires-Dist: ")
        for line in extension.splitlines()
        if line.startswith("Requires-Dist: skein")
    )
)
assert Version("0.1.0") not in requirement.specifier
assert Version(os.environ["PRIOR_CORE"]) in requirement.specifier
assert Version(os.environ["NEXT_CORE"]) in requirement.specifier
