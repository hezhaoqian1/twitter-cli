"""Recoverable AES-GCM vault with Argon2id key wrapping."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.vault import VaultMetadata

VAULT_FORMAT_VERSION = 1
KEY_BYTES = 32
NONCE_BYTES = 12
DEFAULT_CACHE_TTL_SECONDS = 900.0
BACKUP_KDF_PARAMETERS: dict[str, int | str] = {
    "algorithm": "argon2id",
    "time_cost": 3,
    "memory_cost": 64 * 1024,
    "parallelism": 2,
    "hash_len": KEY_BYTES,
    "version": 19,
}
KDF_PARAMETERS: dict[str, int | str] = {
    "algorithm": "argon2id",
    "time_cost": 3,
    "memory_cost": 64 * 1024,
    "parallelism": 2,
    "hash_len": KEY_BYTES,
    "version": 19,
}
GENERIC_UNLOCK_ERROR = "vault unlock failed"


class VaultError(Exception):
    """Base error for vault operations."""


class VaultAlreadyInitializedError(VaultError):
    """Raised when a second active vault is requested."""


class VaultUnlockError(VaultError):
    """Raised without revealing whether a vault exists or a secret matched."""


class VaultRuntime:
    """Hold one unwrapped vault key for the lifetime of the API process."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        self.cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._vault_key: bytes | None = None
        self._unlocked_until = 0.0

    @property
    def is_unlocked(self) -> bool:
        """Return whether the process-local key is inside its TTL window."""
        if self._vault_key is None:
            return False
        if self._clock() >= self._unlocked_until:
            self.lock()
            return False
        return True

    def get_key(self) -> bytes | None:
        """Return the cached key while it is valid, otherwise expire it."""
        if not self.is_unlocked:
            return None
        return self._vault_key

    def set_key(self, vault_key: bytes) -> None:
        """Cache the unwrapped key until the configured expiry."""
        self._vault_key = vault_key
        self._unlocked_until = self._clock() + self.cache_ttl_seconds

    def lock(self) -> None:
        """Erase the process-local reference to the unwrapped key."""
        self._vault_key = None
        self._unlocked_until = 0.0


@dataclass(frozen=True)
class VaultInitialization:
    """Initialization result containing the one-time recovery key."""

    metadata: VaultMetadata
    recovery_key: str


class VaultService:
    """Own all encryption, decryption, wrapping, and in-memory key handling."""

    def __init__(
        self,
        session: Session,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        runtime: VaultRuntime | None = None,
    ) -> None:
        self.session = session
        self.runtime = runtime or VaultRuntime(
            cache_ttl_seconds=cache_ttl_seconds,
            clock=clock,
        )

    @property
    def is_unlocked(self) -> bool:
        """Return whether the in-memory key is still inside its TTL window."""
        return self.runtime.is_unlocked

    @property
    def initialized(self) -> bool:
        """Return whether an active vault metadata record exists."""
        metadata = self._get_metadata()
        return metadata is not None and metadata.active

    @property
    def initialized_at(self):
        """Return the initialization timestamp without exposing key material."""
        metadata = self._get_metadata()
        return metadata.initialized_at if metadata is not None and metadata.active else None

    def initialize(
        self,
        password: str,
    ) -> VaultInitialization:
        """Create one vault and return the recovery key exactly once."""
        if not password:
            raise ValueError("password must not be empty")
        existing = self._get_metadata()
        if existing is not None:
            raise VaultAlreadyInitializedError("vault already initialized")

        generated_recovery_key = self._new_recovery_key()

        password_salt = secrets.token_bytes(16)
        recovery_salt = secrets.token_bytes(16)
        vault_key = secrets.token_bytes(KEY_BYTES)
        password_key = self._derive_key(password, password_salt, KDF_PARAMETERS)
        recovery_wrap_key = self._derive_key(
            generated_recovery_key,
            recovery_salt,
            KDF_PARAMETERS,
        )
        metadata = VaultMetadata(
            singleton_key="active",
            format_version=VAULT_FORMAT_VERSION,
            password_kdf=dict(KDF_PARAMETERS),
            recovery_kdf=dict(KDF_PARAMETERS),
            password_salt=password_salt,
            recovery_salt=recovery_salt,
            wrapped_with_password=self._wrap_key(
                vault_key,
                password_key,
                purpose="password",
            ),
            wrapped_with_recovery=self._wrap_key(
                vault_key,
                recovery_wrap_key,
                purpose="recovery",
            ),
            active=True,
            initialized_at=utc_now(),
        )
        self.session.add(metadata)
        self.session.flush()
        self._set_unlocked(vault_key)
        return VaultInitialization(metadata=metadata, recovery_key=generated_recovery_key)

    def unlock_with_password(self, password: str) -> None:
        """Unwrap the vault key with the management password."""
        self._unlock(password, mode="password")

    def unlock_with_recovery_key(self, recovery_key: str) -> None:
        """Unwrap the vault key with the one-time recovery credential."""
        self._unlock(recovery_key, mode="recovery")

    def encrypt_field(
        self,
        table_name: str,
        record_id: UUID | str,
        field_name: str,
        value: str | bytes,
        *,
        secret_version: int = 1,
    ) -> bytes:
        """Encrypt one secret with authenticated record and field context."""
        key = self._require_key()
        plaintext = value.encode("utf-8") if isinstance(value, str) else value
        aad = self._field_aad(table_name, record_id, field_name, secret_version)
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
        envelope = {
            "version": VAULT_FORMAT_VERSION,
            "nonce": self._encode(nonce),
            "ciphertext": self._encode(ciphertext),
        }
        return self._json_bytes(envelope)

    def decrypt_field(
        self,
        table_name: str,
        record_id: UUID | str,
        field_name: str,
        envelope: bytes,
        *,
        secret_version: int = 1,
    ) -> bytes:
        """Decrypt a field only when its record context and version match."""
        key = self._require_key()
        try:
            payload = json.loads(envelope.decode("utf-8"))
            if payload["version"] != VAULT_FORMAT_VERSION:
                raise VaultError("unsupported vault envelope")
            nonce = self._decode(payload["nonce"])
            ciphertext = self._decode(payload["ciphertext"])
            aad = self._field_aad(table_name, record_id, field_name, secret_version)
            return AESGCM(key).decrypt(nonce, ciphertext, aad)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise VaultError("invalid vault envelope") from exc

    def lock(self) -> None:
        """Erase the process-local reference to the unwrapped key."""
        self.runtime.lock()

    def fingerprint(self, value: str | bytes) -> str:
        """Return a stable non-reversible fingerprint for duplicate detection."""
        payload = value.encode("utf-8") if isinstance(value, str) else value
        return hashlib.sha256(payload).hexdigest()

    def verify_recovery_key(self, recovery_key: str) -> bool:
        """Validate a recovery key without exposing or persisting vault material."""
        metadata = self._get_metadata()
        if not recovery_key or metadata is None or not metadata.active:
            return False
        try:
            self._unwrap_vault_key(metadata, recovery_key)
        except (InvalidTag, KeyError, TypeError, ValueError, VaultError):
            return False
        return True

    def verify_wrapped_recovery_key(
        self,
        wrapped: bytes,
        salt: bytes,
        parameters: dict[str, Any],
        recovery_key: str,
    ) -> bytes:
        """Validate a serialized recovery wrap from a backup snapshot."""
        vault_key = self._unwrap_key(
            wrapped,
            self._derive_key(recovery_key, salt, parameters),
            purpose="recovery",
        )
        self._ensure_key_length(vault_key)
        return vault_key

    def encrypt_backup_payload(self, payload: bytes, recovery_key: str) -> dict[str, object]:
        """Encrypt a complete backup payload with an independent recovery-key wrap."""
        metadata = self._get_metadata()
        if metadata is None or not metadata.active:
            raise VaultError("vault is not initialized")
        self._unwrap_vault_key(metadata, recovery_key)
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(NONCE_BYTES)
        wrapping_key = self._derive_key(recovery_key, salt, BACKUP_KDF_PARAMETERS)
        aad = f"manager-vault-backup:v{VAULT_FORMAT_VERSION}".encode("ascii")
        ciphertext = AESGCM(wrapping_key).encrypt(nonce, payload, aad)
        return {
            "format_version": VAULT_FORMAT_VERSION,
            "kdf": dict(BACKUP_KDF_PARAMETERS),
            "salt": self._encode(salt),
            "nonce": self._encode(nonce),
            "ciphertext": self._encode(ciphertext),
        }

    def decrypt_backup_payload(
        self,
        envelope: dict[str, object],
        recovery_key: str,
    ) -> bytes:
        """Decrypt a backup package and map all cryptographic failures to one error."""
        try:
            if envelope["format_version"] != VAULT_FORMAT_VERSION:
                raise VaultError("unsupported backup format")
            parameters = envelope["kdf"]
            if not isinstance(parameters, dict):
                raise VaultError("invalid backup KDF")
            salt = self._decode(str(envelope["salt"]))
            nonce = self._decode(str(envelope["nonce"]))
            ciphertext = self._decode(str(envelope["ciphertext"]))
            wrapping_key = self._derive_key(recovery_key, salt, parameters)
            aad = f"manager-vault-backup:v{VAULT_FORMAT_VERSION}".encode("ascii")
            return AESGCM(wrapping_key).decrypt(nonce, ciphertext, aad)
        except (InvalidTag, KeyError, TypeError, ValueError, UnicodeDecodeError, VaultError) as exc:
            raise VaultUnlockError(GENERIC_UNLOCK_ERROR) from exc

    def _unlock(self, secret: str, *, mode: str) -> None:
        metadata = self._get_metadata()
        if not secret or metadata is None or not metadata.active:
            raise VaultUnlockError(GENERIC_UNLOCK_ERROR)
        try:
            if mode == "password":
                salt = metadata.password_salt
                parameters = metadata.password_kdf
                wrapped = metadata.wrapped_with_password
            else:
                salt = metadata.recovery_salt
                parameters = metadata.recovery_kdf
                wrapped = metadata.wrapped_with_recovery
            vault_key = self._unwrap_key(wrapped, self._derive_key(secret, salt, parameters), purpose=mode)
            self._ensure_key_length(vault_key)
        except (InvalidTag, KeyError, TypeError, ValueError, VaultError) as exc:
            self.lock()
            raise VaultUnlockError(GENERIC_UNLOCK_ERROR) from exc
        self._set_unlocked(vault_key)

    def _get_metadata(self) -> VaultMetadata | None:
        return self.session.scalar(
            select(VaultMetadata)
            .where(VaultMetadata.singleton_key == "active", VaultMetadata.active.is_(True))
            .limit(1)
        )

    def _require_key(self) -> bytes:
        key = self.runtime.get_key()
        if key is None:
            raise VaultUnlockError("vault is locked")
        return key

    def _set_unlocked(self, vault_key: bytes) -> None:
        self.runtime.set_key(vault_key)

    @staticmethod
    def _ensure_key_length(vault_key: bytes) -> None:
        """Reject malformed wrapped keys before they reach AES-GCM."""
        if len(vault_key) != KEY_BYTES:
            raise VaultError("invalid vault key")

    def _unwrap_vault_key(self, metadata: VaultMetadata, recovery_key: str) -> bytes:
        """Unwrap the persisted vault key for recovery-key validation."""
        return self.verify_wrapped_recovery_key(
            metadata.wrapped_with_recovery,
            metadata.recovery_salt,
            metadata.recovery_kdf,
            recovery_key,
        )

    @staticmethod
    def _new_recovery_key() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii").rstrip("=")

    @staticmethod
    def _derive_key(
        secret: str,
        salt: bytes,
        parameters: dict[str, Any],
    ) -> bytes:
        return hash_secret_raw(
            secret=secret.encode("utf-8"),
            salt=salt,
            time_cost=int(parameters["time_cost"]),
            memory_cost=int(parameters["memory_cost"]),
            parallelism=int(parameters["parallelism"]),
            hash_len=int(parameters["hash_len"]),
            type=Type.ID,
            version=int(parameters.get("version", 19)),
        )

    @classmethod
    def _wrap_key(cls, vault_key: bytes, wrapping_key: bytes, *, purpose: str) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        aad = f"manager-vault-wrap:v{VAULT_FORMAT_VERSION}:{purpose}".encode("ascii")
        return nonce + AESGCM(wrapping_key).encrypt(nonce, vault_key, aad)

    @classmethod
    def _unwrap_key(cls, wrapped: bytes, wrapping_key: bytes, *, purpose: str) -> bytes:
        if len(wrapped) <= NONCE_BYTES:
            raise VaultError("invalid wrapped key")
        nonce, ciphertext = wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:]
        aad = f"manager-vault-wrap:v{VAULT_FORMAT_VERSION}:{purpose}".encode("ascii")
        return AESGCM(wrapping_key).decrypt(nonce, ciphertext, aad)

    @staticmethod
    def _field_aad(
        table_name: str,
        record_id: UUID | str,
        field_name: str,
        secret_version: int,
    ) -> bytes:
        return (
            f"manager-vault-field:v{VAULT_FORMAT_VERSION}:{table_name}:"
            f"{record_id}:{field_name}:v{secret_version}"
        ).encode("utf-8")

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value.encode("ascii"))

    @staticmethod
    def _json_bytes(payload: dict[str, object]) -> bytes:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
