#!/usr/bin/env python3
"""Comments and docstrings that point at something must point at something real.

A false comment reads as coverage, and the code under it keeps running, so no
test notices when the thing it names goes away. This gate checks the claims a
regex can check:

- a repo path (`services/crews.py`, `lib/api.ts`) names a tracked file
- `file.py::symbol` names a symbol that file still contains
- a migration is cited by its file name (`001_baseline.sql`), and the file
  exists. A bare number ("migration 007") is refused: the v1 baseline squash
  renumbered every migration, and about forty comments kept the old numbers.
- no SQLite-only mechanism (BEGIN IMMEDIATE, NOCASE, FTS5, private.db, ...)
  is described as how the code works - the engine is PostgreSQL
- no tool tag (`ponytail:`) stands in for the constraint it was meant to state

Known ceiling: it checks that a name exists, never that the sentence about it
is true. Lock and race claims still need a reader who traces the call chain.

Usage:
  python3 scripts/check_comment_refs.py
  python3 scripts/check_comment_refs.py --self-test
"""

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".sql"}
# Paths inside a dependency, cited for an SDK rule the code must follow.
EXTERNAL = (
    "strands/",
    "mcp/",
    "psycopg",
    "next/",
    "react-markdown/",
    "@assistant-ui/",
    "node_modules/",
)
# kaneo's source files, which tests/gitlab_payloads.py cites as the origin of its payload shapes.
FOREIGN = {"apps/api/src/plugins/gitlab/webhooks/push.ts", "utils/payload.ts"}

PATH = re.compile(
    r"(?<![\w/.\-])((?:[\w\-\[\]]+/)+[\w\-.\[\]]+\.(?:py|tsx|ts|mjs|sql|sh|md|yaml|yml|json))(?![\w])"
    r"(?:::(\w+))?"
)
SYMBOL = re.compile(r"(?<![\w/.\-])([\w\-]+\.(?:py|tsx|ts))::(\w+)")
MIGRATION_FILE = re.compile(r"\b(\d{3}_\w+\.sql)\b")
MIGRATION_NUMBER = re.compile(r"\bmigrations? \d{3}\b|\bpre-\d{3}\b", re.I)
SQLITE = re.compile(
    r"\b(BEGIN IMMEDIATE|NOCASE|FTS5|private\.db|julianday|GROUP_CONCAT|INSERT OR REPLACE|"
    r"AUTOINCREMENT|WAL mode)\b",
    re.I,
)
TOOL_TAG = re.compile(r"\bponytail:", re.I)


def py_blocks(src):
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string[1:]
    except (tokenize.TokenError, SyntaxError):
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                yield first.lineno, first.value.value


def js_blocks(src):
    i, n, line, quote = 0, len(src), 1, None
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
        if quote:
            if c == "\\":
                line += src[i + 1 : i + 2] == "\n"
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            yield line, src[i + 2 : j]
            i = j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j
            yield line, src[i + 2 : j]
            line += src.count("\n", i, j)
            i = j + 2
            continue
        i += 1


def hash_blocks(src, marker):
    for no, raw in enumerate(src.splitlines(), 1):
        s = raw.strip()
        if s.startswith(marker) and not s.startswith("#!"):
            yield no, s[len(marker) :]


def blocks(path, src):
    ext = Path(path).suffix
    if ext == ".py":
        return py_blocks(src)
    if ext == ".sh":
        return hash_blocks(src, "#")
    if ext == ".sql":
        return hash_blocks(src, "--")
    return js_blocks(src)


def problems(path, text, tracked, migrations, read):
    # A path broken across a comment line ("chat-\n  sidebar.tsx") is one name.
    text = re.sub(r"(?<=\w)-[ \t]*\n[\s*#/]*", "-", text)
    out = []
    refs = [m.groups() for m in PATH.finditer(text)] + [m.groups() for m in SYMBOL.finditer(text)]
    for ref, sym in refs:
        if ref.startswith(EXTERNAL) or ref in FOREIGN:
            continue
        hits = [t for t in tracked if t == ref or t.endswith("/" + ref)]
        if not hits:
            if "/" in ref:
                out.append(f"names {ref}, which is not a tracked file")
        elif sym and not any(re.search(rf"\b{re.escape(sym)}\b", read(h)) for h in hits):
            out.append(f"names {ref}::{sym}, which that file does not contain")
    for m in MIGRATION_FILE.finditer(text):
        if m.group(1) not in migrations:
            out.append(f"names {m.group(1)}, which is not a migration file")
    if MIGRATION_NUMBER.search(text):
        out.append("cites a migration by number - cite its file name instead")
    for m in SQLITE.finditer(text):
        out.append(f"describes {m.group(1)}, a SQLite mechanism - the engine is PostgreSQL")
    if TOOL_TAG.search(text):
        out.append("carries a tool tag - state the limit and when to upgrade instead")
    return out


def lint(files, tracked, migrations, read):
    found = []
    for path in files:
        if path == "scripts/check_comment_refs.py":
            continue  # its docstring names every refused term
        if path.startswith("backend/app/core_migrations/"):
            # An applied migration is never edited (CLAUDE.md), so its comments
            # record the code of the day it shipped, references included.
            continue
        for line, text in blocks(path, read(path)):
            for p in problems(path, text, tracked, migrations, read):
                found.append(f"{path}:{line}: {p}")
    return found


def _expect(ok, context):
    if not ok:
        raise SystemExit(f"self-test failed: {context}")


def self_test():
    tracked = ["backend/app/services/crews.py", "backend/app/core_migrations/001_baseline.sql"]
    sources = {
        "backend/app/services/crews.py": "def assert_steward():\n    pass\n",
        "bad.py": (
            "# see services/gone.py and services/crews.py::_crew_steward\n"
            "# the column arrived in migration 007 (009_old.sql); BEGIN IMMEDIATE holds it\n"
            '"""ponytail: one pass, add an index if it matters"""\n'
        ),
        "good.ts": (
            "// services/crews.py::assert_steward decides it (001_baseline.sql).\n"
            "/* strands/tools/decorator.py runs a sync tool in a thread */\n"
            'const url = "http://host/a.py"; // the string is code, not a comment\n'
        ),
    }
    migrations = {"001_baseline.sql"}
    bad = lint(["bad.py"], tracked, migrations, sources.__getitem__)
    good = lint(["good.ts"], tracked, migrations, sources.__getitem__)
    for needle in (
        "services/gone.py",
        "_crew_steward",
        "by number",
        "009_old.sql",
        "BEGIN IMMEDIATE",
        "tool tag",
    ):
        _expect(any(needle in b for b in bad), (needle, bad))
    _expect(good == [], good)
    print("self-test OK:", len(bad), "problems in the bad fixture, 0 in the good one")


def main():
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    tracked = subprocess.run(  # noqa: S603 - fixed argv, no caller input
        # untracked files too: lint.sh runs before the commit that adds them
        ["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607 - git on PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    files = [f for f in tracked if Path(f).suffix in EXTS and (REPO / f).is_file()]
    migrations = {p.name for p in (REPO / "backend/app/core_migrations").glob("*.sql")}
    cache: dict[str, str] = {}

    def read(path):
        if path not in cache:
            cache[path] = (REPO / path).read_text(encoding="utf-8", errors="replace")
        return cache[path]

    found = lint(files, tracked, migrations, read)
    for f in found:
        print(f)
    if found:
        raise SystemExit(
            f"{len(found)} comment reference problem(s). Fix the comment, not the check."
        )
    print(f"comment references resolve ({len(files)} files)")


if __name__ == "__main__":
    main()
