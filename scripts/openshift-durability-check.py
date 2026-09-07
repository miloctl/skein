#!/usr/bin/env python3
"""Read-only preflight, plus an explicit disposable-namespace storage probe."""

import argparse
import json
import re
import secrets
import subprocess
import sys


class GateError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise GateError("The preflight did not pass. " + message)


def validate_deployment(deployment, pods, claims, budgets):
    spec = deployment["spec"]
    require(
        spec.get("replicas", 1) >= 2, "Select a disposable candidate with at least two replicas."
    )
    require(
        spec.get("strategy", {}).get("type") == "RollingUpdate",
        "Use RollingUpdate for the candidate.",
    )
    rolling = spec["strategy"].get("rollingUpdate", {})
    require(
        rolling.get("maxUnavailable") == 0 and rolling.get("maxSurge") == 1,
        "Set candidate maxUnavailable to 0 and maxSurge to 1.",
    )
    template = spec["template"]["spec"]
    require(
        template.get("terminationGracePeriodSeconds", 30) >= 210,
        "Keep at least 210 seconds for backend shutdown.",
    )
    backend = next(c for c in template["containers"] if c["name"] == "backend")
    require(
        backend.get("readinessProbe", {}).get("httpGet", {}).get("path") == "/ready",
        "Point readiness at /ready.",
    )
    require(
        backend.get("livenessProbe", {}).get("httpGet", {}).get("path") == "/health",
        "Point liveness at /health.",
    )
    require(
        re.search(r"@sha256:[0-9a-f]{64}$", backend["image"]), "Pin the candidate image digest."
    )
    mounts = {
        v["name"]: v.get("persistentVolumeClaim", {}).get("claimName") for v in template["volumes"]
    }
    names = [mounts.get("data"), mounts.get("backup-mirror")]
    require(all(names) and len(set(names)) == 2, "Use separate data and backup-mirror claims.")
    for name in names:
        claim = next((c for c in claims if c["metadata"]["name"] == name), {})
        require(
            claim.get("status", {}).get("phase") == "Bound"
            and "ReadWriteMany" in claim.get("spec", {}).get("accessModes", []),
            "Bind both candidate recovery claims with ReadWriteMany.",
        )
    labels = spec["template"]["metadata"]["labels"]
    require(
        any(
            b["spec"].get("minAvailable") == 1
            and b["spec"].get("selector", {}).get("matchLabels") == {"app": "skein-backend"}
            and not b["spec"]["selector"].get("matchExpressions")
            for b in budgets
        ),
        "Set a matching candidate PodDisruptionBudget with minAvailable 1.",
    )
    require(labels.get("app") == "skein-backend", "Keep the backend selector on the candidate.")
    serving = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    require(len(serving) >= 2, "Wait for at least two backend pods.")
    for pod in serving:
        require(
            any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in pod.get("status", {}).get("conditions", [])
            ),
            "Wait for every backend pod to be ready.",
        )
    require(
        len({p["spec"].get("nodeName") for p in serving}) >= 2,
        "Place backend pods on separate nodes.",
    )
    first = serving[0]
    second = next(p for p in serving if p["spec"]["nodeName"] != first["spec"]["nodeName"])
    return [first, second]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument(
        "--allow-faults",
        action="store_true",
        help="Check explicit permission before the runbook's manual faults.",
    )
    parser.add_argument(
        "--probe-storage",
        action="store_true",
        help="Write and remove unique markers on both candidate volumes.",
    )
    parser.add_argument("--confirm-namespace")
    args = parser.parse_args(argv)
    require(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.namespace),
        "Supply a valid explicit namespace.",
    )
    require(
        bool(args.context.strip()) and not args.context.startswith("-"),
        "Supply an explicit context.",
    )
    if args.allow_faults or args.probe_storage:
        require(
            args.confirm_namespace == args.namespace,
            "Repeat the namespace with --confirm-namespace before probes or faults.",
        )
    command = [
        "kubectl",
        "--context",
        args.context,
        "--namespace",
        args.namespace,
        "--request-timeout=15s",
    ]

    def run(*words, input=None):
        result = subprocess.run(  # noqa: S603 — fixed kubectl operations and explicit target
            [*command, *words],
            input=input,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        require(result.returncode == 0, "Check target access and readiness.")
        return result.stdout

    def get(kind, *words):
        return json.loads(run("get", kind, *words, "-o", "json"))

    namespace = get("namespace", args.namespace)
    # A familiar namespace name is not permission to inject faults into its workloads.
    require(
        namespace["metadata"].get("labels", {}).get("skein.dev/durability-drill") == "disposable",
        "Select a namespace labeled skein.dev/durability-drill=disposable.",
    )
    routes = get("routes.route.openshift.io")["items"]
    require(
        any(
            r.get("spec", {}).get("to", {}).get("name") == "skein-backend"
            and any(
                c.get("type") == "Admitted" and c.get("status") == "True"
                for ingress in r.get("status", {}).get("ingress", [])
                for c in ingress.get("conditions", [])
            )
            for r in routes
        ),
        "Wait for an admitted Route to the candidate backend Service.",
    )
    deployment = get("deployment", "skein-backend")
    pods = get("pods", "-l", "app=skein-backend")["items"]
    pair = validate_deployment(deployment, pods, get("pvc")["items"], get("pdb")["items"])
    for pod in pair:
        runtime = run(
            "exec",
            pod["metadata"]["name"],
            "-c",
            "backend",
            "--",
            "python",
            "-c",
            "import os,urllib.request; assert os.getuid()!=0; "
            "[urllib.request.urlopen('http://127.0.0.1:8000/'+p,timeout=4) for p in ('health','ready')]; "
            "print('uid='+str(os.getuid())+' gid='+str(os.getgid()))",
        ).strip()
        print(pod["metadata"]["name"], pod["spec"]["nodeName"], runtime)
        for container in pod.get("status", {}).get("containerStatuses", []):
            print(container["name"], container.get("imageID", ""), container.get("restartCount", 0))
    if args.probe_storage:
        marker = ".skein-durability-" + secrets.token_hex(16)
        print("Storage marker:", marker, flush=True)
        created = []
        try:
            for path in ("/data/" + marker, "/backup-mirror/" + marker):
                for index, pod in enumerate(pair):
                    # O_EXCL never overwrites an existing file. Both UIDs must append and fsync.
                    code = (
                        "import os; from pathlib import Path; "
                        f"p=Path({path!r}); "
                        + (
                            "f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o660); os.close(f); "
                            if index == 0
                            else ""
                        )
                        + f"assert p.read_bytes()=={(b'' if index == 0 else b'one')!r}; "
                        + f"f=p.open('ab'); f.write({(b'one' if index == 0 else b'two')!r}); f.flush(); os.fsync(f.fileno()); f.close()"
                    )
                    run(
                        "exec", pod["metadata"]["name"], "-c", "backend", "--", "python", "-c", code
                    )
                    if index == 0:
                        created.append(path)
                run(
                    "exec",
                    pair[0]["metadata"]["name"],
                    "-c",
                    "backend",
                    "--",
                    "python",
                    "-c",
                    f"from pathlib import Path; assert Path({path!r}).read_bytes()==b'onetwo'",
                )
        finally:
            for path in created:
                run(
                    "exec",
                    pair[0]["metadata"]["name"],
                    "-c",
                    "backend",
                    "--",
                    "python",
                    "-c",
                    f"from pathlib import Path; Path({path!r}).unlink(missing_ok=True)",
                )
        print("Cross-node marker reads, writes, and fsync passed on both volumes.")
    print(
        "Preflight passed. Route draining, faults, storage independence, and migration compatibility still need runbook proof."
    )


if __name__ == "__main__":
    try:
        main()
    except (
        GateError,
        KeyError,
        StopIteration,
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(
            str(exc)
            if isinstance(exc, GateError)
            else "The preflight could not complete. Check the candidate and cluster access.",
            file=sys.stderr,
        )
        sys.exit(1)
