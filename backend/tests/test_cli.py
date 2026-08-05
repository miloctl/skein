"""The stdlib CLI: git-trailer parsing and user-visible wording."""

import importlib.util
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
    cli.cmd_commitments(Namespace(action="settle", id=3, status="kept"))
    out = capsys.readouterr().out
    assert "promise #3 kept" in out
    assert "commitment" not in out
