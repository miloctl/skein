#!/usr/bin/env python
"""Validate the rendered backend and frontend workload contract."""

import re
import sys
from pathlib import Path

import yaml


def deployment(documents: list[dict], name: str) -> dict:
    matches = [
        item
        for item in documents
        if item.get("kind") == "Deployment" and item.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one Deployment named {name}, found {len(matches)}.")
    return matches[0]


def container(workload: dict, name: str) -> dict:
    matches = [
        item
        for item in workload["spec"]["template"]["spec"]["containers"]
        if item.get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one container named {name}, found {len(matches)}.")
    return matches[0]


def http_get(item: dict, probe: str) -> dict | None:
    value = item.get(probe)
    return None if value is None else value.get("httpGet")


def pod_spec(document: dict) -> dict | None:
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
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: validate-deployment.py RENDERED BACKEND_DEPLOYMENT BACKEND_CONTAINER"
            " FRONTEND_DEPLOYMENT FRONTEND_CONTAINER"
        )
    documents = [
        item for item in yaml.safe_load_all(Path(sys.argv[1]).read_text()) if isinstance(item, dict)
    ]
    backend_workload = deployment(documents, sys.argv[2])
    frontend_workload = deployment(documents, sys.argv[4])
    backend = container(backend_workload, sys.argv[3])
    frontend = container(frontend_workload, sys.argv[5])
    expected = {
        "backend startup": (http_get(backend, "startupProbe"), {"path": "/health", "port": "http"}),
        "backend readiness": (
            http_get(backend, "readinessProbe"),
            {"path": "/ready", "port": "http"},
        ),
        "backend liveness": (
            http_get(backend, "livenessProbe"),
            {"path": "/health", "port": "http"},
        ),
        "frontend startup": (http_get(frontend, "startupProbe"), None),
        "frontend readiness": (http_get(frontend, "readinessProbe"), {"path": "/", "port": "http"}),
        "frontend liveness": (http_get(frontend, "livenessProbe"), {"path": "/", "port": "http"}),
        "backend replicas": (backend_workload["spec"].get("replicas"), 1),
        "backend strategy": (backend_workload["spec"].get("strategy", {}).get("type"), "Recreate"),
    }
    wrong = [
        f"{label}: {actual!r}, expected {wanted!r}"
        for label, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    named = {
        (backend_workload["metadata"]["name"], backend["name"]),
        (frontend_workload["metadata"]["name"], frontend["name"]),
    }
    workload_count = 0
    for document in documents:
        pod = pod_spec(document)
        if pod is None:
            continue
        workload_count += 1
        workload_name = document.get("metadata", {}).get("name")
        label = f"{document.get('kind')}/{workload_name}"
        if pod.get("automountServiceAccountToken") is not False:
            wrong.append(f"{label} projects the default service-account token")
        pod_security = pod.get("securityContext", {})
        if pod_security.get("runAsNonRoot") is not True:
            wrong.append(f"{label} does not require a non-root user")
        if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
            wrong.append(f"{label} does not use the RuntimeDefault seccomp profile")
        for item in pod.get("initContainers", []) + pod.get("containers", []):
            item_label = f"{label}/{item.get('name')}"
            if not re.search(r"@sha256:[0-9a-f]{64}$", item.get("image", "")):
                wrong.append(f"{item_label} uses a mutable image")
            if (workload_name, item.get("name")) in named:
                security = item.get("securityContext", {})
                if security.get("allowPrivilegeEscalation") is not False:
                    wrong.append(f"{item_label} permits privilege escalation")
                if security.get("readOnlyRootFilesystem") is not True:
                    wrong.append(f"{item_label} has a writable root filesystem")
                if "ALL" not in security.get("capabilities", {}).get("drop", []):
                    wrong.append(f"{item_label} does not drop all capabilities")
    if workload_count == 0:
        wrong.append("the manifest has no workload")
    if wrong:
        raise AssertionError("Deployment contract failed. " + ". ".join(wrong))


if __name__ == "__main__":
    main()
