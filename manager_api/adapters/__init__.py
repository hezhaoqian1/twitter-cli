"""External integration adapters and their normalized contracts."""

from .kredo_adapter import KredoAdapter
from .kredo_browser_workflow import KredoBrowserWorkflow, kredo_workflow_factory
from .protocol import (
    AccountHealthResult,
    AccountMaterial,
    AdapterError,
    AdapterEvidence,
    KredoBalanceResult,
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
    "KredoBalanceResult",
    "ExternalAdapterProtocol",
    "ExternalObservation",
    "ExternalOperation",
    "ExternalStatus",
    "KredoAdapter",
    "KredoBrowserWorkflow",
    "KredoWorkflowFactory",
    "KredoWorkflowProtocol",
    "OperationMaterial",
    "TwitterClientFactory",
    "TwitterClientProtocol",
    "WalletMaterial",
    "XAdapter",
    "build_twitter_client_factory",
    "kredo_workflow_factory",
    "redact_value",
]
