"""Frontend constants that restate a backend rule. A preview, a select or a
link table that drifts from the service it mirrors tells the reader one thing
and does another, and no rendered test sees both sides at once."""

import re
from pathlib import Path

from conftest import authored_repo_root

from app.services import blockers, capture, chat_threads, search, work

FRONTEND = authored_repo_root(Path(__file__)) / "frontend"


def _source(relative: str) -> str:
    return (FRONTEND / relative).read_text()


def _block(source: str, start: str, end: str) -> str:
    i = source.index(start)
    return source[i : source.index(end, i)]


def _ts_strings(source: str, const: str) -> list[str]:
    return re.findall(r'"([^"]+)"', re.search(rf"const {const} = \[(.*?)\];", source).group(1))


def test_the_capture_preview_classifies_with_the_backend_grammar():
    """The palette's "will file as" line runs RULES. A rule the backend lacks,
    or holds in a different order, previews one kind and files another."""
    source = _source("components/capture-palette.tsx")
    rules = _block(source, "const RULES", "];")
    ts = [(k, p, f == "i") for k, p, f in re.findall(r'\["(\w+)", /(.+)/(i?)\]', rules)]
    py = [(k, p.pattern, bool(p.flags & re.I)) for k, p in capture.PATTERNS]
    assert ts == py

    known = re.search(r"const KNOWN_PREFIX =\s*/(.+)/i;", source).group(1)
    # `fb` is the one extra prefix: capture() routes an fb: line to private
    # feedback before it reads PREFIX
    assert known.replace("|fb)", ")") == capture.PREFIX.pattern
    assert capture.PREFIX.flags & re.I


def test_the_task_and_blocker_selects_offer_only_values_the_backend_accepts():
    """A value absent from the frontend is unpickable; a value absent from
    the backend is a 400 for every person who picks it."""
    blocker_form = _source("components/raise-blocker-form.tsx")
    assert _ts_strings(blocker_form, "IMPACTS") == list(blockers.IMPACTS)
    peek = _source("components/task-peek.tsx")
    assert _ts_strings(peek, "PRIORITIES") == list(work.PRIORITIES)
    # blocked and void are left out on purpose (the comment above STATUSES)
    assert _ts_strings(peek, "STATUSES") == [
        s for s in work.TASK_STATUSES if s not in ("blocked", "void")
    ]


def test_the_rename_field_stops_at_the_chat_title_length():
    """Past TITLE_LEN the PATCH answers with a pydantic sentence."""
    sidebar = _source("components/chat-sidebar.tsx")
    cap = re.search(r"chat_threads\.TITLE_LEN.*?maxLength=\{(\d+)\}", sidebar, re.S).group(1)
    assert int(cap) == chat_threads.TITLE_LEN


def test_every_indexed_entity_links_to_a_page_from_search():
    """An indexed entity with no page renders as dead text in nav search.
    EntityLink routes task (to the peek) and document itself."""
    source = _source("components/nav-search.tsx")
    table = _block(source, "const ENTITY_PAGE", "};")
    pages = set(re.findall(r"^\s+(\w+): \"/", table, re.MULTILINE))
    assert search.INDEXED_KINDS - {"task", "document"} <= pages
