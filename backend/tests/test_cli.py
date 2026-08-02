"""The stdlib CLI: git-trailer parsing."""


def test_cli_trailer_regex():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "skein_cli", Path(__file__).parents[2] / "cli" / "skein_cli.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    msg = "Fix auth\n\nCloses-Task: #12\nRefs-Task: 7\n"
    assert cli.TRAILER.findall(msg) == [("Closes", "12"), ("Refs", "7")]
