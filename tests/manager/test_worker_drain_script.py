from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import sys
from pathlib import Path


def _load_drain():
    """加载 drain 脚本模块，不执行命令入口。"""
    path = Path("scripts/manager_worker_drain.py")
    spec = importlib.util.spec_from_file_location("manager_worker_drain", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class DrainResultFixture:
    """最小 drain 结果夹具。"""

    cycles: int
    dispatched: int
    completed: int


def test_worker_drain_summary_is_stable() -> None:
    """drain 输出固定 key=value 行，方便日志检索。"""
    drain = _load_drain()

    assert drain._format_summary(DrainResultFixture(2, 3, 4)) == "\n".join(
        [
            "cycles=2",
            "dispatched=3",
            "completed=4",
        ]
    )


def test_worker_drain_main_uses_configured_runner_without_secret_echo(
    monkeypatch,
    capsys,
) -> None:
    """命令入口复用生产 runner 装配并保持输出脱敏。"""
    drain = _load_drain()
    calls: dict[str, object] = {}

    class RedisFixture:
        """记录 Redis URL，但不触发网络。"""

        def __init__(self, url: str, **kwargs: object) -> None:
            calls["redis_url"] = url
            calls["redis_kwargs"] = kwargs

        @classmethod
        def from_url(cls, url: str, **kwargs: object) -> "RedisFixture":
            return cls(url, **kwargs)

        def close(self) -> None:
            calls["redis_closed"] = True

    class SessionFixture:
        """最小 session 夹具。"""

        def commit(self) -> None:
            calls["committed"] = True

        def close(self) -> None:
            calls["session_closed"] = True

    class EngineFixture:
        """最小 engine 夹具。"""

        def dispose(self) -> None:
            calls["engine_disposed"] = True

    class RunnerFixture:
        """记录 drain 参数。"""

        def run_until_idle(self, **kwargs: object) -> DrainResultFixture:
            calls["drain_kwargs"] = kwargs
            return DrainResultFixture(cycles=5, dispatched=6, completed=7)

    class SettingsFixture:
        redis_url = "redis://example.test:6379/0"

    def build_runner_fixture(settings: object, **kwargs: object) -> RunnerFixture:
        calls["settings"] = settings
        calls["runner_kwargs"] = kwargs
        return RunnerFixture()

    monkeypatch.setattr(drain, "get_settings", lambda: SettingsFixture())
    monkeypatch.setattr(drain, "build_engine", lambda settings: EngineFixture())
    monkeypatch.setattr(drain, "session_factory", lambda engine: lambda: SessionFixture())
    monkeypatch.setattr(drain.redis, "Redis", RedisFixture)
    monkeypatch.setattr(drain, "required_kredo_factory_spec", lambda settings=None: "fixture:kredo")
    monkeypatch.setattr(drain, "_load_symbol", lambda spec: f"loaded:{spec}")
    monkeypatch.setattr(drain, "build_x_adapter", lambda spec: f"x:{spec or 'default'}")
    monkeypatch.setattr(drain, "build_runner", build_runner_fixture)
    monkeypatch.setenv("MANAGER_X_ADAPTER_FACTORY", "fixture:x-secret")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manager_worker_drain.py",
            "--max-cycles",
            "5",
            "--dispatch-limit",
            "3",
            "--max-jobs-per-cycle",
            "2",
        ],
    )

    assert drain.main() == 0
    output = capsys.readouterr().out

    assert "cycles=5" in output
    assert "dispatched=6" in output
    assert "completed=7" in output
    assert "secret-token" not in output
    assert calls["drain_kwargs"] == {
        "dispatch_limit": 3,
        "max_jobs_per_cycle": 2,
        "max_cycles": 5,
    }
    assert calls["session_closed"] is True
    assert calls["redis_closed"] is True
    assert calls["engine_disposed"] is True
