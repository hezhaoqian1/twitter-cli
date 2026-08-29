"""Launch headed browser workbenches for semi-automatic Kredo operations."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ..adapters.kredo_browser_workflow import _cookie_payload
from ..adapters.protocol import AccountMaterial, WalletMaterial
from ..models.accounts import AccountSecret
from ..models.bindings import AccountWalletBinding
from ..models.wallets import WalletSecret
from .bindings import BindingNotFoundError, BindingService
from .vault import VaultService

WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS_CREATE_NO_WINDOW = 0x08000000
WORKBENCH_LAUNCH_INTERVAL_SECONDS = 1.0


def _resolve_uv_executable() -> str:
    """解析后台工作台子进程需要的 uv 可执行文件路径。"""
    configured = os.environ.get("UV_EXECUTABLE", "").strip()
    if configured:
        return configured

    resolved = shutil.which("uv")
    if resolved:
        return resolved

    if os.name == "nt":
        user_uv = Path.home() / ".local" / "bin" / "uv.exe"
        if user_uv.is_file():
            return str(user_uv)

    # 让非 Windows 环境继续依赖系统 PATH，并保留原有错误信息。
    return "uv"


def _detached_process_kwargs() -> dict[str, Any]:
    """按当前系统返回子浏览器进程的分离参数。"""
    if os.name == "nt":
        return {
            "creationflags": getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                WINDOWS_CREATE_NEW_PROCESS_GROUP,
            )
            | getattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW)
        }
    return {"start_new_session": True}


@dataclass(frozen=True)
class ManualWorkbenchLaunch:
    """脱敏返回值：只告诉前端本机浏览器工作台是否已启动。"""

    binding_id: UUID
    process_id: int
    screenshot: str


class ManualWorkbenchService:
    """Materialize one binding into a local headed browser process."""

    def __init__(self, session: Session, vault: VaultService) -> None:
        self.session = session
        self.vault = vault

    def launch(
        self,
        binding_id: UUID,
        *,
        timeout_seconds: int = 45,
    ) -> ManualWorkbenchLaunch:
        """启动工作台：主页面停在 Kredo，X 页面由操作员手动打开并保留。"""
        account, wallet = self._materials(binding_id)
        repo_root = Path(__file__).resolve().parents[2]
        workdir = Path(tempfile.mkdtemp(prefix="kredo-manual-workbench-"))
        os.chmod(workdir, 0o700)
        cookie_file = workdir / "x-cookies.json"
        cookie_file.write_text(
            json.dumps(_cookie_payload(account), ensure_ascii=False),
            encoding="utf-8",
        )
        artifact_dir = repo_root / "artifacts/kredo-workbench"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        screenshot = artifact_dir / f"workbench-{str(binding_id)[:8]}-{uuid4().hex[:10]}.png"
        env = {
            **os.environ,
            "KREDO_PRIVATE_KEY": wallet.private_key,
        }
        command = [
            _resolve_uv_executable(),
            "run",
            "--with",
            "playwright",
            "--with",
            "eth-account",
            "python",
            "scripts/kredo_wallet_login_probe.py",
            "--headed",
            "--keep-open",
            "--no-bind-twitter",
            "--no-wait-task-state",
            "--twitter-cookie-file",
            str(cookie_file),
            "--screenshot",
            str(screenshot),
            "--timeout",
            str(timeout_seconds),
        ]
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_detached_process_kwargs(),
        )
        return ManualWorkbenchLaunch(
            binding_id=binding_id,
            process_id=process.pid,
            screenshot=str(screenshot),
        )

    def launch_many(
        self,
        binding_ids: list[UUID],
        *,
        limit: int = 10,
        timeout_seconds: int = 45,
    ) -> list[ManualWorkbenchLaunch]:
        """按当前选择顺序启动多个独立浏览器，最多一次 10 个。"""
        if limit < 1:
            raise ValueError("limit must be positive")
        if limit > 10:
            raise ValueError("limit must be at most 10")
        launches: list[ManualWorkbenchLaunch] = []
        selected_binding_ids = binding_ids[:limit]
        for index, binding_id in enumerate(selected_binding_ids):
            launches.append(
                self.launch(
                    binding_id,
                    timeout_seconds=timeout_seconds,
                )
            )
            if index + 1 < len(selected_binding_ids):
                # 批量启动之间留出间隔，避免多个浏览器同时访问 Kredo。
                time.sleep(WORKBENCH_LAUNCH_INTERVAL_SECONDS)
        return launches

    def _materials(self, binding_id: UUID) -> tuple[AccountMaterial, WalletMaterial]:
        """从 binding 读取当前账号和钱包，并只在本函数内解密材料。"""
        try:
            view = BindingService(self.session).get(binding_id)
        except BindingNotFoundError:
            raise
        binding = self.session.get(AccountWalletBinding, view.binding.id)
        if binding is None or binding.account is None or binding.wallet is None:
            raise BindingNotFoundError("binding not found")
        account_secret = binding.account.secret
        wallet_secret = binding.wallet.secret
        if account_secret is None or wallet_secret is None:
            raise ValueError("binding is missing encrypted material")
        account = AccountMaterial(
            handle=binding.account.handle,
            **self._decrypt_account_secret(account_secret),
        )
        wallet = WalletMaterial(
            address=binding.wallet.address,
            private_key=self._decrypt_wallet_secret(wallet_secret),
            derivation_path=binding.wallet.derivation_path,
        )
        return account, wallet

    def _decrypt_wallet_secret(self, secret: WalletSecret) -> str:
        """解密单个钱包私钥，不写日志也不返回给前端。"""
        return self.vault.decrypt_field(
            "wallet_secrets",
            secret.id,
            "private_key",
            secret.envelope,
        ).decode("utf-8")

    def _decrypt_account_secret(self, secret: AccountSecret) -> dict[str, str]:
        """解密账号 Cookie/token 等字段，仅供本机浏览器进程使用。"""
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
            envelope = base64.urlsafe_b64decode(encoded.encode("ascii"))
            values[field_name] = self.vault.decrypt_field(
                "account_secrets",
                secret.id,
                field_name,
                envelope,
            ).decode("utf-8")
        email_envelope = encoded_fields.get("email")
        if isinstance(email_envelope, str):
            values["email"] = self.vault.decrypt_field(
                "account_secrets",
                secret.id,
                "email",
                base64.urlsafe_b64decode(email_envelope.encode("ascii")),
            ).decode("utf-8")
        return values
