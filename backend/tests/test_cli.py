"""The stdlib CLI: git-trailer parsing and user-visible wording."""

import contextlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "skein_cli", Path(__file__).parents[2] / "cli" / "skein_cli.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def test_cli_trailer_regex():
    cli = _load_cli()
    msg = "Fix auth\n\nCloses-Task: #12\nRefs-Task: 7\n"
    assert cli.TRAILER.findall(msg) == [("Closes", "12"), ("Refs", "7")]


def test_cli_settle_says_promise(monkeypatch, capsys):
    """The reader's word is promise (docs/LEXICON.md row 1); `commitment`
    stays on the wire only. The frontend sweep in one-wording.test.ts cannot
    see this surface, so the CLI pins its own wording."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "api", lambda *a, **k: {})
    cli.cmd_promises(Namespace(action="settle", id=3, status="kept"))
    out = capsys.readouterr().out
    assert "promise #3 kept" in out
    assert "commitment" not in out


def test_the_commit_hook_fires_on_a_dash_m_commit(tmp_path):
    """git's $2 is "message" for `git commit -m`, which is how most commits
    are written and the case where the trailer helps most — nobody sees an
    editor to add it by hand. Skipping every non-empty source skipped exactly
    that. merge and squash stay skipped: their message is assembled from other
    commits, and a trailer there would claim this commit closed the task."""
    import subprocess

    cli = _load_cli()
    hook = tmp_path / "prepare-commit-msg"
    hook.write_text(cli.COMMIT_MSG_HOOK)
    repo = tmp_path / "repo"
    repo.mkdir()
    # a branch with NO commits yet: `git init`, then `skein task start`, then
    # the first commit. rev-parse cannot resolve HEAD here, so the hook reads
    # symbolic-ref instead — otherwise it misses the commit that starts the work.
    subprocess.run(
        ["git", "init", "-q", "-b", "task/42-do-the-thing"],  # noqa: S607 — git/sh on PATH, tmp_path repo
        cwd=repo,
        capture_output=True,
    )

    def message_after(source: str) -> str:
        msg = tmp_path / "MSG"
        msg.write_text("wire it up\n")
        subprocess.run(  # noqa: S603 — git/sh on PATH, tmp_path repo
            ["sh", str(hook), str(msg), source],  # noqa: S607 — git/sh on PATH, tmp_path repo
            cwd=repo,
            check=False,
        )
        return msg.read_text()

    assert "Closes-Task: #42" in message_after("")  # editor
    assert "Closes-Task: #42" in message_after("message")  # git commit -m
    assert "Closes-Task" not in message_after("merge")
    assert "Closes-Task" not in message_after("squash")


def test_the_commit_hook_ignores_a_branch_that_names_no_task(tmp_path):
    import subprocess

    cli = _load_cli()
    hook = tmp_path / "prepare-commit-msg"
    hook.write_text(cli.COMMIT_MSG_HOOK)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],  # noqa: S607 — git/sh on PATH, tmp_path repo
        cwd=repo,
        capture_output=True,
    )
    msg = tmp_path / "MSG"
    msg.write_text("unrelated work\n")
    subprocess.run(  # noqa: S603 — git/sh on PATH, tmp_path repo
        ["sh", str(hook), str(msg), ""],  # noqa: S607 — git/sh on PATH, tmp_path repo
        cwd=repo,
        check=False,
    )
    assert "Closes-Task" not in msg.read_text()


def test_the_branch_slug_is_git_safe():
    cli = _load_cli()
    # git refuses a ref with a space, a double dot, or a trailing dot
    assert cli._slug("Ship the thing: fast!") == "ship-the-thing-fast"
    assert cli._slug("  ...odd.. Title  ") == "odd-title"
    assert cli._slug("a" * 3 + " b c d e f g h") == "aaa-b-c-d-e-f"  # capped at six words


def test_attention_porcelain_says_nothing_when_nothing_waits(monkeypatch, capsys, tmp_path):
    """A prompt segment that renders "0" is noise on every line of a clean
    day, and one that raises ruins every line."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "api_quiet", lambda *a, **k: {"count": 0})
    cli.cmd_attention(Namespace(porcelain=True))
    assert capsys.readouterr().out == ""


def test_attention_is_silent_and_short_when_the_server_is_down(monkeypatch, capsys, tmp_path):
    """The real failure is api_quiet returning None, not raising — it catches
    everything. What must hold: no output, no raise, and a SHORT timeout,
    because a host that drops packets never refuses the connection and the
    caller waits the whole budget on every keystroke."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    seen = {}

    def down(method, path, body=None, timeout=15):
        seen["timeout"] = timeout
        return None

    monkeypatch.setattr(cli, "api_quiet", down)
    cli.cmd_attention(Namespace(porcelain=True))
    assert capsys.readouterr().out == ""
    assert seen["timeout"] <= 2, "a shell prompt cannot wait on the API default"


def test_a_failed_attention_is_remembered_so_the_prompt_stops_asking(monkeypatch, capsys, tmp_path):
    """The cache gates on its MTIME. Writing only on success left the file
    untouched, so the age check never applied and a dead backend was retried
    on every prompt — a stall per keystroke, forever."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    calls = {"n": 0}

    def down(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(cli, "api_quiet", down)
    cli.cmd_attention(Namespace(porcelain=True))
    cli.cmd_attention(Namespace(porcelain=True))
    cli.cmd_attention(Namespace(porcelain=True))
    assert calls["n"] == 1, "the failure was not stamped — every prompt asks again"
    assert capsys.readouterr().out == ""


def test_attention_says_what_happened_when_it_is_not_a_prompt(monkeypatch, capsys, tmp_path):
    """--porcelain stays silent because a prompt cannot carry an explanation.
    The bare command can, and must: a lone "?" is a failure rendered as data."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "api_quiet", lambda *a, **k: None)
    cli.cmd_attention(Namespace(porcelain=False))
    out = capsys.readouterr().out
    assert "did not answer" in out
    assert "?" not in out


def test_attention_computes_its_plural(monkeypatch, capsys, tmp_path):
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "api_quiet", lambda *a, **k: {"count": 1})
    cli.cmd_attention(Namespace(porcelain=False))
    assert "1 thing waiting on you" in capsys.readouterr().out


def test_a_capture_survives_a_dead_server_and_files_later(monkeypatch, capsys, tmp_path):
    """The one write a person makes mid-thought. Losing it to a dead server is
    the failure the outbox exists to prevent."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(cli, "api_quiet", lambda *a, **k: None)  # server down
    cli.cmd_capture(Namespace(text=["todo:", "fix", "it"]))
    assert "saved locally" in capsys.readouterr().out
    assert (tmp_path / "outbox.jsonl").exists()

    sent = []
    monkeypatch.setattr(cli, "api_quiet", lambda m, p, b=None: sent.append((p, b)) or {"ok": 1})
    assert cli.flush_outbox() == 1
    assert sent == [("/api/capture", {"text": "todo: fix it"})]
    # the row leaves only after the server accepts it
    assert not (tmp_path / "outbox.jsonl").exists()


def test_a_failed_flush_keeps_everything_it_did_not_send(monkeypatch, tmp_path):
    """A queue that drops the tail because row two failed loses work the
    person believed was captured."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    for i in range(3):
        cli._queue("/api/capture", {"text": f"note {i}"})

    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return {"ok": 1} if calls["n"] == 1 else None

    monkeypatch.setattr(cli, "api_quiet", flaky)
    assert cli.flush_outbox() == 1
    left = [json.loads(x) for x in (tmp_path / "outbox.jsonl").read_text().splitlines()]
    assert [r["body"]["text"] for r in left] == ["note 1", "note 2"]


def test_the_hook_reads_only_a_real_task_number(tmp_path):
    """Three definitions of a task branch exist — this hook, BRANCH_RE, and
    services/forge.py. `task/12abc` was accepted by the shell one alone, which
    would have closed task 12 from an unrelated branch."""
    import subprocess

    cli = _load_cli()
    hook = tmp_path / "prepare-commit-msg"
    hook.write_text(cli.COMMIT_MSG_HOOK)

    def trailer_for(branch: str) -> str:
        repo = tmp_path / branch.replace("/", "_")
        repo.mkdir()
        subprocess.run(  # noqa: S603 — git/sh on PATH, tmp_path repo
            ["git", "init", "-q", "-b", branch],  # noqa: S607 — git/sh on PATH, tmp_path repo
            cwd=repo,
            capture_output=True,
        )
        msg = repo / "MSG"
        msg.write_text("work\n")
        subprocess.run(  # noqa: S603 — git/sh on PATH, tmp_path repo
            ["sh", str(hook), str(msg), ""],  # noqa: S607 — git/sh on PATH, tmp_path repo
            cwd=repo,
            check=False,
        )
        return msg.read_text()

    assert "Closes-Task: #42" in trailer_for("task/42-slug")
    assert "Closes-Task: #42" in trailer_for("task/42")
    assert "Closes-Task" not in trailer_for("task/12abc")
    assert "Closes-Task" not in trailer_for("task/nodigits")


def test_the_hook_leaves_an_existing_trailer_alone(tmp_path):
    """A hand-written `Refs-Task: #42` must not gain a `Closes-Task: #42`
    beneath it — that turns a commit which REFERENCED a task into one that
    closes it. The guard used a GNU-only BRE alternation, which BSD grep reads
    as a literal pipe."""
    import subprocess

    cli = _load_cli()
    hook = tmp_path / "prepare-commit-msg"
    hook.write_text(cli.COMMIT_MSG_HOOK)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "task/42-slug"],  # noqa: S607 — git/sh on PATH, tmp_path repo
        cwd=repo,
        capture_output=True,
    )
    msg = repo / "MSG"
    msg.write_text("work\n\nRefs-Task: #42\n")
    subprocess.run(  # noqa: S603 — git/sh on PATH, tmp_path repo
        ["sh", str(hook), str(msg), ""],  # noqa: S607 — git/sh on PATH, tmp_path repo
        cwd=repo,
        check=False,
    )
    assert "Closes-Task" not in msg.read_text()


def test_a_refused_capture_is_not_queued_as_if_the_server_were_down(monkeypatch, capsys, tmp_path):
    """A 4xx is the server's verdict and will be the same verdict forever.
    Queueing it promised a filing that could never happen, exited 0 on a write
    that did not land, and parked every later capture behind it."""
    import urllib.error

    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox.jsonl")
    refusal = urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(cli, "api_quiet", lambda *a, **k: refusal)

    def loud(*a, **k):
        raise SystemExit("error: text too long")

    monkeypatch.setattr(cli, "api", loud)
    with contextlib.suppress(SystemExit):
        cli.cmd_capture(Namespace(text=["todo:", "x"]))
    assert not (tmp_path / "outbox.jsonl").exists()


def test_a_poison_row_is_dropped_instead_of_blocking_the_queue(monkeypatch, capsys, tmp_path):
    """A row the server permanently refuses used to be rewritten to the head
    of the queue on every flush, so every later capture waited behind a row
    that could never send."""
    import urllib.error

    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox.jsonl")
    cli._queue("/api/capture", {"text": "poison"})
    cli._queue("/api/capture", {"text": "good"})

    refusal = urllib.error.HTTPError("u", 422, "Unprocessable", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        cli, "api_quiet", lambda m, p, b=None, **k: refusal if b["text"] == "poison" else {"ok": 1}
    )
    assert cli.flush_outbox() == 1
    assert not (tmp_path / "outbox.jsonl").exists()
    assert "refused and dropped" in capsys.readouterr().err


def test_a_corrupt_line_does_not_wedge_the_queue(monkeypatch, tmp_path):
    """A crash mid-append can truncate one line. Raising there made every
    capture behind it unreachable for the life of the file, silently."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setattr(cli, "OUTBOX", outbox)
    outbox.write_text(
        '{"path": "/api/capture", "bo\n'
        + json.dumps({"path": "/api/capture", "body": {"text": "survivor"}})
        + "\n"
    )

    sent = []
    monkeypatch.setattr(cli, "api_quiet", lambda m, p, b=None, **k: sent.append(b) or {"ok": 1})
    assert cli.flush_outbox() == 1
    assert sent == [{"text": "survivor"}]


def test_a_capture_made_during_a_flush_is_not_destroyed(monkeypatch, tmp_path):
    """The flush used to read the file and then truncate it. Anything a second
    shell appended in between was gone — the opposite direction from the
    duplication the at-least-once note calls safe."""
    cli = _load_cli()
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox.jsonl")
    cli._queue("/api/capture", {"text": "first"})

    def send_then_interleave(m, p, b=None, **k):
        cli._queue("/api/capture", {"text": "arrived mid-flush"})
        return {"ok": 1}

    monkeypatch.setattr(cli, "api_quiet", send_then_interleave)
    assert cli.flush_outbox() == 1
    left = [json.loads(x) for x in (tmp_path / "outbox.jsonl").read_text().splitlines()]
    assert [r["body"]["text"] for r in left] == ["arrived mid-flush"]
