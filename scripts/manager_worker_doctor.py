#!/usr/bin/env python3
"""Check manager worker runtime wiring before dispatching real jobs."""

from __future__ import annotations

import argparse
import importlib
import os
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from manager_api.adapters.protocol import OperationMaterial
from manager_api.config import ManagerSettings
from scripts.manager_worker import apply_worker_environment

SYNTHETIC_KREDO_FACTORY = "manager_api.synthetic_kredo:synthetic_workflow_factory"
SYNTHETIC_X_FACTORY = "manager_api.synthetic_kredo:build_synthetic_x_adapter"


@dataclass(frozen=True)
class CheckResult:
    """单项运行前检查结果。"""

    name: str
    ok: bool
    detail: str


def _load_symbol(spec: str) -> Any:
    """加载 module:attribute 形式的 factory。"""
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("factory must use module:attribute notation")
    module: ModuleType = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def _result(name: str, ok: bool, detail: str) -> CheckResult:
    """构造一条稳定、无密钥输出的检查结果。"""
    return CheckResult(name=name, ok=ok, detail=detail)


def _settings_check(settings: ManagerSettings | None = None) -> CheckResult:
    """验证 manager 必要环境变量能被配置层读取。"""
    try:
        settings or ManagerSettings()  # type: ignore[call-arg]
    except Exception as error:
        return _result("settings", False, type(error).__name__)
    return _result("settings", True, "DATABASE_URL / REDIS_URL / SESSION_SECRET parsed")


def _factory_check(settings: ManagerSettings | None = None) -> CheckResult:
    """验证 Kredo workflow factory 可加载且返回 context manager。"""
    spec = os.environ.get("MANAGER_KREDO_WORKFLOW_FACTORY", "").strip()
    if not spec and settings is not None:
        spec = settings.manager_kredo_workflow_factory.strip()
    if not spec:
        return _result("kredo_factory", False, "MANAGER_KREDO_WORKFLOW_FACTORY is empty")
    try:
        factory = _load_symbol(spec)
        workflow = factory(OperationMaterial(kind="status"))
    except Exception as error:
        return _result("kredo_factory", False, type(error).__name__)
    has_context = hasattr(workflow, "__enter__") and hasattr(workflow, "__exit__")
    if not has_context:
        return _result("kredo_factory", False, "factory did not return a context manager")
    return _result("kredo_factory", True, spec)


def _x_factory_check(settings: ManagerSettings | None = None) -> CheckResult:
    """验证可选 X adapter factory，未配置时说明 worker 会使用默认实现。"""
    spec = os.environ.get("MANAGER_X_ADAPTER_FACTORY", "").strip()
    if not spec and settings is not None:
        spec = settings.manager_x_adapter_factory.strip()
    if not spec:
        return _result("x_factory", True, "default X adapter")
    try:
        factory = _load_symbol(spec)
        adapter = factory()
    except Exception as error:
        return _result("x_factory", False, type(error).__name__)
    has_methods = hasattr(adapter, "verify_account") and hasattr(adapter, "repost")
    if not has_methods:
        return _result("x_factory", False, "factory did not return an X adapter")
    return _result("x_factory", True, spec)


def _playwright_check() -> CheckResult:
    """验证 Playwright Python 包可以导入。"""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as error:
        return _result("playwright", False, type(error).__name__)
    return _result("playwright", True, "python package import ok")


def _chrome_check() -> CheckResult:
    """尝试启动并关闭 Chrome，确认浏览器依赖可用。"""
    try:
        from playwright.sync_api import sync_playwright
        from scripts.kredo_wallet_login_probe import _launch_chromium

        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright, headed=False)
            browser.close()
    except Exception as error:
        return _result("chrome", False, type(error).__name__)
    return _result("chrome", True, "headless chrome launch ok")


def _postgres_check() -> CheckResult:
    """可选检查 PostgreSQL 连通性，只执行 SELECT 1。"""
    try:
        from sqlalchemy import text

        from manager_api.db.session import build_engine

        engine = build_engine()
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
        finally:
            engine.dispose()
    except Exception as error:
        return _result("postgres", False, type(error).__name__)
    return _result("postgres", True, "select 1 ok")


def _redis_check() -> CheckResult:
    """可选检查 Redis 连通性，只执行 PING。"""
    try:
        import redis

        settings = ManagerSettings()  # type: ignore[call-arg]
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            client.ping()
        finally:
            client.close()
    except Exception as error:
        return _result("redis", False, type(error).__name__)
    return _result("redis", True, "ping ok")


def _print_results(results: list[CheckResult]) -> None:
    """用中文日志输出检查摘要，不展开任何环境变量值。"""
    for item in results:
        status = "通过" if item.ok else "失败"
        print(f"[{status}] {item.name}: {item.detail}")


def main() -> int:
    """运行 worker doctor。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-network",
        action="store_true",
        help="also check PostgreSQL and Redis connectivity",
    )
    parser.add_argument(
        "--launch-browser",
        action="store_true",
        help="also launch headless Chrome through Playwright",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="use local synthetic X/Kredo factories for a no-provider dry run check",
    )
    args = parser.parse_args()

    if args.synthetic:
        os.environ.setdefault("MANAGER_KREDO_WORKFLOW_FACTORY", SYNTHETIC_KREDO_FACTORY)
        os.environ.setdefault("MANAGER_X_ADAPTER_FACTORY", SYNTHETIC_X_FACTORY)

    settings: ManagerSettings | None
    try:
        settings = ManagerSettings()  # type: ignore[call-arg]
    except Exception:
        settings = None
    if settings is not None:
        apply_worker_environment(settings)

    results = [_settings_check(settings), _x_factory_check(settings), _factory_check(settings)]
    if not args.synthetic or args.launch_browser:
        results.append(_playwright_check())
    if args.launch_browser:
        results.append(_chrome_check())
    if args.check_network:
        results.extend([_postgres_check(), _redis_check()])
    _print_results(results)
    return 0 if all(item.ok for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
