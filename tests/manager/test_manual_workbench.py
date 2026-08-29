from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from manager_api.config import ManagerSettings
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.models.accounts import SocialAccount
from manager_api.schemas.bindings import ManualWorkbenchRequest
from manager_api.services.bindings import BindingService
from manager_api.services.imports import AccountImportService
from manager_api.services.manual_workbench import ManualWorkbenchService
from manager_api.services.vault import VaultRuntime, VaultService
from manager_api.services.wallets import WalletService


ACCOUNT_SECRET_VALUES = {
    "handle": "WorkbenchFixture",
    "password": "password-fixture",
    "totp": "totp-fixture",
    "email": "fixture@example.test",
    "email_password": "mail-fixture",
    "token": "token-fixture",
}
COOKIE_SECRET = base64.b64encode(
    json.dumps(
        [
            {
                "name": "auth_token",
                "value": "token-fixture",
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
            {
                "name": "ct0",
                "value": "csrf-fixture",
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            },
        ],
        separators=(",", ":"),
    ).encode("utf-8")
).decode("ascii")
PRIVATE_KEY_SECRET = "59c6995e998f97a5a0044966f094538b292d4874067f2f3f2d8f3f3e8f5f7f8f"


def _engine():
    """创建跨会话可复用的内存 SQLite。"""
    return create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_binding(session: Session, runtime: VaultRuntime) -> UUID:
    """写入一组已加密账号、钱包和 pending 绑定。"""
    vault = VaultService(session, runtime=runtime)
    vault.initialize("manager-password-fixture")
    account_row = "\t".join(
        (
            ACCOUNT_SECRET_VALUES["handle"],
            ACCOUNT_SECRET_VALUES["password"],
            ACCOUNT_SECRET_VALUES["totp"],
            ACCOUNT_SECRET_VALUES["email"],
            ACCOUNT_SECRET_VALUES["email_password"],
            ACCOUNT_SECRET_VALUES["token"],
            COOKIE_SECRET,
        )
    )
    AccountImportService(session, vault).commit(account_row, source_name="workbench.tsv")
    _, wallet_preview = WalletService(session, vault).commit_private_keys(
        PRIVATE_KEY_SECRET,
        label_prefix="workbench",
    )
    session.flush()
    account = session.scalars(
        select(SocialAccount).where(SocialAccount.normalized_handle == "workbenchfixture")
    ).one()
    wallet_id = next(
        decision.wallet_id for decision in wallet_preview.decisions if decision.wallet_id is not None
    )
    binding = BindingService(session).create_pending(account.id, wallet_id).binding
    session.commit()
    return binding.id


class FakePopen:
    """记录启动参数，避免测试真的打开浏览器。"""

    calls: list[dict[str, Any]] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        self.pid = 4242 + len(self.calls)
        self.calls.append({"command": command, **kwargs})


@pytest.fixture(autouse=True)
def _clear_popen_calls() -> Iterator[None]:
    """每个用例独立统计启动次数。"""
    FakePopen.calls.clear()
    yield
    FakePopen.calls.clear()


def test_manual_workbench_service_launches_headed_process_without_returning_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """半自动工作台会为单个 binding 启动有头浏览器，并只返回脱敏进程信息。"""
    from manager_api.services import manual_workbench

    engine = _engine()
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)
    with Session(engine) as session:
        binding_id = _seed_binding(session, runtime)
        monkeypatch.setattr(manual_workbench.subprocess, "Popen", FakePopen)
        launch = ManualWorkbenchService(session, VaultService(session, runtime=runtime)).launch(
            binding_id,
            timeout_seconds=30,
        )

    assert launch.process_id == 4242
    assert launch.binding_id == binding_id
    assert PRIVATE_KEY_SECRET not in json.dumps(launch.__dict__, default=str)
    call = FakePopen.calls[0]
    command = call["command"]
    assert Path(command[0]).stem.lower() == "uv"
    assert command[1:6] == ["run", "--with", "playwright", "--with", "eth-account"]
    assert "--headed" in command
    assert "--keep-open" in command
    assert "--no-bind-twitter" in command
    assert "--no-wait-task-state" in command
    assert "--repost" not in command
    assert "--repost-target" not in command
    assert call["env"]["KREDO_PRIVATE_KEY"] == PRIVATE_KEY_SECRET
    assert call["cwd"].name == "twitter-cli"


def test_manual_workbench_resolves_uv_from_windows_user_bin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Windows 后台 API 没有继承 PATH 时仍能找到 uv。"""
    from manager_api.services import manual_workbench

    uv_path = tmp_path / ".local" / "bin" / "uv.exe"
    uv_path.parent.mkdir(parents=True)
    uv_path.write_bytes(b"fixture")
    monkeypatch.setattr(manual_workbench.shutil, "which", lambda _name: None)
    monkeypatch.setattr(manual_workbench.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(manual_workbench.os, "name", "nt", raising=False)

    assert manual_workbench._resolve_uv_executable() == str(uv_path)


def test_manual_workbench_service_rejects_more_than_ten() -> None:
    """服务层硬限制单次最多 10 个浏览器，避免误点拖垮本机。"""
    engine = _engine()
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)
    with Session(engine) as session:
        service = ManualWorkbenchService(session, VaultService(session, runtime=runtime))
        with pytest.raises(ValueError, match="at most 10"):
            service.launch_many([], limit=11)


def test_manual_workbench_bulk_launch_waits_between_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量工作台启动之间会等待一秒，降低外部请求峰值。"""
    from manager_api.services import manual_workbench

    engine = _engine()
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)
    with Session(engine) as session:
        binding_id = _seed_binding(session, runtime)
        service = ManualWorkbenchService(session, VaultService(session, runtime=runtime))
        monkeypatch.setattr(service, "launch", lambda *args, **kwargs: None)
        sleep_calls: list[float] = []
        monkeypatch.setattr(manual_workbench.time, "sleep", sleep_calls.append)

        service.launch_many([binding_id, binding_id], limit=2)

    assert sleep_calls == [manual_workbench.WORKBENCH_LAUNCH_INTERVAL_SECONDS]


def test_manual_workbench_detaches_process_with_windows_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 本地运行时隐藏终端，同时保留有头浏览器窗口。"""
    from manager_api.services import manual_workbench

    monkeypatch.setattr(manual_workbench.os, "name", "nt", raising=False)

    kwargs = manual_workbench._detached_process_kwargs()

    assert kwargs == {"creationflags": 0x08000200}


def test_manual_workbench_detaches_process_with_posix_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS/Linux 本地运行时保持现有 start_new_session 行为。"""
    from manager_api.services import manual_workbench

    monkeypatch.setattr(manual_workbench.os, "name", "posix", raising=False)

    kwargs = manual_workbench._detached_process_kwargs()

    assert kwargs == {"start_new_session": True}


def test_manual_workbench_api_bulk_launch_uses_env_unlock_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 批量入口可用服务端密码解锁，并且响应中没有账号或钱包密钥材料。"""
    from manager_api.api.routers.bindings import launch_manual_workbenches
    from manager_api.services import manual_workbench

    engine = _engine()
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)
    with Session(engine) as session:
        binding_id = _seed_binding(session, runtime)
    runtime.lock()

    settings = ManagerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost/0",
        session_secret="test-session-secret-123",
        worker_vault_password=SecretStr("manager-password-fixture"),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    monkeypatch.setattr(manual_workbench.subprocess, "Popen", FakePopen)

    with Session(engine) as session:
        response = launch_manual_workbenches(
            ManualWorkbenchRequest(
                binding_ids=[binding_id],
                limit=10,
                timeout_seconds=30,
            ),
            request=request,
            session=session,
            vault=VaultService(session, runtime=runtime),
        )

    body = response.model_dump(mode="json")
    rendered = json.dumps(body)
    assert body["launched"] == 1
    assert body["items"][0]["binding_id"] == str(binding_id)
    assert "process_id" in body["items"][0]
    assert ACCOUNT_SECRET_VALUES["password"] not in rendered
    assert ACCOUNT_SECRET_VALUES["totp"] not in rendered
    assert ACCOUNT_SECRET_VALUES["token"] not in rendered
    assert COOKIE_SECRET not in rendered
    assert PRIVATE_KEY_SECRET not in rendered
    assert FakePopen.calls[0]["env"]["KREDO_PRIVATE_KEY"] == PRIVATE_KEY_SECRET


def test_manual_workbench_routes_are_registered() -> None:
    """OpenAPI 暴露单个和批量工作台入口，供 UI 调用。"""
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    paths = app.openapi()["paths"]

    assert "/api/bindings/{binding_id}/manual-workbench" in paths
    assert "/api/bindings/manual-workbenches" in paths
