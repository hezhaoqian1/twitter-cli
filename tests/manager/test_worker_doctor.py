from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_doctor():
    """加载 worker doctor 脚本，不执行命令入口。"""
    path = Path("scripts/manager_worker_doctor.py")
    spec = importlib.util.spec_from_file_location("manager_worker_doctor", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_factory_check_reports_missing_env_without_secret(monkeypatch) -> None:
    """缺少 factory 环境变量时输出稳定错误。"""
    doctor = _load_doctor()
    monkeypatch.delenv("MANAGER_KREDO_WORKFLOW_FACTORY", raising=False)

    result = doctor._factory_check()

    assert result.ok is False
    assert result.name == "kredo_factory"
    assert "MANAGER_KREDO_WORKFLOW_FACTORY" in result.detail


def test_factory_check_loads_live_factory(monkeypatch) -> None:
    """真实 factory 路径能被 worker doctor 解析。"""
    doctor = _load_doctor()
    spec = "manager_api.adapters.kredo_browser_workflow:kredo_workflow_factory"
    monkeypatch.setenv("MANAGER_KREDO_WORKFLOW_FACTORY", spec)

    result = doctor._factory_check()

    assert result.ok is True
    assert result.detail == spec


def test_factory_check_loads_settings_factory_without_exported_env(monkeypatch) -> None:
    """worker doctor 命令入口可以复用 .env.manager 加载到 settings 的 factory。"""
    doctor = _load_doctor()
    spec = "manager_api.adapters.kredo_browser_workflow:kredo_workflow_factory"
    monkeypatch.delenv("MANAGER_KREDO_WORKFLOW_FACTORY", raising=False)

    settings = doctor.ManagerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost/0",
        session_secret="test-session-secret-123",
        manager_kredo_workflow_factory=spec,
    )
    result = doctor._factory_check(settings)

    assert result.ok is True
    assert result.detail == spec


def test_factory_check_loads_synthetic_factory(monkeypatch) -> None:
    """本地 synthetic factory 可作为 worker dry-run 入口。"""
    doctor = _load_doctor()
    monkeypatch.setenv("MANAGER_KREDO_WORKFLOW_FACTORY", doctor.SYNTHETIC_KREDO_FACTORY)

    result = doctor._factory_check()

    assert result.ok is True
    assert result.detail == doctor.SYNTHETIC_KREDO_FACTORY


def test_x_factory_check_accepts_default_and_synthetic(monkeypatch) -> None:
    """X adapter factory 未配置或配置为 synthetic 时都能完成检查。"""
    doctor = _load_doctor()
    monkeypatch.delenv("MANAGER_X_ADAPTER_FACTORY", raising=False)

    default_result = doctor._x_factory_check()
    assert default_result.ok is True
    assert default_result.detail == "default X adapter"

    monkeypatch.setenv("MANAGER_X_ADAPTER_FACTORY", doctor.SYNTHETIC_X_FACTORY)
    synthetic_result = doctor._x_factory_check()
    assert synthetic_result.ok is True
    assert synthetic_result.detail == doctor.SYNTHETIC_X_FACTORY


def test_settings_check_does_not_echo_environment_values(monkeypatch) -> None:
    """配置错误时只返回错误类型，不回显连接串或密钥。"""
    doctor = _load_doctor()
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setenv("PGUSER", "user")
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/0")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "short")

    result = doctor._settings_check()

    assert result.ok is False
    assert "secret" not in result.detail
    assert "example.invalid" not in result.detail
