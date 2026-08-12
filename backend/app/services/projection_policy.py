"""Reusable per-row policy for composite and search-style read projections."""

from __future__ import annotations

from dataclasses import dataclass

from ..extensions.policy import PolicyEngine, PolicySubject, permits_resource
from . import policy_context, scope

PROJECT_RESOURCE_TYPES = (
    "engagement",
    "milestone",
    "task",
    "blocker",
    "event",
    "promise",
    "memory",
    "lesson",
    "intake",
    "allocation",
    "artifact",
)


@dataclass(frozen=True)
class ProjectionPolicy:
    engine: PolicyEngine
    subject: PolicySubject
    action: str
    origin: str
    viewer: scope.Viewer = scope.NOBODY
    agent: str = ""
    tool: str = ""

    def permits(
        self,
        entity: str,
        entity_id: int,
        attributes: dict[str, str],
    ) -> bool:
        return permits_resource(
            self.engine,
            self.subject,
            self.action,
            entity,
            entity_id,
            attributes,
            self.origin,
            agent=self.agent,
            tool=self.tool,
        )

    def filter_rows(self, entity: str, rows: list[dict]) -> list[dict]:
        contexts = policy_context.resource_contexts(
            [(entity, int(row["id"])) for row in rows],
            self.viewer,
        )
        return [
            row
            for row in rows
            if self.permits(
                entity,
                int(row["id"]),
                contexts.get((entity, int(row["id"])), {}),
            )
        ]

    def filter_resources(self, rows: list[dict]) -> list[dict]:
        resources = [(str(row.get("entity") or ""), int(row.get("entity_id") or 0)) for row in rows]
        contexts = policy_context.resource_contexts(resources, self.viewer)
        return [
            row
            for row, (entity, entity_id) in zip(rows, resources, strict=True)
            if self.permits(entity, entity_id, contexts.get((entity, entity_id), {}))
        ]

    def allows_all_inputs(self) -> bool:
        """Fail an opaque derivative if any contributing boundary is denied."""
        if not self.allows_all_projects():
            return False
        return all(
            self.permits(resource_type, resource_id, attributes)
            for resource_type, resource_id, attributes in policy_context.opaque_resource_contexts()
        )

    def allows_all_projects(self) -> bool:
        """Fail an aggregate if one project domain or legacy link is unsafe."""
        if policy_context.has_visible_relationship_conflict(self.viewer):
            return False
        return all(
            self.permits(resource_type, resource_id, attributes)
            for resource_id, attributes in policy_context.opaque_project_contexts(self.viewer)
            for resource_type in PROJECT_RESOURCE_TYPES
        )
