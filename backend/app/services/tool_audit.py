"""Durable, content-free receipts for governed external tool execution."""

from .. import db


def record_tool_execution(*, actor: str, tool: str, status: str, error_code: str = "") -> None:
    detail = f"{tool} {status}"
    if error_code:
        detail += f" ({error_code})"
    db.log_activity(actor, "external_tool", detail)
