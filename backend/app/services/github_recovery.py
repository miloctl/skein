"""Bounded GitHub delivery-history scans and requests for signed redelivery.

GitHub keeps redelivery available for three days and does not retry failures.
GET lists attempts, not events: every attempt with the same guid must be
checked before POST /deliveries/{id}/attempts (202 means accepted, not applied).
https://docs.github.com/en/rest/repos/webhooks
https://docs.github.com/en/webhooks/using-webhooks/automatically-redelivering-failed-deliveries-for-a-repository-webhook
"""

import json
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from math import ceil
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import psycopg

from .. import config, db
from ..extensions.policy import PolicyEngine, PolicyInput, PolicyResource, PolicySubject
from . import forge, mcp_servers

MAX_SECONDS = 20
MAX_PAGES = 5
MAX_POSTS = 20
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_BYTES = 8 * 1024 * 1024
HISTORY_DAYS = 3
RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 6 * 3600
_LOCK = 4_216_031


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat(timespec="microseconds")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(UTC)


class _RemoteFault(Exception):
    def __init__(self, code: str, delay: int = 0):
        super().__init__(code)
        self.code, self.delay = code, delay


@dataclass
class _Budget:
    deadline: float
    pages: int = 0
    posts: int = 0
    requested: int = 0
    size: int = 0

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.size >= MAX_BYTES:
            raise _RemoteFault("WORK_BOUND")
        return min(remaining, 5.0)


def _retry_delay(headers: httpx.Headers) -> int:
    value = headers.get("retry-after", "")
    try:
        seconds = (
            int(value)
            if value.isdecimal()
            else ceil((parsedate_to_datetime(value) - _now()).total_seconds())
        )
    except (ValueError, TypeError, OverflowError):
        seconds = 0
    try:
        reset = int(headers.get("x-ratelimit-reset", "0")) - int(_now().timestamp())
    except ValueError:
        reset = 0
    # Both server deadlines are lower bounds. Neither can be shortened to
    # our exponential-backoff ceiling, or by choosing the other header.
    return max(RETRY_SECONDS, seconds, reset)


def _api_namespace() -> str:
    return sha256(config.GITHUB_API_URL.encode()).hexdigest()


def _api_retry_at() -> str:
    row = db.query_one(
        "SELECT retry_at FROM github_recovery_api WHERE namespace = ?", (_api_namespace(),)
    )
    return row["retry_at"] if row else ""


def _check_api_throttle() -> None:
    retry_at = _api_retry_at()
    if retry_at and _timestamp(retry_at) > _now():
        raise _RemoteFault("RATE_LIMITED", ceil((_timestamp(retry_at) - _now()).total_seconds()))


def _throttle_api(delay: int) -> None:
    namespace = _api_namespace()
    with db.transaction():
        db.name_lock(_LOCK, "api:" + namespace)
        db.execute(
            "INSERT INTO github_recovery_api (namespace, retry_at, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (namespace) DO UPDATE SET retry_at = GREATEST(github_recovery_api.retry_at, EXCLUDED.retry_at), updated_at = EXCLUDED.updated_at",
            (namespace, _stamp(_now() + timedelta(seconds=delay)), _stamp()),
        )


def _request(
    client: httpx.Client, method: str, path: str, budget: _Budget
) -> tuple[object, httpx.Headers]:
    # The API credential is shared by every hook. A restart or a later hook
    # must honor a throttle even when that hook has never been visited.
    _check_api_throttle()
    url = config.GITHUB_API_URL + path
    try:
        mcp_servers.check_url(url)
    except ValueError:
        raise _RemoteFault("DESTINATION_REFUSED") from None
    try:
        with client.stream(method, url, timeout=budget.remaining()) as response:
            if response.status_code == 429 or (
                response.status_code == 403
                and (
                    response.headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response.headers
                )
            ):
                delay = _retry_delay(response.headers)
                _throttle_api(delay)
                raise _RemoteFault("RATE_LIMITED", delay)
            if response.headers.get("x-ratelimit-remaining") == "0":
                _throttle_api(_retry_delay(response.headers))
            if response.status_code >= 500:
                raise _RemoteFault("UPSTREAM_UNAVAILABLE")
            if response.status_code != (200 if method == "GET" else 202):
                code = (
                    "HISTORY_UNAVAILABLE"
                    if response.status_code == 404
                    else "CURSOR_INVALID"
                    if response.status_code == 422 and "cursor=" in path
                    else "UPSTREAM_REFUSED"
                )
                raise _RemoteFault(code)
            if response.headers.get("content-encoding", "identity") != "identity":
                raise _RemoteFault("INVALID_RESPONSE")
            data = bytearray()
            # A fixed decoded chunk size buffers slow drips before yielding,
            # so the wall-clock check would not run until that buffer filled.
            for chunk in response.iter_raw():
                budget.remaining()
                budget.size += len(chunk)
                if len(data) + len(chunk) > MAX_PAGE_BYTES or budget.size > MAX_BYTES:
                    raise _RemoteFault("RESPONSE_TOO_LARGE")
                data.extend(chunk)
            if method == "POST":
                return None, response.headers
            try:
                return json.loads(
                    data,
                    object_pairs_hook=config._json_object,
                    parse_constant=config._json_constant,
                ), response.headers
            except (ValueError, RecursionError):
                raise _RemoteFault("INVALID_RESPONSE") from None
    except httpx.HTTPError:
        # Exception strings carry the URL and can carry authentication data.
        # An interrupted POST stays unknown, even if GitHub applied it.
        raise _RemoteFault("NETWORK_ERROR") from None


def _path(hook: dict) -> str:
    return f"/repos/{hook['repository']}/hooks/{hook['hook_id']}/deliveries"


def _next_cursor(headers: httpx.Headers, path: str) -> str:
    link = headers.get("link", "")
    if len(link) > 8192:
        raise _RemoteFault("INVALID_PAGINATION")
    matches = re.findall(r'<([^<>]+)>;\s*rel="next"', link)
    if not matches:
        if 'rel="next"' in link:
            raise _RemoteFault("INVALID_PAGINATION")
        return ""
    if len(matches) != 1:
        raise _RemoteFault("INVALID_PAGINATION")
    target, expected = urlsplit(matches[0]), urlsplit(config.GITHUB_API_URL + path)
    query = parse_qs(target.query, keep_blank_values=True)
    if (
        target.scheme != expected.scheme
        or target.netloc != expected.netloc
        or target.path != expected.path
        or target.fragment
        or set(query) - {"cursor", "per_page"}
        or len(query.get("cursor", [])) != 1
        or query.get("per_page", ["100"]) != ["100"]
    ):
        raise _RemoteFault("INVALID_PAGINATION")
    cursor = query["cursor"][0]
    if not cursor or len(cursor) > 1024 or not all(33 <= ord(c) <= 126 for c in cursor):
        raise _RemoteFault("INVALID_PAGINATION")
    return cursor


def _deliveries(data: object) -> list[dict]:
    if not isinstance(data, list) or len(data) > 100:
        raise _RemoteFault("INVALID_RESPONSE")
    rows = []
    try:
        for item in data:
            if (
                not isinstance(item, dict)
                or type(item.get("id")) is not int
                or not 0 < item["id"] < 2**63
                or not isinstance(item.get("guid"), str)
                or not isinstance(item.get("event"), str)
                or len(item["event"]) > 100
                or not isinstance(item.get("delivered_at"), str)
                or type(item.get("status_code")) is not int
                or not 0 <= item["status_code"] <= 599
                or type(item.get("redelivery")) is not bool
            ):
                raise ValueError
            rows.append(
                {
                    "id": item["id"],
                    "guid": forge.github_guid(item["guid"]),
                    "event": item["event"],
                    "delivered_at": _stamp(_timestamp(item["delivered_at"])),
                    "successful": 200 <= item["status_code"] < 400,
                }
            )
    except (ValueError, OverflowError, TypeError):
        raise _RemoteFault("INVALID_RESPONSE") from None
    return rows


def _gap(namespace: str, since: str, until: str) -> None:
    row = db.query_one(
        "SELECT gap_since, gap_until, reconciled_at FROM github_recovery_hooks WHERE namespace = ?",
        (namespace,),
    )
    if row is None:
        raise RuntimeError("GitHub recovery state is missing.")
    if row["gap_since"] and not row["reconciled_at"]:
        since = min(since, row["gap_since"])
        until = max(until, row["gap_until"])
    if row["gap_since"] == since and row["gap_until"] == until and not row["reconciled_at"]:
        return
    db.execute(
        "UPDATE github_recovery_hooks SET gap_since = ?, gap_until = ?, reconciled_at = '', reconciled_by = '', reconciliation_note = '', updated_at = ? WHERE namespace = ?",
        (since, until, _stamp(), namespace),
    )
    db.log_activity(
        "scheduler",
        "github_history_gap",
        "GitHub delivery history is incomplete. Compare current repository state with tasks, then record reconciliation.",
    )


def _start(hook: dict) -> dict:
    namespace = forge.github_namespace(hook["repository"], hook["hook_id"])
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        db.execute(
            "INSERT INTO github_recovery_hooks (namespace, repository, hook_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (namespace, hook["repository"], hook["hook_id"], _stamp(), _stamp()),
        )
        row = db.query_one("SELECT * FROM github_recovery_hooks WHERE namespace = ?", (namespace,))
        if row is None:
            raise RuntimeError("GitHub recovery state is missing.")
        if row["retry_at"] and _timestamp(row["retry_at"]) > _now():
            return row
        cutoff = _stamp(_now() - timedelta(days=HISTORY_DAYS))
        if row["last_complete_at"] and row["last_complete_at"] < cutoff:
            _gap(namespace, row["last_complete_at"], cutoff)
        if row["drain_scan"] and row["drain_scan"] < cutoff:
            db.execute(
                "UPDATE github_recovery_hooks SET drain_scan = '' WHERE namespace = ?", (namespace,)
            )
            row["drain_scan"] = ""
        if row["drain_scan"]:
            return row
        # A scan that itself outlives retention cannot prove its missing pages.
        # Restart at the head and preserve the lost interval as a gap.
        if row["scan_started_at"] and row["scan_started_at"] < cutoff:
            _gap(namespace, row["scan_started_at"], cutoff)
            db.execute(
                "UPDATE github_recovery_hooks SET cursor = '', scan_started_at = '' WHERE namespace = ?",
                (namespace,),
            )
        db.execute(
            "UPDATE github_recovery_hooks SET scan_started_at = CASE WHEN scan_started_at = '' THEN ? ELSE scan_started_at END, updated_at = ? WHERE namespace = ?",
            (_stamp(), _stamp(), namespace),
        )
        return (
            db.query_one("SELECT * FROM github_recovery_hooks WHERE namespace = ?", (namespace,))
            or row
        )


def _save_page(namespace: str, scan: str, rows: list[dict], cursor: str) -> None:
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        for item in rows:
            db.execute(
                "INSERT INTO github_recovery_deliveries (namespace, guid, delivery_id, event, delivered_at, successful, state, seen_scan, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (namespace, guid) DO UPDATE SET"
                " delivery_id = CASE WHEN EXCLUDED.delivered_at > github_recovery_deliveries.delivered_at THEN EXCLUDED.delivery_id ELSE github_recovery_deliveries.delivery_id END,"
                " delivered_at = GREATEST(github_recovery_deliveries.delivered_at, EXCLUDED.delivered_at),"
                " successful = github_recovery_deliveries.successful OR EXCLUDED.successful,"
                " state = CASE WHEN EXCLUDED.successful OR github_recovery_deliveries.successful THEN 'successful'"
                " WHEN github_recovery_deliveries.state = 'unavailable' AND EXCLUDED.delivered_at >= ? THEN 'pending'"
                " ELSE github_recovery_deliveries.state END,"
                " seen_scan = EXCLUDED.seen_scan, updated_at = EXCLUDED.updated_at",
                (
                    namespace,
                    item["guid"],
                    item["id"],
                    item["event"],
                    item["delivered_at"],
                    item["successful"],
                    "successful" if item["successful"] else "pending",
                    scan,
                    _stamp(),
                    _stamp(),
                    _stamp(_now() - timedelta(days=HISTORY_DAYS)),
                ),
            )
        db.execute(
            "UPDATE github_recovery_hooks SET cursor = ?, failures = 0, retry_at = '', error_code = '', updated_at = ? WHERE namespace = ?",
            (cursor, _stamp(), namespace),
        )
        if not cursor:
            previous = db.query_one(
                "SELECT last_complete_at FROM github_recovery_hooks WHERE namespace = ?",
                (namespace,),
            )
            if previous is None:
                raise RuntimeError("GitHub recovery state is missing.")
            cutoff = _stamp(_now() - timedelta(days=HISTORY_DAYS))
            since = previous["last_complete_at"] or scan
            if since < cutoff:
                # An unread tail can expire DURING pagination. Detect the lost
                # interval before committing a newer observation checkpoint.
                _gap(namespace, since, cutoff)
            # Application is a separate durable phase. A last page that uses
            # the work budget must not force the next pass to rescan forever.
            db.execute(
                "UPDATE github_recovery_hooks SET last_complete_at = ?, drain_scan = ?, scan_started_at = '' WHERE namespace = ?",
                (scan, scan, namespace),
            )


def _fail(namespace: str, fault: _RemoteFault) -> None:
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        row = db.query_one("SELECT * FROM github_recovery_hooks WHERE namespace = ?", (namespace,))
        if row is None:
            raise RuntimeError("GitHub recovery state is missing.")
        delay = max(
            fault.delay, min(MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(row["failures"], 9))
        )
        db.execute(
            "UPDATE github_recovery_hooks SET failures = failures + 1, retry_at = ?, error_code = ?, updated_at = ? WHERE namespace = ?",
            (_stamp(_now() + timedelta(seconds=delay)), fault.code, _stamp(), namespace),
        )
        if fault.code != "WORK_BOUND":
            # A retry after a remote fault must observe all GUID attempts
            # again. Budget exhaustion alone resumes untouched drain work.
            db.execute(
                "UPDATE github_recovery_hooks SET drain_scan = '' WHERE namespace = ?", (namespace,)
            )
        if fault.code == "CURSOR_INVALID":
            # A rejected pagination token is not completion. Re-read retained
            # history from the head, carrying a visible reconciliation need.
            _gap(namespace, row["last_complete_at"] or row["created_at"], _stamp())
            db.execute(
                "UPDATE github_recovery_hooks SET cursor = '', scan_started_at = '' WHERE namespace = ?",
                (namespace,),
            )


def _drain(
    client: httpx.Client,
    hook: dict,
    row: dict,
    scan: str,
    budget: _Budget,
    registry,
    *,
    limit: int,
) -> int:
    namespace, requested = row["namespace"], 0
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        db.execute(
            "UPDATE github_recovery_deliveries d SET state = 'local_receipt', updated_at = ? WHERE namespace = ? AND state NOT IN ('successful', 'local_receipt') AND EXISTS (SELECT 1 FROM forge_receipts r WHERE r.namespace = d.namespace AND r.delivery_id = d.guid)",
            (_stamp(), namespace),
        )
        vanished = db.query_one(
            "SELECT MIN(delivered_at) AS oldest FROM github_recovery_deliveries WHERE namespace = ? AND state IN ('pending', 'requested', 'unknown') AND seen_scan != ?",
            (namespace, scan),
        )
        if vanished and vanished["oldest"]:
            _gap(namespace, vanished["oldest"], _stamp())
            db.execute(
                "UPDATE github_recovery_deliveries SET state = 'unavailable', updated_at = ? WHERE namespace = ? AND state IN ('pending', 'requested', 'unknown') AND seen_scan != ?",
                (_stamp(), namespace, scan),
            )
    pending = db.query(
        "SELECT * FROM github_recovery_deliveries WHERE namespace = ? AND state IN ('pending', 'requested', 'unknown') AND successful = FALSE AND event IN ('push', 'pull_request') AND attempted_scan != ? AND (retry_at = '' OR retry_at <= ?) ORDER BY delivered_at, guid LIMIT ?",
        (namespace, scan, _stamp(), max(0, min(limit, MAX_POSTS - budget.posts))),
    )
    for item in pending:
        budget.remaining()
        _check_api_throttle()
        if registry is not None:
            from ..extensions.policy import PolicyEffect, PolicyInput, PolicyResource

            decision = registry.policy_engine.decide(
                PolicyInput(
                    registry.service_subject("forge"),
                    "skein.integration.forge",
                    PolicyResource(
                        "integration",
                        "github",
                        attributes={"repository": hook["repository"], "provider": "github"},
                    ),
                    "forge",
                    tool="forge.webhook",
                    tool_effect="write",
                    tool_risk="high",
                )
            )
            if decision.effect != PolicyEffect.PERMIT:
                raise _RemoteFault("POLICY_DENIED")
        with db.transaction():
            db.name_lock(_LOCK, namespace)
            if db.query_one(
                "SELECT 1 FROM forge_receipts WHERE namespace = ? AND delivery_id = ?",
                (namespace, item["guid"]),
            ):
                db.execute(
                    "UPDATE github_recovery_deliveries SET state = 'local_receipt', updated_at = ? WHERE namespace = ? AND guid = ?",
                    (_stamp(), namespace, item["guid"]),
                )
                continue
            delay = min(MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(item["attempts"], 9))
            attempt_id = db.execute(
                "INSERT INTO github_recovery_attempts (namespace, guid, delivery_id, state, created_at, updated_at) VALUES (?, ?, ?, 'unknown', ?, ?) RETURNING id",
                (namespace, item["guid"], item["delivery_id"], _stamp(), _stamp()),
            )
            db.execute(
                "UPDATE github_recovery_deliveries SET state = 'unknown', attempts = attempts + 1, attempted_scan = ?, retry_at = ?, updated_at = ? WHERE namespace = ? AND guid = ?",
                (
                    scan,
                    _stamp(_now() + timedelta(seconds=delay)),
                    _stamp(),
                    namespace,
                    item["guid"],
                ),
            )
            db.execute(
                "UPDATE github_recovery_hooks SET last_drained_at = ? WHERE namespace = ?",
                (_stamp(), namespace),
            )
        # Commit the unknown outcome BEFORE the external POST. A hard crash
        # leaves a delayed GUID retry, not a claim that a request never ran.
        budget.posts += 1
        try:
            _request(client, "POST", f"{_path(hook)}/{item['delivery_id']}/attempts", budget)
        except _RemoteFault as exc:
            state = (
                "unknown"
                if exc.code
                in (
                    "NETWORK_ERROR",
                    "WORK_BOUND",
                    "RESPONSE_TOO_LARGE",
                    "UPSTREAM_UNAVAILABLE",
                    "INVALID_RESPONSE",
                )
                else "failed"
            )
            with db.transaction():
                db.name_lock(_LOCK, namespace)
                db.execute(
                    "UPDATE github_recovery_attempts SET state = ?, error_code = ?, updated_at = ? WHERE id = ?",
                    (state, exc.code, _stamp(), attempt_id),
                )
                if exc.delay:
                    db.execute(
                        "UPDATE github_recovery_deliveries SET retry_at = ? WHERE namespace = ? AND guid = ?",
                        (
                            _stamp(_now() + timedelta(seconds=max(delay, exc.delay))),
                            namespace,
                            item["guid"],
                        ),
                    )
            raise
        budget.requested += 1
        with db.transaction():
            db.name_lock(_LOCK, namespace)
            db.execute(
                "UPDATE github_recovery_attempts SET state = 'accepted', updated_at = ? WHERE id = ?",
                (_stamp(), attempt_id),
            )
            db.execute(
                "UPDATE github_recovery_deliveries SET state = 'requested', updated_at = ? WHERE namespace = ? AND guid = ?",
                (_stamp(), namespace, item["guid"]),
            )
        requested += 1
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        remaining = db.query_one(
            "SELECT 1 FROM github_recovery_deliveries WHERE namespace = ? AND state IN ('pending', 'requested', 'unknown') AND successful = FALSE AND event IN ('push', 'pull_request') AND attempted_scan != ? AND (retry_at = '' OR retry_at <= ?) LIMIT 1",
            (namespace, scan, _stamp()),
        )
        db.execute(
            "UPDATE github_recovery_hooks SET drain_scan = ?, failures = 0, retry_at = '', error_code = '', updated_at = ? WHERE namespace = ?",
            (scan if remaining else "", _stamp(), namespace),
        )
    return requested


def run(*, registry=None) -> dict:
    if not config.GITHUB_RECOVERY:
        return {"enabled": False, "status": "noop"}
    if (
        config.GITHUB_CONFIG_ERROR
        or not config.GITHUB_TOKEN
        or not config.GITHUB_HOOKS
        or not config.FORGE_WEBHOOK_SECRET
    ):
        return {"enabled": True, "status": "error", "error_code": "NOT_CONFIGURED"}
    budget = _Budget(time.monotonic() + MAX_SECONDS)
    drained: set[str] = set()
    result: dict = {"enabled": True, "status": "ok", "pages": 0, "requested": 0, "gaps": 0}
    try:
        # ponytail: one recovery scanner per deployment; per-hook locks if
        # the fixed 32-hook inventory ever needs parallel HTTP throughput.
        with (
            db.session_lock(_LOCK, "github-recovery", wait_seconds=0),
            httpx.Client(
                follow_redirects=False,
                trust_env=False,
                # System/SSL_CERT_FILE trust permits private GitHub Enterprise
                # CAs without enabling ambient proxies or credential routing.
                verify=ssl.create_default_context(),
                headers={
                    "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github+json",
                    "Accept-Encoding": "identity",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Skein-GitHub-Recovery",
                },
            ) as client,
        ):
            _check_api_throttle()
            inventory = {
                forge.github_namespace(h["repository"], h["hook_id"]): h
                for h in config.GITHUB_HOOKS
            }
            quota = max(1, MAX_POSTS // len(inventory))

            def drain_ready() -> None:
                # Observation timestamps do not rotate POST turns. A hook
                # skipped for lack of capacity keeps its old drain priority.
                ready = db.query(
                    "SELECT * FROM github_recovery_hooks WHERE drain_scan != '' ORDER BY last_drained_at, namespace"
                )
                for saved in ready:
                    namespace = saved["namespace"]
                    if namespace not in inventory or namespace in drained:
                        continue
                    if (
                        budget.posts >= MAX_POSTS
                        or time.monotonic() >= budget.deadline
                        or budget.size >= MAX_BYTES
                    ):
                        return
                    row = _start(inventory[namespace])
                    if not row["drain_scan"] or (
                        row["retry_at"] and _timestamp(row["retry_at"]) > _now()
                    ):
                        continue
                    drained.add(namespace)
                    try:
                        _drain(
                            client,
                            inventory[namespace],
                            row,
                            row["drain_scan"],
                            budget,
                            registry,
                            limit=quota,
                        )
                    except _RemoteFault as exc:
                        _fail(namespace, exc)
                        result["status"], result["error_code"] = "error", exc.code
                        if exc.code == "RATE_LIMITED":
                            raise

            # A completed scan gets a drain turn before any new GET work,
            # including when the preceding pass exhausted its GET budget.
            drain_ready()
            stamps = {
                r["namespace"]: r["updated_at"]
                for r in db.query("SELECT namespace, updated_at FROM github_recovery_hooks")
            }
            for namespace in sorted(inventory, key=lambda key: stamps.get(key, "")):
                if namespace in drained:
                    continue
                if budget.pages >= MAX_PAGES or time.monotonic() >= budget.deadline:
                    break
                hook = inventory[namespace]
                row = _start(hook)
                if row["retry_at"] and _timestamp(row["retry_at"]) > _now():
                    result["status"], result["error_code"] = "error", row["error_code"]
                    continue
                if row["drain_scan"]:
                    continue
                try:
                    scan, cursor = row["scan_started_at"], row["cursor"]
                    while budget.pages < MAX_PAGES:
                        budget.pages += 1
                        query = {"per_page": "100", **({"cursor": cursor} if cursor else {})}
                        data, response_headers = _request(
                            client, "GET", _path(hook) + "?" + urlencode(query), budget
                        )
                        rows = _deliveries(data)
                        next_cursor = _next_cursor(response_headers, _path(hook))
                        if next_cursor and next_cursor == cursor:
                            raise _RemoteFault("INVALID_PAGINATION")
                        _save_page(namespace, scan, rows, next_cursor)
                        cursor = next_cursor
                        if not cursor:
                            break
                except _RemoteFault as exc:
                    _fail(namespace, exc)
                    result["status"], result["error_code"] = "error", exc.code
                    if exc.code == "RATE_LIMITED":
                        raise
            drain_ready()
            namespaces = [
                forge.github_namespace(h["repository"], h["hook_id"]) for h in config.GITHUB_HOOKS
            ]
            result["gaps"] = sum(
                1
                for r in db.query(
                    "SELECT namespace FROM github_recovery_hooks WHERE gap_since != '' AND reconciled_at = ''"
                )
                if r["namespace"] in namespaces
            )
            if result["gaps"]:
                result["status"] = "error"
                result["error_code"] = "HISTORY_GAP"
    except psycopg.errors.LockNotAvailable:
        return {"enabled": True, "status": "noop"}
    except _RemoteFault as exc:
        result["status"], result["error_code"] = "error", exc.code
    except Exception:
        # jobs.run_job includes exception messages in job health. Remote URLs,
        # secrets and response bodies must never reach that generic logger.
        result["status"], result["error_code"] = "error", "RECOVERY_FAILED"
    result["pages"] = budget.pages
    result["requested"] = budget.requested
    if result["status"] == "ok" and not budget.pages and not budget.posts and not drained:
        # jobs.py refreshes health on 'ok'. Setup alone is not evidence
        # that a history observation or a drain turn worked.
        result["status"] = "noop"
    return result


def _admin_policy(
    policy: PolicyEngine, subject: PolicySubject, action: str, row: dict | None = None
) -> None:
    from ..extensions.fastapi import enforce_decision

    attributes = {"provider": "github"}
    if row is not None:
        attributes.update(repository=row["repository"], hook_id=str(row["hook_id"]))
    enforce_decision(
        policy.decide(
            PolicyInput(
                subject,
                f"skein.integration.github_recovery.{action}",
                PolicyResource(
                    "integration",
                    row["namespace"] if row is not None else "github-recovery",
                    attributes=attributes,
                ),
                "human",
                tool=f"github_recovery.{action}",
                tool_effect="read" if action == "read" else "write",
                tool_risk="low" if action == "read" else "high",
            )
        )
    )


def status(*, policy: PolicyEngine, subject: PolicySubject) -> dict:
    _admin_policy(policy, subject, "read")
    namespaces = {
        forge.github_namespace(h["repository"], h["hook_id"]) for h in config.GITHUB_HOOKS
    }
    rows = db.query(
        "SELECT namespace, repository, hook_id, scan_started_at != '' AS scan_in_progress, drain_scan != '' AS drain_pending, last_drained_at, last_complete_at, retry_at, error_code, gap_since, gap_until, reconciled_at, reconciled_by, reconciliation_note FROM github_recovery_hooks ORDER BY repository, hook_id"
    )
    rows = [row for row in rows if row["namespace"] in namespaces]
    for row in rows:
        _admin_policy(policy, subject, "read", row)
    return {
        "enabled": config.GITHUB_RECOVERY,
        "configured": bool(
            config.GITHUB_HOOKS
            and config.GITHUB_TOKEN
            and config.FORGE_WEBHOOK_SECRET
            and not config.GITHUB_CONFIG_ERROR
        ),
        "history_days": HISTORY_DAYS,
        "retry_at": _api_retry_at(),
        "hooks": rows,
    }


def acknowledge_gap(
    namespace: str,
    gap_until: str,
    note: str,
    *,
    policy: PolicyEngine,
    subject: PolicySubject,
) -> dict:
    actor = subject.name
    if not note.strip() or len(note) > 1000 or "\x00" in note:
        raise ValueError("A reconciliation note is required. Describe the current-state checks.")
    allowed = {forge.github_namespace(h["repository"], h["hook_id"]) for h in config.GITHUB_HOOKS}
    with db.transaction():
        db.name_lock(_LOCK, namespace)
        row = db.query_one("SELECT * FROM github_recovery_hooks WHERE namespace = ?", (namespace,))
        if namespace not in allowed or not row or not row["gap_since"]:
            raise ValueError("No GitHub history gap is available. Check recovery status.")
        # The scanner holds this same namespace lock before replacing a gap.
        # Policy must see the gap that this acknowledgment writes.
        _admin_policy(policy, subject, "reconcile", row)
        if row["gap_until"] != gap_until:
            raise ValueError("The GitHub history gap changed. Check current state again.")
        db.execute(
            "UPDATE github_recovery_hooks SET reconciled_at = ?, reconciled_by = ?, reconciliation_note = ?, updated_at = ? WHERE namespace = ?",
            (_stamp(), actor, note.strip(), _stamp(), namespace),
        )
        db.log_activity(
            actor,
            "reconcile_github_gap",
            "Recorded a manual current-state comparison. Missing historical events remain unavailable.",
        )
    return {"reconciled": True}
