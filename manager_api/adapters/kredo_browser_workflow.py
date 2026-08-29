"""Browser-backed Kredo workflow bridge for manager workers."""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

from .protocol import AccountMaterial, AdapterError, OperationMaterial, WalletMaterial


def _json_candidate(value: str) -> object | None:
    """解析可能是明文 JSON 的 Cookie 导出。"""
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _base64_json_candidate(value: str) -> object | None:
    """解析可能是 base64 包裹的 Cookie 导出。"""
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _cookies_from_mapping(value: dict[str, object]) -> list[dict[str, object]]:
    """从 dict 或浏览器导出 envelope 中提取 Playwright Cookie 列表。"""
    nested = value.get("cookies")
    if isinstance(nested, list):
        return _normalize_cookies(nested)
    cookies: list[dict[str, object]] = []
    for name in ("auth_token", "ct0"):
        item_value = value.get(name)
        if item_value:
            cookies.append(_cookie(str(name), str(item_value)))
    return cookies


def _cookie(name: str, value: str) -> dict[str, object]:
    """构造 X 域名下的单个 Playwright Cookie。"""
    return {
        "name": name,
        "value": value,
        "domain": ".x.com",
        "path": "/",
        "secure": True,
        "httpOnly": name == "auth_token",
        "sameSite": "None",
    }


def _normalize_cookies(raw_items: list[object]) -> list[dict[str, object]]:
    """标准化导入 Cookie，保留浏览器登录所需字段。"""
    cookies: list[dict[str, object]] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        cookies.append(
            {
                "name": name,
                "value": str(item.get("value", "")),
                "domain": str(item.get("domain") or ".x.com"),
                "path": str(item.get("path") or "/"),
                "secure": bool(item.get("secure", True)),
                "httpOnly": bool(item.get("httpOnly", name == "auth_token")),
                "sameSite": str(item.get("sameSite") or "None"),
            }
        )
    return cookies


def _cookies_from_header(value: str) -> list[dict[str, object]]:
    """兼容 name=value; name2=value2 形式的 Cookie Header。"""
    cookies: list[dict[str, object]] = []
    for part in value.split(";"):
        name, separator, item_value = part.partition("=")
        name = name.strip()
        if separator and name:
            cookies.append(_cookie(name, item_value.strip()))
    return cookies


def _cookie_payload(account: AccountMaterial) -> dict[str, list[dict[str, object]]]:
    """把导入账号材料转换成 probe 需要的 Cookie 文件内容。"""
    value = account.cookie.strip()
    candidates = [candidate for candidate in (_json_candidate(value), _base64_json_candidate(value)) if candidate]
    cookies: list[dict[str, object]] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            cookies = _normalize_cookies(candidate)
            break
        if isinstance(candidate, dict):
            cookies = _cookies_from_mapping(candidate)
            if cookies:
                break
    if not cookies and value and ";" in value and "=" in value:
        cookies = _cookies_from_header(value)
    if account.auth_token and not any(item.get("name") == "auth_token" for item in cookies):
        cookies.append(_cookie("auth_token", account.auth_token))
    return {"cookies": cookies}


def _probe_runner() -> Any:
    """延迟导入浏览器 probe，避免普通 manager 单测加载 Playwright。"""
    from scripts.kredo_wallet_login_probe import run

    return run


def _task_state(result: dict[str, object]) -> dict[str, object]:
    """从 probe 结果中提取 Kredo 任务状态。"""
    twitter = result.get("twitter")
    if not isinstance(twitter, dict):
        return {}
    state = twitter.get("task_state")
    return dict(state) if isinstance(state, dict) else {}


def _twitter_diag(result: dict[str, object]) -> dict[str, object]:
    """保留可诊断字段，不返回账号密钥材料。"""
    twitter = result.get("twitter")
    if not isinstance(twitter, dict):
        return {}
    safe_keys = {
        "bind_clicked",
        "task_modal_opened",
        "bind_api_seen",
        "oauth_popup_opened",
        "oauth_navigation_fallback",
        "popup_url",
        "authorize_url",
        "callback_url",
        "callback_location",
        "final_task_url",
        "oauth_completion",
        "callback_result",
        "task_state",
        "repost",
        "claim",
        "api_responses",
        "redirects",
        "oauth_responses",
        "navigation_events",
        "request_failures",
    }
    return {key: value for key, value in twitter.items() if key in safe_keys}


def _oauth_failed(result: dict[str, object]) -> bool:
    """识别 OAuth 已经失败的诊断信号，避免把 403 误当慢回写。"""
    twitter = result.get("twitter")
    if not isinstance(twitter, dict):
        return False
    if twitter.get("callback_result") == "failed":
        return True
    if twitter.get("oauth_completion") in {
        "x_oauth_error",
        "authorize_url_missing",
        "authorize_not_completed",
        "bind_not_clicked",
    }:
        return True
    oauth_responses = twitter.get("oauth_responses")
    if isinstance(oauth_responses, list):
        return any(
            isinstance(item, dict) and item.get("status") in {401, 403}
            for item in oauth_responses
        )
    return False


def _operation_ref(operation: OperationMaterial) -> str | None:
    """生成稳定外部引用，优先使用已有任务引用。"""
    if operation.operation_ref:
        return operation.operation_ref
    binding_id = operation.metadata.get("binding_id")
    task_id = operation.metadata.get("task_id")
    candidate = binding_id or task_id
    return f"kredo:{operation.kind}:{candidate}" if candidate else None


def _is_bound(state: dict[str, object], account: AccountMaterial) -> bool:
    """判断绑定状态是否已经回写到当前账号。"""
    bound_handle = str(state.get("boundHandle") or "").casefold().lstrip("@")
    return state.get("status") == "bound" or bound_handle == account.handle.casefold()


def _status_from_state(
    state: dict[str, object],
    account: AccountMaterial,
    operation: OperationMaterial,
) -> str:
    """按单阶段语义把 Kredo 状态映射为 worker 状态。"""
    raw_status = str(state.get("status") or "").strip().casefold()
    if raw_status in {"claimed", "complete", "completed"}:
        return "claimed"
    if operation.kind == "bind" and raw_status in {"unbound", "not_bound"}:
        return "failed"
    if operation.kind == "claim" and state.get("repostVerified") is True:
        return "pending_claim"
    if state.get("repostVerified") is True:
        return "reposted"
    if _is_bound(state, account):
        return "bound"
    if raw_status:
        return raw_status
    return "pending"


class KredoBrowserWorkflow:
    """每个 worker 调用创建一个独立浏览器 probe 上下文。"""

    def __init__(
        self,
        operation: OperationMaterial,
        *,
        runner: Any | None = None,
        artifact_dir: Path | None = None,
        timeout_seconds: int | None = None,
        headed: bool | None = None,
    ) -> None:
        self.operation = operation
        self._runner = runner or _probe_runner()
        self._artifact_dir = artifact_dir or Path(
            os.environ.get("MANAGER_KREDO_BROWSER_ARTIFACT_DIR", "artifacts/kredo-worker")
        )
        self._timeout_seconds = timeout_seconds or int(
            os.environ.get("MANAGER_KREDO_BROWSER_TIMEOUT_SECONDS", "120")
        )
        self._headed = headed if headed is not None else (
            os.environ.get("MANAGER_KREDO_BROWSER_HEADED", "").strip().casefold()
            in {"1", "true", "yes", "on"}
        )
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._workdir: Path | None = None

    def __enter__(self) -> "KredoBrowserWorkflow":
        """创建单次调用的临时目录。"""
        self._tempdir = tempfile.TemporaryDirectory(prefix="kredo-worker-")
        self._workdir = Path(self._tempdir.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """清理临时 Cookie 文件和浏览器输入材料。"""
        del exc_type, exc, traceback
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None
            self._workdir = None

    def bind(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> dict[str, object]:
        """只执行 Kredo 绑定阶段，不继续执行转发或领取。"""
        result = self._run_probe(
            account,
            wallet,
            operation,
            bind_twitter=True,
            wait_for_task_state=True,
        )
        state = _task_state(result)
        if _is_bound(state, account):
            status = "bound"
        elif _oauth_failed(result):
            status = "failed"
        elif result.get("ok") is True:
            status = "pending_bind"
        else:
            status = "failed"
        return self._payload(status, operation, result)

    def repost(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> dict[str, object]:
        """读取 Kredo 转发回写状态，不把整套流程串起来执行。"""
        return self.status(operation, account, wallet)

    def claim(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> dict[str, object]:
        """只执行领取阶段，前置状态由 KredoAdapter 负责检查。"""
        result = self._run_probe(
            account,
            wallet,
            operation,
            claim=True,
            wait_for_task_state=False,
        )
        state = _task_state(result)
        twitter = result.get("twitter") if isinstance(result.get("twitter"), dict) else {}
        claim = twitter.get("claim") if isinstance(twitter, dict) else None
        claim_status = str(claim.get("status") if isinstance(claim, dict) else "")
        if claim_status == "claimed" or _status_from_state(state, account, operation) == "claimed":
            status = "claimed"
        elif result.get("ok") is True:
            status = "pending_claim"
        else:
            status = "failed"
        return self._payload(status, operation, result)

    def status(
        self,
        operation: OperationMaterial,
        account: AccountMaterial | None = None,
        wallet: WalletMaterial | None = None,
    ) -> dict[str, object]:
        """读取当前 Kredo 任务状态，用于慢回写轮询。"""
        if account is None or wallet is None:
            return {
                "status": "pending",
                "operation_ref": _operation_ref(operation),
                "evidence": {"reason": "account_and_wallet_required"},
            }
        result = self._run_probe(account, wallet, operation, status_only=True)
        state = _task_state(result)
        status = _status_from_state(state, account, operation) if result.get("ok") is True else "failed"
        return self._payload(status, operation, result)

    def account_summary(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
    ) -> dict[str, object]:
        """余额同步需要专门接口；浏览器任务 probe 只负责任务阶段。"""
        del account, wallet, operation
        raise AdapterError(
            "balance_not_supported",
            "Kredo browser workflow does not expose account summary fields",
            retryable=False,
        )

    def _run_probe(
        self,
        account: AccountMaterial,
        wallet: WalletMaterial,
        operation: OperationMaterial,
        *,
        bind_twitter: bool = False,
        claim: bool = False,
        status_only: bool = False,
        wait_for_task_state: bool = True,
    ) -> dict[str, object]:
        """准备临时 Cookie 文件并调用既有浏览器 probe。"""
        workdir = self._require_workdir()
        cookie_file = workdir / "x-cookies.json"
        cookie_file.write_text(
            json.dumps(_cookie_payload(account), ensure_ascii=False),
            encoding="utf-8",
        )
        screenshot = self._screenshot_path(operation)
        try:
            result = self._runner(
                wallet.private_key,
                screenshot=screenshot,
                timeout=self._timeout_seconds,
                x_cookie_file=cookie_file,
                bind_twitter=bind_twitter,
                repost=False,
                claim=claim,
                headed=self._headed,
                keep_open=False,
                status_only=status_only,
                wait_for_task_state=wait_for_task_state,
            )
        except Exception as error:
            raise AdapterError.from_exception(operation.kind, error, retryable=True) from error
        if not isinstance(result, dict):
            raise AdapterError(
                f"{operation.kind}_invalid_response",
                "Kredo browser workflow returned an invalid result",
            )
        return result

    def _payload(
        self,
        status: str,
        operation: OperationMaterial,
        result: dict[str, object],
    ) -> dict[str, object]:
        """把 probe 结果压缩成 KredoAdapter 可归一化的 mapping。"""
        state = _task_state(result)
        return {
            "status": status,
            "operation_ref": _operation_ref(operation),
            "evidence": {
                "address": result.get("address"),
                "screenshot": result.get("screenshot"),
                "wallet_methods": result.get("wallet_methods", []),
                "task_state": state,
                "boundHandle": state.get("boundHandle"),
                "repostVerified": state.get("repostVerified"),
                "needsRebind": state.get("needsRebind"),
                "failReason": state.get("failReason"),
                "tweetUrl": state.get("tweetUrl"),
                "twitter": _twitter_diag(result),
            },
        }

    def _require_workdir(self) -> Path:
        """保证 workflow 通过 context manager 使用。"""
        if self._workdir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="kredo-worker-")
            self._workdir = Path(self._tempdir.name)
        return self._workdir

    def _screenshot_path(self, operation: OperationMaterial) -> Path:
        """生成不含账号、Cookie、私钥的截图路径。"""
        task_id = str(operation.metadata.get("task_id") or "")[:8]
        suffix = f"-{task_id}" if task_id else ""
        return self._artifact_dir / f"{operation.kind}{suffix}-{uuid4().hex[:10]}.png"


def kredo_workflow_factory(operation: OperationMaterial) -> KredoBrowserWorkflow:
    """Worker 环境变量指向的 factory 入口。"""
    return KredoBrowserWorkflow(operation)
