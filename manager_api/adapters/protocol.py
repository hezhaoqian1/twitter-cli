"""Typed contracts and redacted normalized results for external adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ContextManager, Protocol, TypeAlias

from ..models.accounts import AccountHealth

RedactedMap: TypeAlias = dict[str, object]

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "auth_token",
        "access_token",
        "cookie",
        "cookies",
        "cookie_string",
        "email_password",
        "password",
        "private_key",
        "refresh_token",
        "signature",
        "token",
        "totp",
        "mnemonic",
        "state",
        "code",
    }
)

_SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "network_error",
        "not_authenticated",
        "rate_limited",
        "timeout",
        "transient_error",
    }
)


def _normalized_key(key: str) -> str:
    """Normalize field names so common camelCase and snake_case secrets match."""
    return "".join(character for character in key.casefold() if character.isalnum())


def redact_value(value: object, *, key: str | None = None) -> object:
    """Return a recursively redacted value suitable for evidence or events."""
    if key is not None:
        normalized_key = _normalized_key(key)
        sensitive_keys = {_normalized_key(item) for item in _SENSITIVE_KEYS}
        if normalized_key in sensitive_keys or normalized_key.endswith("token"):
            return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


@dataclass(frozen=True, repr=False)
class AccountMaterial:
    """Imported account material held only for the duration of an adapter call."""

    handle: str
    password: str = field(default="", repr=False)
    totp: str = field(default="", repr=False)
    email: str = field(default="", repr=False)
    email_password: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    cookie: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        """Normalize the public handle while keeping all secret fields opaque."""
        normalized_handle = self.handle.strip().lstrip("@")
        if not normalized_handle:
            raise ValueError("account handle must not be empty")
        object.__setattr__(self, "handle", normalized_handle)

    @property
    def auth_token(self) -> str:
        """Expose the imported token under the existing client terminology."""
        return self.token

    def __repr__(self) -> str:
        """Show only the public account identity in diagnostics."""
        return f"AccountMaterial(handle={self.handle!r})"


@dataclass(frozen=True, repr=False)
class WalletMaterial:
    """Wallet identity plus transient private material for one workflow call."""

    address: str
    private_key: str = field(default="", repr=False)
    mnemonic: str = field(default="", repr=False)
    derivation_path: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Normalize the public address without retaining secret values in repr."""
        normalized_address = self.address.strip()
        if not normalized_address:
            raise ValueError("wallet address must not be empty")
        object.__setattr__(self, "address", normalized_address)

    def __repr__(self) -> str:
        """Show only the public wallet address in diagnostics."""
        return f"WalletMaterial(address={self.address!r})"


@dataclass(frozen=True, repr=False)
class OperationMaterial:
    """Provider operation input with a stable kind and optional external target."""

    kind: str
    target: str | None = field(default=None, repr=False)
    operation_ref: str | None = field(default=None, repr=False)
    metadata: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Reject empty operation kinds before they cross the adapter boundary."""
        normalized_kind = self.kind.strip().casefold()
        if not normalized_kind:
            raise ValueError("operation kind must not be empty")
        object.__setattr__(self, "kind", normalized_kind)

    def __repr__(self) -> str:
        """Show only the non-sensitive operation kind."""
        return f"OperationMaterial(kind={self.kind!r})"


@dataclass(frozen=True)
class AdapterEvidence:
    """Redacted provider evidence safe for task events and operator views."""

    code: str
    summary: str
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Sanitize nested provider fields before storing the evidence object."""
        object.__setattr__(
            self,
            "attributes",
            redact_value(dict(self.attributes)) if self.attributes else {},
        )

    def to_dict(self) -> RedactedMap:
        """Return a JSON-compatible redacted evidence mapping."""
        return {
            "code": self.code,
            "summary": self.summary,
            "attributes": dict(self.attributes),
        }


class ExternalStatus(str, Enum):
    """Normalized status values shared by all provider adapters."""

    ACCEPTED = "accepted"
    PENDING = "pending"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"

    @property
    def is_delayed(self) -> bool:
        """Return whether the task layer should wait and poll instead of failing."""
        return self in {ExternalStatus.ACCEPTED, ExternalStatus.PENDING, ExternalStatus.WAITING}

    @property
    def is_complete(self) -> bool:
        """Return whether replaying the external action is unnecessary."""
        return self in {ExternalStatus.SUCCEEDED, ExternalStatus.ALREADY_COMPLETED}


@dataclass(frozen=True)
class ExternalOperation:
    """Normalized result returned after starting an external operation."""

    operation_ref: str | None
    status: ExternalStatus
    evidence: AdapterEvidence


@dataclass(frozen=True)
class ExternalObservation:
    """Normalized result returned by a provider status read."""

    operation_ref: str | None
    status: ExternalStatus
    evidence: AdapterEvidence


ExternalPayload: TypeAlias = ExternalOperation | ExternalObservation | Mapping[str, object]


@dataclass(frozen=True)
class AccountHealthResult:
    """Normalized account verification result without session material."""

    health: AccountHealth
    handle: str | None
    user_id: str | None
    evidence: AdapterEvidence


class AdapterError(RuntimeError):
    """Typed adapter failure whose message and evidence are safe for task events."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool = False,
        evidence: AdapterEvidence | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.evidence = evidence or AdapterEvidence(code=code, summary=detail)
        super().__init__(f"{code}: {detail}")

    @classmethod
    def from_exception(
        cls,
        operation: str,
        error: BaseException,
        *,
        retryable: bool = False,
    ) -> AdapterError:
        """Translate provider exceptions without copying their message or secrets."""
        error_code = getattr(error, "error_code", None)
        candidate = str(error_code).strip().casefold() if isinstance(error_code, str) else ""
        code = candidate if candidate in _SAFE_PROVIDER_ERROR_CODES else "provider_error"
        return cls(
            f"{operation}_{code}",
            "external provider operation failed",
            retryable=retryable,
            evidence=AdapterEvidence(
                code=f"{operation}_{code}",
                summary="external provider operation failed",
                attributes={"exception_type": type(error).__name__},
            ),
        )


class TwitterClientProtocol(Protocol):
    """Subset of twitter-cli used by the X adapter."""

    def fetch_me(self) -> Any:
        """Return the authenticated profile."""

    def retweet(self, tweet_id: str) -> bool:
        """Create one repost and report whether the provider accepted it."""


TwitterClientFactory: TypeAlias = Callable[[AccountMaterial], TwitterClientProtocol]


class KredoWorkflowProtocol(Protocol):
    """One isolated Kredo browser workflow created for one adapter call."""

    def bind(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalPayload:
        """Start or observe the Kredo binding workflow."""

    def repost(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalPayload:
        """Start one Kredo repost workflow."""

    def claim(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalPayload:
        """Start one Kredo claim workflow."""

    def status(self, operation: OperationMaterial) -> ExternalPayload:
        """Read the current external state before replaying an action."""


KredoWorkflowFactory: TypeAlias = Callable[
    [OperationMaterial],
    ContextManager[KredoWorkflowProtocol],
]


class ExternalAdapterProtocol(Protocol):
    """Common high-level adapter shape used by the worker layer."""

    def status(self, operation: OperationMaterial) -> ExternalObservation:
        """Read one normalized external operation state."""
