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

# A baseline from before the PostgreSQL migration cannot be upgraded to HEAD:
# its code writes a SQLite file and HEAD reads a PostgreSQL server, so there is
# no database for HEAD's migrations to apply TO. Skip with the reason rather
# than fail, and re-arm automatically — the moment a tag exists on this side of
# the engine change, this test passes and the check runs again.
# grep reads to EOF on purpose (no -q): under pipefail, grep -q exiting on
# the first match hands git show a SIGPIPE, the pipeline reports 141, and a
# PostgreSQL-era baseline skips as if it predated the migration.
if ! git show "$baseline:backend/app/config.py" 2>/dev/null | grep "SKEIN_DATABASE_URL" >/dev/null; then
    echo "upgrade-path: baseline $baseline predates the PostgreSQL migration, so there is no"
    echo "upgrade-path: upgrade path from it to HEAD. Skipped. This re-arms on the next release tag."
    exit 0
fi

if [ -z "${SKEIN_DATABASE_URL:-}" ]; then
    echo "upgrade-path: SKEIN_DATABASE_URL is not set. Set it to a PostgreSQL server." >&2
    exit 1
fi

python="backend/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python)"

tmp="$(mktemp -d)"

# One database per side. Isolation used to be a data directory; the app
# keeps no database under SKEIN_DATA_DIR now, so two instances sharing a
# server would share one schema and the comparison would prove nothing.
db_base="${SKEIN_DATABASE_URL%/*}"
upgraded_db="skein_upgrade_upgraded_$$"
fresh_db="skein_upgrade_fresh_$$"
for name in "$upgraded_db" "$fresh_db"; do
    psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" >/dev/null
    psql "$SKEIN_DATABASE_URL" -qtAc "CREATE DATABASE \"$name\"" >/dev/null
done
trap 'psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$upgraded_db\" WITH (FORCE)" >/dev/null 2>&1 || true;
      psql "$SKEIN_DATABASE_URL" -qtAc "DROP DATABASE IF EXISTS \"$fresh_db\" WITH (FORCE)" >/dev/null 2>&1 || true;
      git worktree remove --force "$tmp/base" 2>/dev/null || true; rm -rf "$tmp"' EXIT

echo "upgrade-path: baseline $baseline"
git worktree add --detach "$tmp/base" "$baseline" >/dev/null

# 1. a database as the baseline release built it, carrying chained rows —
#    CI databases are otherwise always empty, which is the blind spot
SKEIN_DATABASE_URL="$db_base/$upgraded_db" PYTHONPATH="$tmp/base/backend" "$python" - <<'PY'
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
SKEIN_DATABASE_URL="$db_base/$upgraded_db" PYTHONPATH="backend" "$python" -c "from app import db; db.init_db()"

# 3. a fresh database from HEAD alone
SKEIN_DATABASE_URL="$db_base/$fresh_db" PYTHONPATH="backend" "$python" -c "from app import db; db.init_db()"

# 4. the two schemas must be identical, object by object
"$python" - "$db_base/$upgraded_db" "$db_base/$fresh_db" <<'PY'
import sys

import psycopg


def schema(url):
    """Every column, as the catalog reports it — not a dump, whose owners,
    comments and ordering differ between two freshly created databases and
    would make every run a false failure."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public'"
        ).fetchall()
    return {(r[0], r[1]): r[2:] for r in rows}


upgraded, fresh = schema(sys.argv[1]), schema(sys.argv[2])
diverged = False
for key in sorted(set(upgraded) | set(fresh)):
    a, b = upgraded.get(key), fresh.get(key)
    if a != b:
        diverged = True
        print(f"upgrade-path: {key[0]}.{key[1]} upgraded={a} fresh={b}")
if diverged:
    sys.exit("upgrade-path: the upgraded schema differs from a fresh one")
print("upgrade-path: schemas identical")
PY

# 5. the hash chain written at the baseline must verify after the upgrade
SKEIN_DATABASE_URL="$db_base/$upgraded_db" PYTHONPATH="backend" "$python" - <<'PY'
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
