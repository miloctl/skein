#!/usr/bin/env python
"""Create, delete, or use one isolated PostgreSQL contract database."""

from __future__ import annotations

import os
import re
import shutil
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def database_name(value: str) -> str:
    if not re.fullmatch(r"(?:skein_atlas_contract|skein_contract)_[a-z0-9_]{1,48}", value):
        raise SystemExit("The contract database name is not safe.")
    if len(value.encode()) > 63:
        raise SystemExit("The contract database name is too long.")
    return value


def role_name(value: str) -> str:
    if not re.fullmatch(r"(?:skein_atlas_role|skein_contract_role)_[a-z0-9_]{1,40}", value):
        raise SystemExit("The contract role name is not safe.")
    return value


def role_credentials() -> tuple[str, str]:
    role = role_name(os.environ.get("SKEIN_CONTRACT_ROLE_NAME", ""))
    password = os.environ.get("SKEIN_CONTRACT_ROLE_PASSWORD", "")
    if not re.fullmatch(r"[0-9a-f]{48}", password):
        raise SystemExit("The contract role password is not safe.")
    return role, password


def database_url(
    base: str,
    name: str,
    role: str,
    password: str,
    host: str = "",
) -> str:
    fields = conninfo_to_dict(base)
    fields.update({"dbname": name, "user": role, "password": password})
    if host:
        fields["host"] = host
    return make_conninfo(**{key: str(value) for key, value in fields.items() if value is not None})


def main() -> None:
    actions = {
        "create-role",
        "drop-role",
        "create",
        "drop",
        "run",
        "run-clean",
        "run-docker",
    }
    if len(sys.argv) < 2 or sys.argv[1] not in actions:
        raise SystemExit(
            "Usage: contract-db.py create-role|drop-role|create|drop|run|run-clean|run-docker"
            " [DATABASE_NAME] [COMMAND ...]"
        )
    action = sys.argv[1]
    base = os.environ.get("SKEIN_DATABASE_URL", "")
    if not base:
        raise SystemExit("Set SKEIN_DATABASE_URL to the PostgreSQL administrator database.")
    role, password = role_credentials()
    if action in {"create-role", "drop-role"}:
        with psycopg.connect(base, autocommit=True) as connection:
            if action == "create-role":
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB"
                        " NOCREATEROLE NOREPLICATION"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
            else:
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        return
    if len(sys.argv) < 3:
        raise SystemExit("The database action needs a database name.")
    name = database_name(sys.argv[2])
    if action in {"run", "run-clean", "run-docker"}:
        if len(sys.argv) < 4:
            raise SystemExit("The run action needs a command.")
        environment = (
            {"PATH": os.environ.get("PATH", os.defpath)}
            if action == "run-clean"
            else dict(os.environ)
        )
        docker_host = (
            environment.get("SKEIN_CONTRACT_DOCKER_DB_HOST", "host.docker.internal")
            if action == "run-docker"
            else ""
        )
        environment["SKEIN_DATABASE_URL"] = database_url(base, name, role, password, docker_host)
        environment.pop("SKEIN_CONTRACT_ROLE_NAME", None)
        environment.pop("SKEIN_CONTRACT_ROLE_PASSWORD", None)
        executable = shutil.which(sys.argv[3], path=environment.get("PATH"))
        if executable is None:
            raise SystemExit("The contract command is not installed.")
        os.execve(executable, sys.argv[3:], environment)  # noqa: S606 -- resolved absolute path
    with psycopg.connect(base, autocommit=True) as connection:
        if action == "create":
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name),
                    sql.Identifier(role),
                )
            )
        else:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


if __name__ == "__main__":
    main()
