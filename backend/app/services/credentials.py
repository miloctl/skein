"""Seal personal MCP credentials and server-held OIDC browser tokens.

SKEIN_CREDENTIAL_KEY stays in the deployment Secret, never in the database.
Backups carry MCP ciphertext. Browser-session rows are omitted entirely so
restoring a database cannot restore a logged-out browser's authority."""

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
            "Skein cannot store the credential. Ask whoever runs the server to"
            " set a valid SKEIN_CREDENTIAL_KEY. Then try the operation again."
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
