#!/usr/bin/env bash
# On-demand mutation testing over the service layer. Not a CI gate: a full
# sweep takes hours, so run it on the module you touched, or leave it
# sweeping overnight with no argument.
#
#   ./scripts/mutation-test.sh                     # all of app/services/
#   ./scripts/mutation-test.sh work                # app/services/work.py
#   ./scripts/mutation-test.sh work blockers       # several modules
#
# Read the survivors with:  (cd backend && .venv/bin/mutmut results)
# Show one mutant's diff:   (cd backend && .venv/bin/mutmut show <name>)
#
# A survivor means a change to the write path that no test noticed. Judge
# each one: equivalent mutants (a log string, an internal cache key) are
# fine to leave; a surviving mutation of a permission check, a cap, or a
# recorded field is a missing test, and the fix is the test, not the list.
#
# mutmut 3 mutates module-level functions only. A module whose logic lives
# in class methods (projection_policy) or constants (slas) reports zero
# mutants — that is a tool limit, not proof the tests have teeth there.
set -euo pipefail
cd "$(dirname "$0")/../backend"

if [ ! -x .venv/bin/mutmut ]; then
    echo "mutmut is not installed. Run: uv pip install -e '.[dev]' --python .venv/bin/python" >&2
    exit 2
fi

# mutants/ is mutmut's cache: it detects source changes itself, and the
# baseline stats phase it saves costs ~7 minutes. If a run behaves as if
# it remembers a tree that no longer exists, delete backend/mutants.

if [ "$#" -eq 0 ]; then
    exec .venv/bin/mutmut run
fi

patterns=()
for module in "$@"; do
    if [ ! -f "app/services/${module%.py}.py" ]; then
        echo "app/services/${module%.py}.py does not exist." >&2
        exit 2
    fi
    # mutant names are dotted module paths, not file paths
    patterns+=("app.services.${module%.py}*")
done
exec .venv/bin/mutmut run "${patterns[@]}"
