#!/usr/bin/env bash
# The upgrade path: the schema a deployed database reaches by applying
# HEAD's pending migrations must equal the schema a fresh database gets
# from HEAD alone — and the activity hash chain must survive the ride.
# Every test database is born fresh at HEAD, so this class (an edited or
# renamed migration leaving upgraded production diverging from fresh CI)
# has no other net; a gutted migration in the pre-squash corpus was the
# founding example.
#
# Baseline: the newest v* tag — the thing a deployment can actually be
# running. Before the first release tag nothing is deployed, so there is
# nothing to upgrade from; the check says so and passes. An explicit ref
# overrides for local runs:
#     scripts/upgrade-path.sh [baseline-ref]
set -euo pipefail
cd "$(dirname "$0")/.."

# The newest v* tag that is NOT the commit under test. Taking the newest tag
# outright made this check vacuous for exactly one commit per release: the
# release commit carries the tag, so the baseline was HEAD and the run
# compared a tree with itself and passed.
baseline="${1:-}"
if [ -z "$baseline" ]; then
    head_commit="$(git rev-parse HEAD)"
    for candidate in $(git tag --list 'v*' --sort=-version:refname); do
        if [ "$(git rev-parse "$candidate^{commit}")" != "$head_commit" ]; then
            baseline="$candidate"
            break
        fi
    done
fi
if [ -z "$baseline" ]; then
    echo "upgrade-path: no v* release tag other than this commit, so there is nothing to upgrade from. Skipped."
    exit 0
fi

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python)"

tmp="$(mktemp -d)"
trap 'git worktree remove --force "$tmp/base" 2>/dev/null || true; rm -rf "$tmp"' EXIT

echo "upgrade-path: baseline $baseline"
git worktree add --detach "$tmp/base" "$baseline" >/dev/null

# 1. a database as the baseline release built it, carrying chained rows —
#    CI databases are otherwise always empty, which is the blind spot
SKEIN_DATA_DIR="$tmp/upgraded" PYTHONPATH="$tmp/base/backend" "$python" - <<'PY'
from app import db
from app.services import engagements, work

db.init_db()
for i in range(3):
    db.log_activity("ci", "upgrade_probe", f"row {i}")
direct = engagements.create_engagement("Upgrade direct", project_class="standard")["id"]
engagements.create_engagement("Upgrade milestone", project_class="regulated")
milestone = work.create_milestone("Upgrade gate", project="Upgrade milestone")["id"]
task = work.create_task("Upgrade relationship conflict", engagement_id=direct)
# The legacy row this check exists for: a task whose milestone and
# engagement disagree. Services refuse that combination since the
# relationship guard shipped, so passing it to create_task only worked
# while the baseline predated the guard — once a release carrying the
# guard becomes the baseline, the seed itself raises. A deployed database
# still holds such rows, so write the row the way that deployment has it.
db.execute("UPDATE tasks SET milestone_id = ? WHERE id = ?", (milestone, task["id"]))
PY

# 2. HEAD boots it — the upgrade a deployment performs
SKEIN_DATA_DIR="$tmp/upgraded" PYTHONPATH="backend" "$python" -c "from app import db; db.init_db()"

# 3. a fresh database from HEAD alone
SKEIN_DATA_DIR="$tmp/fresh" PYTHONPATH="backend" "$python" -c "from app import db; db.init_db()"

# 4. the two schemas must be identical, object by object
"$python" - "$tmp/upgraded/platform.db" "$tmp/fresh/platform.db" <<'PY'
import sqlite3
import sys


def schema(path):
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    conn.close()
    return {(t, n): s for t, n, s in rows}


upgraded, fresh = schema(sys.argv[1]), schema(sys.argv[2])
diverged = False
for key in sorted(set(upgraded) | set(fresh)):
    a, b = upgraded.get(key), fresh.get(key)
    if a != b:
        diverged = True
        print(f"DIVERGED {key[0]} {key[1]}:")
        print(f"  upgraded: {a}")
        print(f"  fresh:    {b}")
if diverged:
    sys.exit(1)
print("upgrade-path: schemas identical")
PY

# 5. the hash chain written at the baseline must verify after the upgrade
SKEIN_DATA_DIR="$tmp/upgraded" PYTHONPATH="backend" "$python" - <<'PY'
from app import db
from app.services import activity
from app.agents.core_tools import _resource
from app.extensions.policy import PolicyEffect, PolicyEngine, PolicyInput, PolicySubject
from app.services import policy_context

out = activity.verify_chain()
assert out["ok"], out
task = db.query_one(
    "SELECT id FROM tasks WHERE title = 'Upgrade relationship conflict'"
)
attributes = policy_context.existing("task", int(task["id"]))
assert attributes["relationship_conflict"] == "true", attributes
decision = PolicyEngine().decide(
    PolicyInput(
        PolicySubject("agent", kind="agent"),
        "task.read",
        _resource({"task_id": int(task["id"])}),
        "agent",
    )
)
assert decision.effect == PolicyEffect.DENY, decision
print(f"upgrade-path: chain ok through seq {out['chained_through']}")
PY

echo "upgrade-path: ok"
