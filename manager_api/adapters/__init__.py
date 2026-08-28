"""External integration adapters and their normalized contracts."""

from .kredo_adapter import KredoAdapter
from .protocol import (
    AccountHealthResult,
    AccountMaterial,
    AdapterError,
    AdapterEvidence,
    ExternalAdapterProtocol,
    ExternalObservation,
    ExternalOperation,
    ExternalStatus,
    KredoWorkflowFactory,
    KredoWorkflowProtocol,
    OperationMaterial,
    TwitterClientFactory,
    TwitterClientProtocol,
    WalletMaterial,
    redact_value,
)
from .x_adapter import XAdapter, build_twitter_client_factory

__all__ = [
    "AccountHealthResult",
    "AccountMaterial",
    "AdapterError",
    "AdapterEvidence",
    "ExternalAdapterProtocol",
    "ExternalObservation",
    "ExternalOperation",
    "ExternalStatus",
    "KredoAdapter",
    "KredoWorkflowFactory",
    "KredoWorkflowProtocol",
    "OperationMaterial",
    "TwitterClientFactory",
    "TwitterClientProtocol",
    "WalletMaterial",
    "XAdapter",
    "build_twitter_client_factory",
    "redact_value",
]
