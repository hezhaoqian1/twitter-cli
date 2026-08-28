from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "x_cookie_batch_status", Path("scripts/x_cookie_batch_status.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cookie_blob() -> str:
    cookies = [
        {"name": "auth_token", "value": "a" * 40, "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": "b" * 160, "domain": ".x.com", "path": "/"},
    ]
    return base64.b64encode(json.dumps(cookies).encode()).decode()


def test_parse_tsv_accepts_account_cookie_rows(tmp_path) -> None:
    mod = _load_module()
    source = tmp_path / "accounts.tsv"
    source.write_text(
        "User\tpass\tTOTP\tuser@example.com\tmailpass\t%s\t%s\n" % ("a" * 40, _cookie_blob()),
        encoding="utf-8",
    )

    rows = mod.parse_tsv(source)

    assert len(rows) == 1
    assert rows[0].username == "User"
    assert rows[0].token == "a" * 40
    assert len(rows[0].cookies) == 2


def test_check_rows_uses_cookie_file_env(monkeypatch, tmp_path) -> None:
    mod = _load_module()
    row = mod.CookieRow(
        line_number=1,
        username="User",
        token="a" * 40,
        cookies=[
            {"name": "auth_token", "value": "a" * 40},
            {"name": "ct0", "value": "b" * 160},
        ],
    )
    calls = []

    def fake_run(cmd, cwd, env, text, capture_output, timeout, check):
        # 确认验证命令只拿临时 Cookie 文件，不继承旧的最小 Cookie 环境变量。
        calls.append((cmd, cwd, env, text, capture_output, timeout, check))
        assert "TWITTER_COOKIE_FILE" in env
        assert "TWITTER_AUTH_TOKEN" not in env
        assert "TWITTER_CT0" not in env
        assert os.stat(env["TWITTER_COOKIE_FILE"]).st_mode & 0o777 == 0o600
        return SimpleNamespace(
            returncode=0,
            stdout="ok: true\ndata:\n  authenticated: true\n  user:\n    id: '123'\n    username: User\n",
            stderr="",
        )

    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "old")
    monkeypatch.setenv("TWITTER_CT0", "old")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    results = mod.check_rows([row], tmp_path, timeout=7)

    assert results == [
        {
            "line": 1,
            "input_username": "User",
            "ok": True,
            "reported_username": "User",
            "reported_id": "123",
            "cookie_count": 2,
            "error": "",
        }
    ]
    assert calls[0][5] == 7
