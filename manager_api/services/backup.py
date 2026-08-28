"""Encrypted database backup packages and restore verification."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ..db.base import Base
from .vault import VaultError, VaultService, VaultUnlockError

BACKUP_FORMAT_VERSION = 1


class BackupError(Exception):
    """Base error for backup creation and verification."""


class BackupFormatError(BackupError):
    """Raised when a package is malformed or has a mismatched checksum."""


@dataclass(frozen=True)
class BackupVerification:
    """Non-sensitive verification result suitable for API responses."""

    format_version: int
    table_count: int
    row_count: int
    vault_recovery_key_valid: bool
    checksums_valid: bool


def create_backup(session: Session, vault: VaultService, recovery_key: str) -> bytes:
    """Serialize manager tables and encrypt them with the recovery key."""
    tables = _snapshot_tables(session)
    payload = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "tables": tables,
        "manifest": _manifest(tables),
    }
    raw_payload = _canonical_json(payload)
    try:
        envelope = vault.encrypt_backup_payload(raw_payload, recovery_key)
    except (VaultError, VaultUnlockError) as exc:
        raise BackupError("backup encryption failed") from exc
    ciphertext = _decode_text(envelope["ciphertext"])
    package = {
        "format_version": BACKUP_FORMAT_VERSION,
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "envelope": envelope,
    }
    return _canonical_json(package) + b"\n"


def verify_backup(
    session: Session,
    vault: VaultService,
    package: bytes,
    recovery_key: str,
) -> BackupVerification:
    """Decrypt, checksum, and validate a package without mutating the database."""
    outer = _parse_package(package)
    envelope = outer["envelope"]
    if not isinstance(envelope, dict):
        raise BackupFormatError("missing backup envelope")
    ciphertext = _decode_text(envelope.get("ciphertext"))
    if outer.get("ciphertext_sha256") != hashlib.sha256(ciphertext).hexdigest():
        raise BackupFormatError("backup ciphertext checksum mismatch")
    try:
        raw_payload = vault.decrypt_backup_payload(envelope, recovery_key)
    except VaultUnlockError as exc:
        raise BackupFormatError("backup decryption failed") from exc
    payload = _parse_payload(raw_payload)
    tables = payload.get("tables")
    manifest = payload.get("manifest")
    if not isinstance(tables, dict) or not isinstance(manifest, dict):
        raise BackupFormatError("backup payload is incomplete")
    if _manifest(tables) != manifest:
        raise BackupFormatError("backup manifest mismatch")
    vault_rows = tables.get("vault_metadata", {}).get("rows", [])
    if not _verify_embedded_vault(vault_rows, vault, recovery_key):
        raise BackupFormatError("backup recovery key does not unwrap vault metadata")
    format_version = payload.get("format_version")
    if not isinstance(format_version, int):
        raise BackupFormatError("unsupported backup payload")
    return BackupVerification(
        format_version=format_version,
        table_count=len(tables),
        row_count=sum(len(value.get("rows", [])) for value in tables.values()),
        vault_recovery_key_valid=True,
        checksums_valid=True,
    )


def restore_backup(
    target_session: Session,
    vault: VaultService,
    package: bytes,
    recovery_key: str,
) -> BackupVerification:
    """Restore a verified package into an empty target database transaction."""
    outer = _parse_package(package)
    envelope = outer.get("envelope")
    if not isinstance(envelope, dict):
        raise BackupFormatError("missing backup envelope")
    _verify_ciphertext_checksum(outer, envelope)
    raw_payload = vault.decrypt_backup_payload(envelope, recovery_key)
    payload = _parse_payload(raw_payload)
    tables = payload.get("tables")
    manifest = payload.get("manifest")
    if not isinstance(tables, dict) or not isinstance(manifest, dict) or _manifest(tables) != manifest:
        raise BackupFormatError("backup manifest mismatch")
    if _target_has_manager_rows(target_session):
        raise BackupError("restore target must be empty")
    _insert_tables(target_session, tables)
    target_session.flush()
    return verify_backup(target_session, vault, package, recovery_key)


def write_backup(path: Path, package: bytes) -> None:
    """Write a backup package with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(package)
    path.chmod(0o600)


def _snapshot_tables(session: Session) -> dict[str, dict[str, object]]:
    """Capture manager tables; migration bookkeeping is recreated separately."""
    snapshots: dict[str, dict[str, object]] = {}
    for table in Base.metadata.sorted_tables:
        rows = [
            {column: _encode_value(value) for column, value in row._mapping.items()}
            for row in session.execute(select(table)).all()
        ]
        snapshots[table.name] = {
            "columns": [column.name for column in table.columns],
            "rows": rows,
        }
    return snapshots


def _manifest(tables: dict[str, Any]) -> dict[str, dict[str, object]]:
    """Create deterministic per-table counts and checksums."""
    return {
        name: {
            "row_count": len(value.get("rows", [])),
            "sha256": hashlib.sha256(_canonical_json(value)).hexdigest(),
        }
        for name, value in sorted(tables.items())
    }


def _verify_embedded_vault(
    rows: object,
    vault: VaultService,
    recovery_key: str,
) -> bool:
    """Check the package's own recovery wrap rather than the live database row."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    row = rows[0]
    try:
        wrapped = _decode_tagged_bytes(row["wrapped_with_recovery"])
        salt = _decode_tagged_bytes(row["recovery_salt"])
        parameters = row["recovery_kdf"]
        if not isinstance(parameters, dict):
            return False
        vault.verify_wrapped_recovery_key(wrapped, salt, parameters, recovery_key)
        return True
    except (KeyError, TypeError, ValueError, VaultError):
        return False


def _insert_tables(session: Session, tables: dict[str, Any]) -> None:
    """Insert parent tables before child tables using metadata dependency order."""
    known_tables = {table.name: table for table in Base.metadata.sorted_tables}
    if set(tables) != set(known_tables):
        raise BackupFormatError("backup table set does not match manager schema")
    for table in Base.metadata.sorted_tables:
        snapshot = tables[table.name]
        if snapshot.get("columns") != [column.name for column in table.columns]:
            raise BackupFormatError(f"backup columns mismatch for {table.name}")
        rows = snapshot.get("rows")
        if not isinstance(rows, list):
            raise BackupFormatError(f"backup rows missing for {table.name}")
        decoded_rows = [
            {key: _decode_value(value) for key, value in row.items()}
            for row in rows
            if isinstance(row, dict)
        ]
        if len(decoded_rows) != len(rows):
            raise BackupFormatError(f"backup row is malformed for {table.name}")
        if decoded_rows:
            session.execute(insert(table), decoded_rows)


def _target_has_manager_rows(session: Session) -> bool:
    """Reject restore into any target database that already has manager rows."""
    for table in Base.metadata.sorted_tables:
        if session.execute(select(table).limit(1)).first() is not None:
            return True
    return False


def _parse_package(package: bytes) -> dict[str, object]:
    """Parse the outer package and enforce its version."""
    try:
        value = json.loads(package.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupFormatError("invalid backup package") from exc
    if not isinstance(value, dict) or value.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupFormatError("unsupported backup package")
    return value


def _verify_ciphertext_checksum(
    outer: dict[str, object],
    envelope: dict[str, object],
) -> None:
    """Validate the outer checksum before attempting any restore mutation."""
    ciphertext = _decode_text(envelope.get("ciphertext"))
    if outer.get("ciphertext_sha256") != hashlib.sha256(ciphertext).hexdigest():
        raise BackupFormatError("backup ciphertext checksum mismatch")


def _parse_payload(payload: bytes) -> dict[str, object]:
    """Parse decrypted backup contents and enforce its version."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupFormatError("invalid backup payload") from exc
    if not isinstance(value, dict) or value.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupFormatError("unsupported backup payload")
    return value


def _encode_value(value: object) -> object:
    """Tag database values that JSON cannot preserve on a future restore."""
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": base64.urlsafe_b64encode(value).decode("ascii")}
    if isinstance(value, UUID):
        return {"__type__": "uuid", "value": str(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    return value


def _decode_value(value: object) -> object:
    """Decode tagged values emitted by `_encode_value`."""
    if not isinstance(value, dict) or "__type__" not in value:
        return value
    kind = value.get("__type__")
    raw = value.get("value")
    if kind == "bytes":
        return _decode_text(raw)
    if kind == "uuid":
        return UUID(str(raw))
    if kind == "datetime":
        return datetime.fromisoformat(str(raw))
    raise BackupFormatError("unknown backup value type")


def _decode_tagged_bytes(value: object) -> bytes:
    """Decode one tagged byte value from a database snapshot."""
    decoded = _decode_value(value)
    if not isinstance(decoded, bytes):
        raise ValueError("expected encoded bytes")
    return decoded


def _decode_text(value: object) -> bytes:
    """Decode one URL-safe Base64 field."""
    if not isinstance(value, str):
        raise ValueError("expected Base64 text")
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _canonical_json(value: object) -> bytes:
    """Serialize package structures deterministically for checksums and encryption."""
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
