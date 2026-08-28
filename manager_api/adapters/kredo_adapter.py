"""Injected Kredo workflow adapter with per-call browser-context ownership."""

from __future__ import annotations

from collections.abc import Mapping

from .protocol import (
    AccountMaterial,
    AdapterError,
    AdapterEvidence,
    ExternalObservation,
    ExternalOperation,
    ExternalPayload,
    ExternalStatus,
    KredoWorkflowFactory,
    OperationMaterial,
    WalletMaterial,
)

_STATUS_ALIASES: dict[str, ExternalStatus] = {
    "accepted": ExternalStatus.PENDING,
    "already_bound": ExternalStatus.ALREADY_COMPLETED,
    "already_claimed": ExternalStatus.ALREADY_COMPLETED,
    "already_completed": ExternalStatus.ALREADY_COMPLETED,
    "already_reposted": ExternalStatus.ALREADY_COMPLETED,
    "bound": ExternalStatus.SUCCEEDED,
    "claimed": ExternalStatus.SUCCEEDED,
    "complete": ExternalStatus.SUCCEEDED,
    "completed": ExternalStatus.SUCCEEDED,
    "error": ExternalStatus.FAILED,
    "failed": ExternalStatus.FAILED,
    "not_found": ExternalStatus.NOT_FOUND,
    "pending": ExternalStatus.PENDING,
    "pending_bind": ExternalStatus.PENDING,
    "pending_claim": ExternalStatus.PENDING,
    "pending_repost": ExternalStatus.PENDING,
    "pending_validation": ExternalStatus.PENDING,
    "processing": ExternalStatus.WAITING,
    "in_progress": ExternalStatus.WAITING,
    "queued": ExternalStatus.WAITING,
    "rejected": ExternalStatus.FAILED,
    "reposted": ExternalStatus.SUCCEEDED,
    "success": ExternalStatus.SUCCEEDED,
    "succeeded": ExternalStatus.SUCCEEDED,
    "syncing": ExternalStatus.WAITING,
    "unbound": ExternalStatus.PENDING,
    "waiting": ExternalStatus.WAITING,
    "waiting_external_validation": ExternalStatus.WAITING,
}


class KredoAdapter:
    """Run each Kredo operation inside a fresh factory-owned workflow context."""

    def __init__(self, workflow_factory: KredoWorkflowFactory) -> None:
        self._workflow_factory = workflow_factory

    def bind(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """Start binding and preserve delayed provider responses as pending."""
        return self._run_action(
            account,
            wallet,
            operation,
            action_name="bind",
            preflight=False,
        )

    def repost(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """Check existing repost state before starting a new Kredo action."""
        return self._run_action(
            account,
            wallet,
            operation,
            action_name="repost",
            preflight=True,
        )

    def claim(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """Check existing claim state before starting a new Kredo action."""
        return self._run_action(
            account,
            wallet,
            operation,
            action_name="claim",
            preflight=True,
        )

    def status(self, operation: OperationMaterial) -> ExternalObservation:
        """Read one normalized Kredo state in an isolated workflow context."""
        try:
            with self._workflow_factory(operation) as workflow:
                payload = workflow.status(operation)
                return self._normalize_observation(
                    payload,
                    fallback_ref=operation.operation_ref,
                )
        except AdapterError:
            raise
        except Exception as error:
            raise AdapterError.from_exception("status", error, retryable=True) from error

    def _run_action(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
        *,
        action_name: str,
        preflight: bool,
    ) -> ExternalOperation:
        """Execute one action and close its workflow context before returning."""
        try:
            with self._workflow_factory(operation) as workflow:
                if preflight:
                    # 每次重试前都在当前独立上下文读取外部状态。
                    current = self._normalize_observation(
                        workflow.status(operation),
                        fallback_ref=operation.operation_ref,
                    )
                    if current.status.is_complete:
                        return ExternalOperation(
                            operation_ref=current.operation_ref,
                            status=ExternalStatus.ALREADY_COMPLETED,
                            evidence=AdapterEvidence(
                                code=f"{action_name}_already_completed",
                                summary=f"Kredo {action_name} already completed",
                                attributes=current.evidence.to_dict(),
                            ),
                        )
                    if current.status.is_delayed:
                        return ExternalOperation(
                            operation_ref=current.operation_ref,
                            status=current.status,
                            evidence=current.evidence,
                        )
                payload = getattr(workflow, action_name)(account, wallet, operation)
                return self._normalize_operation(
                    payload,
                    fallback_ref=operation.operation_ref,
                    action_name=action_name,
                )
        except AdapterError:
            raise
        except Exception as error:
            raise AdapterError.from_exception(action_name, error, retryable=True) from error

    @classmethod
    def _normalize_operation(
        cls,
        payload: ExternalPayload,
        *,
        fallback_ref: str | None,
        action_name: str,
    ) -> ExternalOperation:
        """Map provider action output to the worker-facing operation contract."""
        if isinstance(payload, ExternalOperation):
            return payload
        if isinstance(payload, ExternalObservation):
            return ExternalOperation(
                operation_ref=payload.operation_ref,
                status=payload.status,
                evidence=payload.evidence,
            )
        status, operation_ref, evidence = cls._normalize_payload(
            payload,
            fallback_ref=fallback_ref,
            action_name=action_name,
        )
        return ExternalOperation(
            operation_ref=operation_ref,
            status=status,
            evidence=evidence,
        )

    @classmethod
    def _normalize_observation(
        cls,
        payload: ExternalPayload,
        *,
        fallback_ref: str | None,
    ) -> ExternalObservation:
        """Map provider status output to a normalized external observation."""
        if isinstance(payload, ExternalObservation):
            return payload
        if isinstance(payload, ExternalOperation):
            return ExternalObservation(
                operation_ref=payload.operation_ref,
                status=payload.status,
                evidence=payload.evidence,
            )
        status, operation_ref, evidence = cls._normalize_payload(
            payload,
            fallback_ref=fallback_ref,
            action_name="status",
        )
        return ExternalObservation(
            operation_ref=operation_ref,
            status=status,
            evidence=evidence,
        )

    @staticmethod
    def _normalize_payload(
        payload: Mapping[str, object],
        *,
        fallback_ref: str | None,
        action_name: str,
    ) -> tuple[ExternalStatus, str | None, AdapterEvidence]:
        """Normalize a mapping while retaining only redacted evidence fields."""
        if not isinstance(payload, Mapping):
            raise AdapterError(
                f"{action_name}_invalid_response",
                "external provider returned an invalid payload",
            )
        # Kredo API responses may wrap the provider result in a data envelope.
        nested_payload = payload.get("data")
        if isinstance(nested_payload, Mapping) and (
            "status" in nested_payload
            or "state" in nested_payload
            or "operation_ref" in nested_payload
            or "reference" in nested_payload
        ):
            payload = nested_payload
        raw_status = payload.get("status", payload.get("state", "unknown"))
        if isinstance(raw_status, ExternalStatus):
            status = raw_status
        else:
            status_key = str(raw_status).strip().casefold().replace("-", "_").replace(" ", "_")
            status = _STATUS_ALIASES.get(status_key, ExternalStatus.UNKNOWN)
        raw_ref = payload.get("operation_ref", payload.get("reference", payload.get("id")))
        operation_ref = str(raw_ref).strip() if raw_ref is not None else fallback_ref
        if operation_ref == "":
            operation_ref = fallback_ref
        raw_evidence = payload.get("evidence")
        attributes = dict(raw_evidence) if isinstance(raw_evidence, Mapping) else {
            key: value
            for key, value in payload.items()
            if key not in {"status", "state", "operation_ref", "reference", "id", "evidence"}
        }
        evidence = AdapterEvidence(
            code=f"{action_name}_{status.value}",
            summary=f"Kredo {action_name} status normalized",
            attributes=attributes,
        )
        return status, operation_ref, evidence
