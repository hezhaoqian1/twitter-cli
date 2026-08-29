from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from manager_api.adapters.kredo_adapter import KredoAdapter
from manager_api.adapters.kredo_browser_workflow import (
    KredoBrowserWorkflow,
    _cookie_payload,
    _oauth_failed,
    kredo_workflow_factory,
)
from manager_api.adapters.protocol import (
    AccountMaterial,
    AdapterError,
    ExternalStatus,
    OperationMaterial,
    WalletMaterial,
)

ACCOUNT = AccountMaterial(
    handle="MakylaFixture",
    token="token-fixture",
    cookie=base64.b64encode(
        json.dumps(
            [
                {
                    "name": "ct0",
                    "value": "csrf-fixture",
                    "domain": ".x.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                },
                {
                    "name": "auth_token",
                    "value": "token-fixture",
                    "domain": ".x.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                },
            ]
        ).encode("utf-8")
    ).decode("ascii"),
)
WALLET = WalletMaterial(address="0x" + "1" * 40, private_key="private-key-fixture")


def _operation(kind: str = "bind") -> OperationMaterial:
    """创建带公开元数据的任务材料。"""
    return OperationMaterial(
        kind=kind,
        metadata={
            "task_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "binding_id": "binding-fixture",
        },
    )


def _result(*, status: str = "bound", repost_verified: bool = False) -> dict[str, object]:
    """构造不含密钥材料的 probe 返回。"""
    return {
        "ok": True,
        "address": WALLET.address,
        "screenshot": "artifacts/kredo-worker/fixture.png",
        "wallet_methods": ["eth_requestAccounts", "personal_sign"],
        "twitter": {
            "bind_clicked": True,
            "task_modal_opened": True,
            "bind_api_seen": True,
            "oauth_popup_opened": True,
            "oauth_completion": "kredo_tasks",
            "callback_result": "success",
            "task_state": {
                "status": status,
                "boundHandle": ACCOUNT.handle,
                "repostVerified": repost_verified,
                "needsRebind": False,
                "failReason": None,
                "tweetUrl": "https://x.com/kredo/status/123",
            },
        },
    }


def test_cookie_payload_accepts_base64_browser_export() -> None:
    """导入的 base64 Cookie 能转换成 Playwright 文件结构。"""
    payload = _cookie_payload(ACCOUNT)

    names = {item["name"] for item in payload["cookies"]}
    assert names == {"ct0", "auth_token"}
    assert all(item["domain"] == ".x.com" for item in payload["cookies"])


def test_bind_writes_cookie_file_and_maps_bound(tmp_path: Path) -> None:
    """绑定 stage 只调用一次 probe，并把 bound 状态返回给 adapter。"""
    seen: dict[str, object] = {}

    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        cookie_file = kwargs["x_cookie_file"]
        assert isinstance(cookie_file, Path)
        seen["private_key"] = private_key
        seen["cookie_parent"] = cookie_file.parent
        seen["cookie_payload"] = json.loads(cookie_file.read_text(encoding="utf-8"))
        seen["flags"] = {
            "bind_twitter": kwargs["bind_twitter"],
            "repost": kwargs["repost"],
            "claim": kwargs["claim"],
            "status_only": kwargs["status_only"],
            "wait_for_task_state": kwargs["wait_for_task_state"],
        }
        return _result()

    workflow = KredoBrowserWorkflow(
        _operation(),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).bind(ACCOUNT, WALLET, _operation())

    assert result.status is ExternalStatus.SUCCEEDED
    assert result.operation_ref == "kredo:bind:binding-fixture"
    assert seen["private_key"] == "private-key-fixture"
    assert seen["flags"] == {
        "bind_twitter": True,
        "repost": False,
        "claim": False,
        "status_only": False,
        "wait_for_task_state": True,
    }
    assert {item["name"] for item in seen["cookie_payload"]["cookies"]} == {"ct0", "auth_token"}
    assert not Path(seen["cookie_parent"]).exists()


def test_bind_maps_oauth_403_to_failed(tmp_path: Path) -> None:
    """X OAuth 明确 403 时返回失败，避免错误进入慢回写等待。"""
    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key, kwargs
        result = _result(status="unbound")
        twitter = result["twitter"]
        assert isinstance(twitter, dict)
        twitter["oauth_completion"] = "pending"
        twitter["callback_result"] = None
        task_state = twitter["task_state"]
        assert isinstance(task_state, dict)
        task_state["boundHandle"] = None
        twitter["oauth_responses"] = [
            {
                "status": 403,
                "url": "https://x.com/i/oauth2/authorize?state=[REDACTED]",
            }
        ]
        return result

    workflow = KredoBrowserWorkflow(
        _operation(),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).bind(ACCOUNT, WALLET, _operation())

    assert result.status is ExternalStatus.FAILED
    assert _oauth_failed(
        {
            "twitter": {
                "oauth_responses": [{"status": 403}],
            }
        }
    )


def test_bind_maps_authorize_not_completed_to_failed(tmp_path: Path) -> None:
    """授权页未完成跳转时失败退出，不进入慢回写轮询。"""
    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key, kwargs
        result = _result(status="unbound")
        twitter = result["twitter"]
        assert isinstance(twitter, dict)
        twitter["oauth_completion"] = "authorize_not_completed"
        twitter["callback_result"] = None
        task_state = twitter["task_state"]
        assert isinstance(task_state, dict)
        task_state["boundHandle"] = None
        return result

    workflow = KredoBrowserWorkflow(
        _operation(),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).bind(ACCOUNT, WALLET, _operation())

    assert result.status is ExternalStatus.FAILED
    assert _oauth_failed({"twitter": {"oauth_completion": "authorize_not_completed"}})


def test_status_maps_repost_verified_to_complete(tmp_path: Path) -> None:
    """慢回写轮询看到 repostVerified 后返回完成态。"""
    calls: list[dict[str, object]] = []

    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key
        calls.append(dict(kwargs))
        return _result(status="bound", repost_verified=True)

    workflow = KredoBrowserWorkflow(
        _operation("repost"),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).status(_operation("repost"), ACCOUNT, WALLET)

    assert result.status is ExternalStatus.SUCCEEDED
    assert result.evidence.attributes["repostVerified"] is True
    assert calls[0]["status_only"] is True
    assert calls[0]["bind_twitter"] is False
    assert calls[0]["claim"] is False
    assert calls[0]["wait_for_task_state"] is True


def test_repost_stage_only_reads_kredo_status(tmp_path: Path) -> None:
    """转发 stage 的 Kredo 侧只做回写状态读取，不绑定也不领取。"""
    calls: list[dict[str, object]] = []

    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key
        calls.append(dict(kwargs))
        return _result(status="bound", repost_verified=True)

    workflow = KredoBrowserWorkflow(
        _operation("repost"),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).repost(ACCOUNT, WALLET, _operation("repost"))

    assert result.status is ExternalStatus.ALREADY_COMPLETED
    assert len(calls) == 1
    assert calls[0]["status_only"] is True
    assert calls[0]["bind_twitter"] is False
    assert calls[0]["repost"] is False
    assert calls[0]["claim"] is False


def test_bind_status_maps_unbound_to_failed(tmp_path: Path) -> None:
    """已有绑定引用再次轮询仍 unbound 时退出等待。"""
    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key, kwargs
        result = _result(status="unbound")
        twitter = result["twitter"]
        assert isinstance(twitter, dict)
        task_state = twitter["task_state"]
        assert isinstance(task_state, dict)
        task_state["boundHandle"] = None
        return result

    workflow = KredoBrowserWorkflow(
        _operation("bind"),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).status(_operation("bind"), ACCOUNT, WALLET)

    assert result.status is ExternalStatus.FAILED


def test_claim_runs_only_claim_stage_after_preflight(tmp_path: Path) -> None:
    """领取 stage 的 action 不会重新绑定或转发。"""
    calls: list[dict[str, object]] = []

    def runner(private_key: str, **kwargs: object) -> dict[str, object]:
        del private_key
        calls.append(dict(kwargs))
        if kwargs["status_only"]:
            return _result(status="bound", repost_verified=True)
        result = _result(status="claimed", repost_verified=True)
        result["twitter"]["claim"] = {"status": "claimed", "button": "Claim"}
        return result

    workflow = KredoBrowserWorkflow(
        _operation("claim"),
        runner=runner,
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    result = KredoAdapter(lambda _: workflow).claim(ACCOUNT, WALLET, _operation("claim"))

    assert result.status is ExternalStatus.SUCCEEDED
    assert [item["status_only"] for item in calls] == [True, False]
    assert [item["wait_for_task_state"] for item in calls] == [True, False]
    assert calls[-1]["claim"] is True
    assert calls[-1]["bind_twitter"] is False
    assert calls[-1]["repost"] is False


def test_account_summary_reports_unsupported_without_secret_echo(tmp_path: Path) -> None:
    """余额同步未接入时返回类型化错误，不泄露材料。"""
    workflow = KredoBrowserWorkflow(
        _operation("balance_sync"),
        runner=lambda *args, **kwargs: {},
        artifact_dir=tmp_path,
        timeout_seconds=5,
    )

    with pytest.raises(AdapterError) as error:
        with workflow:
            workflow.account_summary(ACCOUNT, WALLET, _operation("balance_sync"))

    assert error.value.code == "balance_not_supported"
    assert "private-key-fixture" not in str(error.value)


def test_factory_returns_context_manager() -> None:
    """环境变量入口返回 worker 可使用的 context manager。"""
    workflow = kredo_workflow_factory(_operation())

    assert hasattr(workflow, "__enter__")
    assert hasattr(workflow, "__exit__")
