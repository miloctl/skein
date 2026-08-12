"""Installed contract check for a private workplace package."""

from typing import assert_type

from app.extensions import AppSettings, SkeinModule
from app.public import CreateTaskCommand, WorkItems

assert_type(AppSettings, type[AppSettings])
assert_type(SkeinModule, type[SkeinModule])
assert_type(CreateTaskCommand, type[CreateTaskCommand])
assert_type(WorkItems, type[WorkItems])
