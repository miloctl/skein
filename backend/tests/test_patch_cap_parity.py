"""A PATCH may never accept a longer string than the POST that created the row.

Discovered by convention rather than enumerated, so a new *Patch model is
covered the day it is written. The one-test-per-model version this replaces
walked straight past two live defects: EngagementPatch.outcome was capped at
4000 against a create cap of 2000, and kill_criteria at 2000 against 500.
"""

import importlib
import inspect

import pytest
from annotated_types import MaxLen
from pydantic import BaseModel

ROUTE_MODULES = ("api", "chat", "private", "slack", "webhooks")


def _models() -> dict[str, type[BaseModel]]:
    out: dict[str, type[BaseModel]] = {}
    for short in ROUTE_MODULES:
        mod = importlib.import_module(f"app.routes.{short}")
        for name, obj in vars(mod).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == mod.__name__
            ):
                out.setdefault(name, obj)
    return out


def _string_caps(model: type[BaseModel]) -> dict[str, int | None]:
    caps: dict[str, int | None] = {}
    for fname, field in model.model_fields.items():
        if "str" not in str(field.annotation):
            continue
        caps[fname] = next((m.max_length for m in field.metadata if isinstance(m, MaxLen)), None)
    return caps


def _pairs():
    models = _models()
    for name, model in sorted(models.items()):
        for suffix in ("Patch", "EditIn"):
            if name.endswith(suffix) and len(name) > len(suffix):
                create = models.get(name[: -len(suffix)] + "In")
                if create is not None:
                    yield name, model, create


def test_the_pairing_convention_still_finds_the_models():
    """Guards the guard: if a rename breaks the *Patch/*In convention, this
    file must fail loudly instead of quietly testing nothing."""
    names = sorted(p[0] for p in _pairs())
    assert len(names) >= 8, names


@pytest.mark.parametrize("name,patch,create", list(_pairs()), ids=[p[0] for p in _pairs()])
def test_a_patch_never_loosens_a_create_cap(name, patch, create):
    create_caps = _string_caps(create)
    offenders = []
    for field, cap in _string_caps(patch).items():
        want = create_caps.get(field)
        if want is None:  # not a shared field, or the create is uncapped too
            continue
        if cap is None or cap > want:
            offenders.append(f"{name}.{field} max_length={cap} > {create.__name__}.{field}={want}")
    assert not offenders, "; ".join(offenders)
