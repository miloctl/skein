#!/usr/bin/env python3
"""Qualify SDK session persistence with two preinstalled Python 3.12 environments.

SKEIN_DATABASE_URL must name a PostgreSQL control database with CREATEDB.
Run: python scripts/check-session-upgrade.py --old-python PATH --new-python PATH
Only the randomly named database created by this process is deleted.
"""

# This is an executable test, not a service. Optimized execution is refused below.
# ruff: noqa: S101

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections import deque
from importlib.metadata import version
from pathlib import Path

if not __debug__:
    raise SystemExit("Optimized Python disables the checks. Run without -O or PYTHONOPTIMIZE.")

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {"write": "1.55.1", "append": "1.56.0", "restart": "1.56.0", "rollback": "1.55.1"}


def guard_target(conninfo: str, owned: str) -> None:
    from psycopg.conninfo import conninfo_to_dict

    if not re.fullmatch(r"skein_sdk_upgrade_[0-9a-f]{32}", owned):
        raise ValueError("The database ownership name is invalid. Run the coordinator.")
    if conninfo_to_dict(conninfo).get("dbname") != owned:
        raise ValueError("The database target is not owned by this run. Run the coordinator.")


def content(messages: list) -> list:
    # Tracking IDs are SDK metadata, not conversation content. They can be regenerated on restore.
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def paired(messages: list) -> None:
    uses, results = [], []
    for index, message in enumerate(messages):
        current = [b["toolUse"]["toolUseId"] for b in message["content"] if "toolUse" in b]
        found = [b["toolResult"]["toolUseId"] for b in message["content"] if "toolResult" in b]
        if current:
            assert index + 1 < len(messages), "Tool call has no result message"
            following = messages[index + 1]["content"]
            assert current == [b["toolResult"]["toolUseId"] for b in following if "toolResult" in b]
        if found:
            assert index > 0
            preceding = messages[index - 1]["content"]
            assert found == [b["toolUse"]["toolUseId"] for b in preceding if "toolUse" in b]
        uses.extend(current)
        results.extend(found)
    assert len(uses) == len(set(uses)), "Duplicate tool call IDs"
    assert uses == results, "Unpaired tool results"


def worker(stage: str, owned: str, checkpoint: Path) -> None:
    import psycopg

    guard_target(os.environ["SKEIN_DATABASE_URL"], owned)
    assert sys.version_info[:2] == (3, 12)
    assert version("strands-agents") == VERSIONS[stage]
    with psycopg.connect(os.environ["SKEIN_DATABASE_URL"]) as connection:
        row = connection.execute(
            "SELECT current_database(), shobj_description(oid, 'pg_database')"
            " FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        assert row == (owned, owned), "The database ownership marker does not match"

    sys.path.insert(0, str(ROOT / "backend"))
    from strands import Agent, tool
    from strands.models.model import Model
    from strands.types.exceptions import ContextWindowOverflowException

    from app import config, db
    from app.agents import session_log, session_store, team_agent

    class ScriptedModel(Model):
        def __init__(self):
            self.responses = deque()
            self.calls = []
            self.config = {"model_id": "session-qualification"}

        def get_config(self):
            return self.config

        def update_config(self, **kwargs):
            self.config.update(kwargs)

        def structured_output(self, *args, **kwargs):
            raise AssertionError("Structured output is outside this drill")

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.calls.append(content(messages))
            assert self.responses, "Unexpected model call"
            reply = self.responses.popleft()
            if isinstance(reply, Exception):
                raise reply
            yield {"messageStart": {"role": "assistant"}}
            if isinstance(reply, dict):
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {"toolUseId": reply["toolUseId"], "name": reply["name"]}
                        }
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "delta": {"toolUse": {"input": json.dumps(reply["input"])}}
                    }
                }
            else:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": reply}}}
            yield {"contentBlockStop": {}}
            yield {
                "messageStop": {"stopReason": "tool_use" if isinstance(reply, dict) else "end_turn"}
            }
            yield {
                "metadata": {
                    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                    "metrics": {"latencyMs": 1},
                }
            }

    def oversized(sid):
        return (f"{sid} harmless qualification content\n" * 1000) + f"{sid} TAIL-RECEIPT\n"

    def build(sid, strategy):
        config.CONTEXT_STRATEGY = strategy
        model = ScriptedModel()

        @tool
        def large_result() -> str:
            """Return harmless deterministic qualification content."""
            return oversized(sid)

        agent = Agent(
            model=model,
            tools=[large_result],
            session_manager=session_store.session_manager(sid),
            conversation_manager=team_agent._conversation_manager(),
            plugins=team_agent._offloader_plugins(sid, None, {"tools": None}),
            callback_handler=None,
        )
        return agent, model

    def invoke(agent, model, prompt, *replies):
        model.responses.extend(replies)
        result = agent(prompt)
        assert not model.responses, "A scripted model response was not consumed"
        assert result.stop_reason == "end_turn"
        paired(content(agent.messages))

    def snapshot(sid, agent):
        repo = session_store.DatabaseSessionRepository()
        rows = [m.to_dict() for m in repo.list_messages(sid, "default")]
        assert [m["message_id"] for m in rows] == list(range(len(rows)))
        paired([m["message"] for m in rows])
        return {
            "session": repo.read_session(sid).to_dict(),
            "agent": repo.read_agent(sid, "default").to_dict(),
            "rows": rows,
            "live": content(agent.messages),
            "blobs": [
                {**r, "content": hashlib.sha256(bytes(r["content"])).hexdigest()}
                for r in db.query(
                    "SELECT key, content, created_at FROM session_offload"
                    " WHERE session_id = ? ORDER BY key",
                    (sid,),
                )
            ],
        }

    def unchanged(before, after):
        assert after["rows"][: len(before["rows"])] == before["rows"], "Existing messages changed"
        assert after["session"] == before["session"], "Session creation metadata changed"
        assert after["agent"]["created_at"] == before["agent"]["created_at"]
        assert after["blobs"] == before["blobs"], "Offloaded content or creation timestamps changed"

    db.init_db()
    previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    records = {}
    try:
        for strategy in ("sliding", "summarize"):
            sid = f"upgrade-{strategy}"
            if stage == "write":
                config.CONTEXT_STRATEGY = strategy
                session_log.log_exchange(sid, f"/help {sid}", f"{sid} opening command")
                agent, model = build(sid, strategy)
                assert f"{sid} opening command" in json.dumps(agent.messages)
                for n in range(4):
                    invoke(agent, model, f"{sid} prompt {n}", f"{sid} reply {n}")
                if strategy == "summarize":
                    invoke(
                        agent,
                        model,
                        f"{sid} overflow",
                        ContextWindowOverflowException("scripted context overflow"),
                        f"{sid} retained summary",
                        f"{sid} recovered reply",
                    )
                assert agent.conversation_manager.get_state()["removed_message_count"] > 0
                session_log.log_exchange(sid, f"/status {sid}", f"{sid} bridged command")
                agent, model = build(sid, strategy)
                assert f"{sid} bridged command" in json.dumps(agent.messages)
                invoke(
                    agent,
                    model,
                    f"{sid} offload",
                    {"toolUseId": f"{sid}-large", "name": "large_result", "input": {}},
                    f"{sid} offload complete",
                )
            else:
                agent, model = build(sid, strategy)
                before = previous[sid]
                restored = snapshot(sid, agent)
                unchanged(before, restored)
                assert restored["live"] == before["live"], (
                    "Restored replay differs from the prior process"
                )
                assert restored["agent"] == before["agent"]
                assert (
                    agent.conversation_manager.get_state()
                    == before["agent"]["conversation_manager_state"]
                )
                paired(restored["live"])
                if stage == "restart":
                    reference = previous[sid]["reference"]
                    invoke(
                        agent,
                        model,
                        f"{sid} retrieve after restart",
                        {
                            "toolUseId": f"{sid}-retrieve",
                            "name": "retrieve_offloaded_content",
                            "input": {
                                "reference": reference,
                                "pattern": "TAIL-RECEIPT",
                                "context_lines": 0,
                            },
                        },
                        f"{sid} retrieval complete",
                    )
                    result = next(
                        b["toolResult"]
                        for m in agent.messages
                        for b in m["content"]
                        if b.get("toolResult", {}).get("toolUseId") == f"{sid}-retrieve"
                    )
                    assert result["status"] == "success"
                    assert f"{sid} TAIL-RECEIPT" in json.dumps(result)
                else:
                    invoke(agent, model, f"{sid} {stage} prompt", f"{sid} {stage} reply")
                assert model.calls[0][:-1] == restored["live"], (
                    "The model did not receive the restored replay"
                )
            current = snapshot(sid, agent)
            state = current["agent"]["conversation_manager_state"]
            assert state == agent.conversation_manager.get_state(), (
                "Hooks did not persist the manager state"
            )
            assert state["removed_message_count"] > 0
            if strategy == "summarize":
                assert f"{sid} retained summary" in json.dumps(state["summary_message"])
                assert current["live"][0] == content([state["summary_message"]])[0]
            rows_text = json.dumps(current["rows"])
            assert rows_text.count(f"/help {sid}") == 1
            assert rows_text.count(f"/status {sid}") == 1
            assert json.dumps(oversized(sid)) not in rows_text
            offloaded = next(
                b["toolResult"]
                for m in current["rows"]
                for b in m["message"]["content"]
                if b.get("toolResult", {}).get("toolUseId") == f"{sid}-large"
            )
            preview = offloaded["content"][0]["text"]
            assert "[Offloaded:" in preview and "truncated at storage" not in preview
            assert "TAIL-RECEIPT" not in preview
            reference = preview.split("[Stored references:]\n", 1)[1].strip().split(" ", 1)[0]
            current["reference"] = reference
            assert current["blobs"], "The real offloader did not write durable bytes"
            stored = session_store.DbOffloadStorage(sid)
            for key in asyncio.run(stored.list()):
                assert (
                    asyncio.run(session_store.DbOffloadStorage("upgrade-isolated").read(key))
                    is None
                )
            other = "upgrade-summarize" if strategy == "sliding" else "upgrade-sliding"
            assert other not in json.dumps(current["rows"]) + json.dumps(current["live"])
            if stage != "write":
                unchanged(previous[sid], current)
                expected_added = 4 if stage == "restart" else 2
                assert len(current["rows"]) == len(previous[sid]["rows"]) + expected_added
            if stage == "write":
                baseline, _ = build(sid, strategy)
                assert snapshot(sid, baseline) == {
                    k: v for k, v in current.items() if k != "reference"
                }, "The old SDK failed its own restore control"
            records[sid] = current
            print(
                json.dumps(
                    {
                        "stage": stage,
                        "sdk": version("strands-agents"),
                        "session": sid,
                        "messages": len(current["rows"]),
                        "replay_offset": state["removed_message_count"],
                        "summary": bool(state.get("summary_message")),
                        "offload_blobs": len(current["blobs"]),
                    }
                ),
                flush=True,
            )
        isolated, isolated_model = build("upgrade-isolated", "sliding")
        if stage == "write":
            assert not isolated.messages
            invoke(isolated, isolated_model, "isolated prompt", "isolated answer")
        else:
            assert snapshot("upgrade-isolated", isolated) == previous["upgrade-isolated"]
        records["upgrade-isolated"] = snapshot("upgrade-isolated", isolated)
        checkpoint.write_text(json.dumps(records))
    finally:
        db.close_pool()


def coordinate(old_python: str, new_python: str) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base = os.environ.get("SKEIN_DATABASE_URL", "")
    if not base:
        raise ValueError("SKEIN_DATABASE_URL is not set. Set a disposable PostgreSQL control URL.")
    owned = f"skein_sdk_upgrade_{secrets.token_hex(16)}"
    target = make_conninfo(base, dbname=owned)
    guard_target(target, owned)
    with psycopg.connect(base, autocommit=True, connect_timeout=3) as control:
        control.execute("SET statement_timeout = '10s'")
        control.execute("SET lock_timeout = '3s'")
        flags = control.execute(
            "SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert flags and any(flags), "The control role needs CREATEDB"
        assert control.info.server_version >= 170000, "PostgreSQL 17 or newer is required"
        assert not control.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (owned,)
        ).fetchone()
        # CREATE without IF EXISTS makes a collision fail before cleanup can acquire ownership.
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(owned)))
        try:
            control.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(owned), sql.Literal(owned)
                )
            )
            with tempfile.TemporaryDirectory(prefix="skein-session-upgrade-") as directory:
                env = {
                    "PATH": os.defpath,
                    "HOME": directory,
                    "LANG": "C.UTF-8",
                    "PYTHON_DOTENV_DISABLED": "1",
                    "PYTHONNOUSERSITE": "1",
                    "OTEL_TRACES_EXPORTER": "none",
                    "OTEL_METRICS_EXPORTER": "none",
                    "OTEL_LOGS_EXPORTER": "none",
                    "NO_PROXY": "*",
                    "SKEIN_DATABASE_URL": target,
                    "SKEIN_DATA_DIR": directory,
                    "SKEIN_SCHEDULER": "0",
                    "SKEIN_EMBEDDINGS": "0",
                    "SKEIN_MODEL_PROVIDER": "ollama",
                    "SKEIN_MODELS": "",
                    "SKEIN_MODELS_FILE": "",
                    "SKEIN_MODEL_PARAMS": "",
                    "SKEIN_MODEL_PARAMS_FILE": "",
                    "SKEIN_MCP_SERVERS": "",
                    "SKEIN_MCP_SERVERS_FILE": "",
                    "SKEIN_CONTEXT_WINDOW_MESSAGES": "8",
                    "SKEIN_CONTEXT_SUMMARY_RATIO": "0.5",
                    "SKEIN_CONTEXT_PRESERVE_RECENT": "2",
                    "SKEIN_CONTEXT_PIN_FIRST": "0",
                    "SKEIN_CONTEXT_PROACTIVE": "0",
                    "SKEIN_OFFLOAD_RESULT_TOKENS": "2500",
                    "SKEIN_OFFLOAD_PREVIEW_TOKENS": "100",
                    "OTEL_SDK_DISABLED": "true",
                }
                for stage, python in (
                    ("write", old_python),
                    ("append", new_python),
                    ("restart", new_python),
                    ("rollback", old_python),
                ):
                    # The operator supplies trusted, preinstalled interpreters. No shell is used.
                    subprocess.run(  # noqa: S603
                        [
                            python,
                            str(Path(__file__).resolve()),
                            "--worker",
                            stage,
                            "--owned-database",
                            owned,
                            "--checkpoint",
                            f"{directory}/checkpoint.json",
                        ],
                        env=env,
                        check=True,
                        timeout=180,
                    )
        finally:
            control.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(owned)))
            print(f"Deleted coordinator-owned database {owned}", flush=True)
    print(
        "PASS: old write, new restore/append, new restart/retrieval, old rollback restore/append."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-python")
    parser.add_argument("--new-python")
    parser.add_argument("--worker", choices=VERSIONS, help=argparse.SUPPRESS)
    parser.add_argument("--owned-database", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.owned_database or not args.checkpoint:
            parser.error("Worker ownership and checkpoint are required. Run the coordinator.")
        worker(args.worker, args.owned_database, args.checkpoint)
    elif args.old_python and args.new_python:
        coordinate(args.old_python, args.new_python)
    else:
        parser.error("Both Python paths are required. Set --old-python and --new-python.")


if __name__ == "__main__":
    main()
