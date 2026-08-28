"""Materialize one task and map adapter results back to durable task state."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from ..adapters.protocol import (
    AccountHealthResult,
    AccountMaterial,
    AdapterError,
    ExternalOperation,
    ExternalObservation,
    OperationMaterial,
    WalletMaterial,
)
from ..models.accounts import AccountHealth, AccountSecret, SocialAccount
from ..models.tasks import TaskJob, TaskKind, TaskState
from ..models.wallets import Wallet
from ..task_outcomes import WorkerOutcome
from .bindings import BindingService
from .vault import VaultError, VaultService, VaultUnlockError
from ..db.base import utc_now


class XTaskAdapter(Protocol):
    """最小化 X 适配器契约，便于生产实现和合成测试替换。"""

    def verify_account(self, account: AccountMaterial) -> AccountHealthResult:
        """校验账号会话并返回脱敏的账号状态。"""

    def repost(
        self,
        account: AccountMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """执行一次幂等的 X 转发操作。"""


class KredoTaskAdapter(Protocol):
    """最小化 Kredo 适配器契约，隔离浏览器或 HTTP 实现。"""

    def bind(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """发起账号与钱包绑定。"""

    def claim(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> ExternalOperation:
        """领取当前账号地址配对对应的奖励。"""

    def status(self, operation: OperationMaterial):
        """读取外部任务的最终状态。"""

    def account_summary(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ):
        """读取 Kredo Points 与 HSK 摘要，不触发外部写操作。"""


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution timing defaults kept independent from provider implementations."""

    poll_delay_seconds: int = 15


class TaskExecutionService:
    """Decrypt one pair for one call, execute it, and immediately discard materials."""

    def __init__(
        self,
        session: Session,
        *,
        vault: VaultService,
        x_adapter: XTaskAdapter,
        kredo_adapter: KredoTaskAdapter,
        config: ExecutionConfig | None = None,
    ) -> None:
        self.session = session
        self.vault = vault
        self.x_adapter = x_adapter
        self.kredo_adapter = kredo_adapter
        self.config = config or ExecutionConfig()

    def handle(self, job: TaskJob) -> WorkerOutcome:
        """Execute one leased job through the typed adapter boundary."""
        try:
            account = self._account_material(job.social_account_id)
            if job.kind is TaskKind.VERIFY_ACCOUNT:
                return self._verify(job, account)

            wallet = self._wallet_material(job.wallet_id)
            operation = OperationMaterial(
                kind=job.kind.value,
                target=job.external_target,
                operation_ref=job.external_operation_ref,
                metadata={
                    "task_id": str(job.id),
                    "binding_id": str(job.binding_id) if job.binding_id else None,
                },
            )
            if job.kind is TaskKind.BIND:
                return self._bind(job, account, wallet, operation)
            if job.kind is TaskKind.REPOST:
                return self._repost(job, account, wallet, operation)
            if job.kind is TaskKind.CLAIM:
                return self._claim(job, account, wallet, operation)
            if job.kind is TaskKind.BALANCE_SYNC:
                return self._sync_balance(job, account, wallet, operation)
            raise ValueError(f"unsupported execution kind: {job.kind.value}")
        except VaultUnlockError:
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary="Vault is locked",
                failure_code="vault_locked",
            )
        except VaultError:
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary="Secret material could not be decrypted",
                failure_code="secret_material_invalid",
            )
        except AdapterError as error:
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary=error.evidence.summary,
                failure_code=error.code,
            )
        except (LookupError, ValueError) as error:
            # 不把数据库字段或解密内容写入任务事件，只保留稳定错误分类。
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary="Task material is incomplete",
                failure_code=self._failure_code(error),
            )

    def _verify(self, job: TaskJob, account: AccountMaterial) -> WorkerOutcome:
        """更新账号健康度，并把无效会话转换成可见的失败任务。"""
        result = self.x_adapter.verify_account(account)
        record = self._require_account(job.social_account_id)
        record.health = result.health
        record.last_verified_at = utc_now()
        self.session.flush()
        if result.health is AccountHealth.INVALID:
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary=result.evidence.summary,
                failure_code=result.evidence.code,
            )
        return WorkerOutcome(
            state=TaskState.SUCCEEDED,
            summary=result.evidence.summary,
            external_operation_ref=result.user_id,
        )

    def _bind(
        self,
        job: TaskJob,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> WorkerOutcome:
        """绑定成功时只确认当前 pending 记录，避免改变历史配对。"""
        result = self.kredo_adapter.bind(account, wallet, operation)
        if result.status.is_complete:
            binding_id = self._require_binding_id(job)
            reference = result.operation_ref or f"kredo:{binding_id}"
            BindingService(self.session).confirm(binding_id, reference)
        return self._operation_outcome(result)

    def _repost(
        self,
        job: TaskJob,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> WorkerOutcome:
        """先执行 X 转发，再读取 Kredo 延迟校验状态，避免重复提交。"""
        if job.external_operation_ref:
            # 轮询任务已经有外部引用，只做只读状态检查，不再次触发转发。
            kredo_result = self.kredo_adapter.status(operation)
            return self._operation_outcome(kredo_result)

        x_result = self.x_adapter.repost(account, operation)
        if not x_result.status.is_complete:
            return self._operation_outcome(x_result)

        # Kredo 的状态传播可能慢于 X 接口；只读状态用于决定是否继续轮询。
        kredo_result = self.kredo_adapter.status(operation)
        if kredo_result.status.is_complete:
            return self._operation_outcome(kredo_result)
        return self._operation_outcome(kredo_result)

    def _claim(
        self,
        job: TaskJob,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> WorkerOutcome:
        """领取奖励并保留外部重复提交保护。"""
        result = self.kredo_adapter.claim(account, wallet, operation)
        return self._operation_outcome(result)

    def _sync_balance(
        self,
        job: TaskJob,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> WorkerOutcome:
        """Read and persist one binding balance snapshot through the adapter."""
        from .balances import BalanceService

        if job.binding_id is None:
            raise LookupError("binding id missing")
        try:
            result = self.kredo_adapter.account_summary(account, wallet, operation)
        except AdapterError as error:
            BalanceService(self.session).sync_error(job.binding_id, error.code)
            return WorkerOutcome(
                state=TaskState.FAILED,
                summary=error.evidence.summary,
                failure_code=error.code,
            )
        BalanceService(self.session).sync_success(job.binding_id, result)
        return WorkerOutcome(
            state=TaskState.SUCCEEDED,
            summary=result.evidence.summary,
        )

    def _operation_outcome(
        self,
        result: ExternalOperation | ExternalObservation,
    ) -> WorkerOutcome:
        """Map normalized provider states into the durable worker state machine."""
        if result.status.is_complete:
            return WorkerOutcome(
                state=TaskState.SUCCEEDED,
                summary=result.evidence.summary,
                external_operation_ref=result.operation_ref,
            )
        if result.status.is_delayed:
            return WorkerOutcome(
                state=TaskState.WAITING_EXTERNAL_VALIDATION,
                summary=result.evidence.summary,
                external_operation_ref=result.operation_ref,
                next_poll_at=utc_now() + timedelta(seconds=self.config.poll_delay_seconds),
            )
        return WorkerOutcome(
            state=TaskState.FAILED,
            summary=result.evidence.summary,
            external_operation_ref=result.operation_ref,
            failure_code=result.evidence.code,
        )

    def _account_material(self, account_id: UUID | None) -> AccountMaterial:
        """Decrypt one account envelope into short-lived adapter input."""
        account = self._require_account(account_id)
        secret = account.secret
        if secret is None:
            raise LookupError("account secret missing")
        values = self._decrypt_account_secret(secret)
        return AccountMaterial(handle=account.handle, **values)

    def _wallet_material(self, wallet_id: UUID | None) -> WalletMaterial:
        """Decrypt only the current private key needed by this wallet call."""
        if wallet_id is None:
            raise LookupError("wallet id missing")
        wallet = self.session.get(Wallet, wallet_id)
        if wallet is None or wallet.archived_at is not None or wallet.state != "active":
            raise LookupError("wallet missing")
        secret = wallet.secret
        if secret is None:
            raise LookupError("wallet secret missing")
        private_key = self.vault.decrypt_field(
            "wallet_secrets",
            secret.id,
            "private_key",
            secret.envelope,
        ).decode("utf-8")
        return WalletMaterial(
            address=wallet.address,
            private_key=private_key,
            derivation_path=wallet.derivation_path,
        )

    def _decrypt_account_secret(self, secret: AccountSecret) -> dict[str, str]:
        """Decode the field envelope without ever returning ciphertext to callers."""
        try:
            encoded_fields = json.loads(secret.envelope.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("account secret envelope is malformed") from error
        if not isinstance(encoded_fields, dict):
            raise ValueError("account secret envelope is malformed")
        values: dict[str, str] = {}
        for field_name in ("password", "totp", "email_password", "token", "cookie"):
            encoded = encoded_fields.get(field_name)
            if not isinstance(encoded, str):
                raise ValueError("account secret field is missing")
            try:
                envelope = base64.urlsafe_b64decode(encoded.encode("ascii"))
                values[field_name] = self.vault.decrypt_field(
                    "account_secrets",
                    secret.id,
                    field_name,
                    envelope,
                ).decode("utf-8")
            except (ValueError, UnicodeDecodeError, UnicodeEncodeError) as error:
                raise ValueError("account secret envelope is malformed") from error
        email_envelope = encoded_fields.get("email")
        if isinstance(email_envelope, str):
            values["email"] = self.vault.decrypt_field(
                "account_secrets",
                secret.id,
                "email",
                base64.urlsafe_b64decode(email_envelope.encode("ascii")),
            ).decode("utf-8")
        return values

    def _require_account(self, account_id: UUID | None) -> SocialAccount:
        """Load one active account and keep missing-resource errors generic."""
        if account_id is None:
            raise LookupError("account id missing")
        account = self.session.get(SocialAccount, account_id)
        if account is None or account.archived_at is not None:
            raise LookupError("account missing")
        return account

    @staticmethod
    def _require_binding_id(job: TaskJob) -> UUID:
        """Require a binding scope before confirming an external bind."""
        if job.binding_id is None:
            raise LookupError("binding id missing")
        return job.binding_id

    @staticmethod
    def _failure_code(error: BaseException) -> str:
        """Convert local material errors into stable redacted event codes."""
        message = str(error).casefold()
        if "secret" in message or "envelope" in message:
            return "secret_material_invalid"
        if "wallet" in message:
            return "wallet_material_invalid"
        if "account" in message:
            return "account_material_invalid"
        return "task_material_invalid"
