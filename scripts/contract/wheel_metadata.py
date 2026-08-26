import os
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


def metadata(wheel: str) -> str:
    with ZipFile(Path(wheel)) as archive:
        name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
        return archive.read(name).decode()


def requirement(meta: str, project: str) -> Requirement:
    expected = canonicalize_name(project)
    matches = [
        Requirement(line.removeprefix("Requires-Dist: "))
        for line in meta.splitlines()
        if line.startswith("Requires-Dist: ")
        and canonicalize_name(Requirement(line.removeprefix("Requires-Dist: ")).name) == expected
    ]
    assert len(matches) == 1, (project, matches)
    return matches[0]


prior_core = metadata(os.environ["PRIOR_CORE_WHEEL"])
next_core = metadata(os.environ["NEXT_CORE_WHEEL"])
prior_extension = metadata(os.environ["PRIOR_EXTENSION_WHEEL"])
next_extension = metadata(os.environ["NEXT_EXTENSION_WHEEL"])

assert "Name: skein\n" in prior_core
assert "Name: skein-agents\n" in next_core
assert "Version: 1.0.0" in prior_extension
assert "Version: 2.0.0" in next_extension

prior_requirement = requirement(prior_extension, "skein")
next_requirement = requirement(next_extension, "skein-agents")
assert Version(os.environ["PRIOR_CORE"]) in prior_requirement.specifier
assert Version(os.environ["PRIOR_CORE"]) not in next_requirement.specifier
assert Version(os.environ["NEXT_CORE"]) in next_requirement.specifier
assert not any(
    canonicalize_name(Requirement(line.removeprefix("Requires-Dist: ")).name) == "skein"
    for line in next_extension.splitlines()
    if line.startswith("Requires-Dist: ")
)
