"""Small, literal KEY=value configuration; never execute shell expressions."""
import os
import re
import secrets
from pathlib import Path


def init_env(path: str = ".env") -> bool:
    body = (
        "# Local configuration. Edit the destination before accepting webhooks.\n"
        f"SYNCBRIDGE_API_TOKEN={secrets.token_hex(32)}\n"
        f"SYNCBRIDGE_WEBHOOK_SECRET={secrets.token_hex(32)}\n"
        "SYNCBRIDGE_DESTINATION=rest\n"
        "DESTINATION_URL=\n"
        "DESTINATION_TOKEN=\n"
        "# DATABASE_URL=postgresql://user:password@localhost/syncbridge\n"
        "# SYNCBRIDGE_FIELD_MAP=config/field-map.example.json\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return True


def destination_kind() -> str:
    """Never interpret an unknown adapter name as permission to send to REST."""
    kind = os.getenv("SYNCBRIDGE_DESTINATION", "rest")
    if kind not in ("rest", "notion"):
        raise ValueError("SYNCBRIDGE_DESTINATION must be rest or notion")
    return kind


def database_url() -> str:
    """Reject a configured unsupported backend instead of silently using SQLite."""
    dsn = os.getenv("DATABASE_URL", "")
    if dsn and not dsn.startswith(("postgres://", "postgresql://")):
        raise ValueError("DATABASE_URL must use postgres:// or postgresql://")
    return dsn


def load_env(path: str = ".env", *, required: bool = False) -> None:
    file = Path(path)
    values = {}
    try:
        handle = file.open(encoding="utf-8-sig")
    except FileNotFoundError:
        if required or file.is_symlink():
            raise
        return
    with handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"invalid environment assignment on line {number}")
            if key in values:
                raise ValueError(f"duplicate environment assignment on line {number}")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"invalid environment quoting on line {number}")
                value = value[1:-1]
            if "\x00" in value:
                raise ValueError(f"invalid environment value on line {number}")
            values[key] = value
    # Validate the entire file before altering process configuration. Explicit
    # service/container environment always wins, including explicitly empty values.
    for key, value in values.items():
        os.environ.setdefault(key, value)
