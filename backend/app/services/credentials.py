"""Seal a per-person credential for storage in mcp_servers.

The key is SKEIN_CREDENTIAL_KEY, read from the deployment Secret and never
written to the database, so every backup and export of a sealed column
carries ciphertext only. Unset, seal() refuses and the caller reports which
variable whoever runs the server must set."""

from cryptography.fernet import Fernet, InvalidToken

from .. import config


def _fernet() -> Fernet | None:
    try:
        return Fernet(config.CREDENTIAL_KEY.encode()) if config.CREDENTIAL_KEY else None
    except ValueError:
        return None


def available() -> bool:
    return _fernet() is not None


def seal(text: str) -> bytes:
    fernet = _fernet()
    if fernet is None:
        raise ValueError(
            "A credential cannot be stored: SKEIN_CREDENTIAL_KEY is not set."
            " Whoever runs the server must set it, then add the server again."
        )
    return fernet.encrypt(text.encode())


def unseal(blob: bytes) -> str:
    """'' when the key changed since sealing: the server then connects without
    a token and fails visibly, instead of the row becoming unreadable."""
    fernet = _fernet()
    if fernet is None:
        return ""
    try:
        return fernet.decrypt(bytes(blob)).decode()
    except InvalidToken:
        return ""
