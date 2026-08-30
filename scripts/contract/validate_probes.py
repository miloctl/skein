"""Validate pod, image, and probe contracts in one Kubernetes manifest."""

import re
import sys
from pathlib import Path

import yaml


def _deployment(documents: list[dict], name: str) -> dict:
    matches = [
        item
        for item in documents
        if item.get("kind") == "Deployment" and item.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one Deployment named {name}, found {len(matches)}.")
    return matches[0]


def _container(deployment: dict, name: str) -> dict:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    matches = [item for item in containers if item.get("name") == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected one container named {name}, found {len(matches)}.")
    return matches[0]


def _http_get(container: dict, probe: str) -> dict | None:
    value = container.get(probe)
    if value is None:
        return None
    return value.get("httpGet")


def _pod_spec(document: dict) -> dict | None:
    kind = document.get("kind")
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
        return document.get("spec", {}).get("template", {}).get("spec", {})
    if kind == "CronJob":
        return (
            document.get("spec", {})
            .get("jobTemplate", {})
            .get("spec", {})
            .get("template", {})
            .get("spec", {})
        )
    if kind == "Pod":
        return document.get("spec", {})
    return None


def main() -> None:
    if len(sys.argv) not in {6, 7} or (len(sys.argv) == 7 and sys.argv[6] != "--require-digests"):
        raise SystemExit(
            "usage: validate_probes.py RENDERED BACKEND_DEPLOYMENT BACKEND_CONTAINER"
            " FRONTEND_DEPLOYMENT FRONTEND_CONTAINER [--require-digests]"
        )
    path = Path(sys.argv[1])
    documents = [item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)]
    backend_deployment = _deployment(documents, sys.argv[2])
    frontend_deployment = _deployment(documents, sys.argv[4])
    backend = _container(backend_deployment, sys.argv[3])
    frontend = _container(frontend_deployment, sys.argv[5])
    expected = {
        "backend startup": (
            _http_get(backend, "startupProbe"),
            {"path": "/health", "port": "http"},
        ),
        "backend readiness": (
            _http_get(backend, "readinessProbe"),
            {"path": "/ready", "port": "http"},
        ),
        "backend liveness": (
            _http_get(backend, "livenessProbe"),
            {"path": "/health", "port": "http"},
        ),
        "frontend startup": (_http_get(frontend, "startupProbe"), None),
        "frontend readiness": (
            _http_get(frontend, "readinessProbe"),
            {"path": "/", "port": "http"},
        ),
        "frontend liveness": (
            _http_get(frontend, "livenessProbe"),
            {"path": "/", "port": "http"},
        ),
        "backend replicas": (backend_deployment["spec"].get("replicas"), 1),
        "backend strategy": (
            backend_deployment["spec"].get("strategy", {}).get("type"),
            "Recreate",
        ),
    }
    wrong = [
        f"{label}: {actual!r}, expected {wanted!r}"
        for label, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    require_digests = len(sys.argv) == 7
    workload_count = 0
    for document in documents:
        pod_spec = _pod_spec(document)
        if pod_spec is None:
            continue
        workload_count += 1
        label = f"{document.get('kind')}/{document.get('metadata', {}).get('name')}"
        if pod_spec.get("automountServiceAccountToken") is not False:
            wrong.append(f"{label} projects the default service-account token")
        pod_security = pod_spec.get("securityContext", {})
        if pod_security.get("runAsNonRoot") is not True:
            wrong.append(f"{label} does not require a non-root user")
        if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
            wrong.append(f"{label} does not use the RuntimeDefault seccomp profile")
        for container in pod_spec.get("initContainers", []) + pod_spec.get("containers", []):
            container_label = f"{label}/{container.get('name')}"
            is_application = document.get("kind") == "Deployment" and (
                (
                    document.get("metadata", {}).get("name") == sys.argv[2]
                    and container.get("name") == sys.argv[3]
                )
                or (
                    document.get("metadata", {}).get("name") == sys.argv[4]
                    and container.get("name") == sys.argv[5]
                )
            )
            if is_application:
                security = container.get("securityContext", {})
                if security.get("allowPrivilegeEscalation") is not False:
                    wrong.append(f"{container_label} permits privilege escalation")
                if security.get("readOnlyRootFilesystem") is not True:
                    wrong.append(f"{container_label} has a writable root filesystem")
                if "ALL" not in security.get("capabilities", {}).get("drop", []):
                    wrong.append(f"{container_label} does not drop all capabilities")
            if require_digests:
                image = container.get("image", "")
                if not re.search(r"@sha256:[0-9a-f]{64}$", image):
                    wrong.append(f"{container_label} uses mutable image {image!r}")
    if workload_count == 0:
        wrong.append("the manifest has no workload")
    if wrong:
        raise AssertionError("Deployment contract failed. " + ". ".join(wrong))


if __name__ == "__main__":
    main()
