#!/usr/bin/env python3
"""Probe Kredo wallet login with a local EIP-1193 provider."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from pathlib import Path
from typing import Any

TRANSACTION_KEYWORDS = ("permit", "approve", "allowance", "transfer")
KREDO_HOME = "https://www.kredo.fun/"
KREDO_TASKS = "https://www.kredo.fun/tasks"
KREDO_API_PREFIX = "https://api.kredo.fun/api/v1/"
TWITTER_COOKIE_FILE_ENV = "TWITTER_COOKIE_FILE"
SENSITIVE_QUERY_KEYS = {
    "code",
    "state",
    "error_description",
    "auth_code",
    "oauth_token",
    "oauth_verifier",
}
OAUTH_CALLBACK_PATH = "/api/v1/tasks/twitter/callback"


def _decode_personal_sign_message(value: object) -> str:
    """把 personal_sign 的 hex 消息解码成人类可读文本。"""
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return bytes.fromhex(value[2:]).decode("utf-8")
        except UnicodeDecodeError:
            return value
    return str(value)


def _provider_script(address: str) -> str:
    """生成页面初始化时注入的 EIP-1193 provider。"""
    return f"""
(() => {{
  const address = "{address}";
  const listeners = {{}};
  const provider = {{
    isMetaMask: true,
    isConnected: () => true,
    selectedAddress: address,
    chainId: "0x1",
    networkVersion: "1",
    request: (args) => window.__kredoWalletRequest(args),
    enable: () => Promise.resolve([address]),
    send: (methodOrPayload, paramsOrCallback) => {{
      if (typeof methodOrPayload === "string") {{
        return window.__kredoWalletRequest({{ method: methodOrPayload, params: paramsOrCallback || [] }});
      }}
      return window.__kredoWalletRequest(methodOrPayload)
        .then((result) => ({{ id: methodOrPayload.id, jsonrpc: "2.0", result }}));
    }},
    sendAsync: (payload, cb) => window.__kredoWalletRequest(payload)
      .then((result) => cb(null, {{ id: payload.id, jsonrpc: "2.0", result }}))
      .catch((error) => cb(error)),
    on: (event, cb) => {{ (listeners[event] ||= []).push(cb); }},
    removeListener: (event, cb) => {{
      listeners[event] = (listeners[event] || []).filter((item) => item !== cb);
    }},
    _metamask: {{ isUnlocked: () => Promise.resolve(true) }}
  }};
  Object.defineProperty(window, "ethereum", {{ value: provider, configurable: true }});
  window.dispatchEvent(new Event("ethereum#initialized"));
  const announce = () => window.dispatchEvent(new CustomEvent("eip6963:announceProvider", {{
    detail: {{
      info: {{
        uuid: "kredo-local-provider",
        name: "MetaMask",
        icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
        rdns: "io.metamask"
      }},
      provider
    }}
  }}));
  window.addEventListener("eip6963:requestProvider", announce);
  announce();
}})();
"""


def _redact_url(value: str) -> str:
    """脱敏 URL，只保留主机、路径和查询参数名。"""
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    query = parse_qs(parsed.query, keep_blank_values=True)
    safe_query = urlencode(
        [(key, "[REDACTED]") for key in sorted(query)],
        doseq=True,
    )
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", safe_query, ""))


def _redact_mapping(value: object) -> object:
    """递归隐藏授权码、token、Cookie 等字段，只保留诊断结构。"""
    sensitive_names = {
        "authorization",
        "auth_token",
        "cookie",
        "cookie_string",
        "code",
        "state",
        "auth_code",
        "signature",
        "token",
    }
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if str(key).lower() in sensitive_names
            else _redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    return value


def _unwrap_api_payload(payload: object) -> dict[str, object]:
    """提取 Kredo API envelope 的诊断字段。"""
    if not isinstance(payload, dict):
        return {"raw_type": type(payload).__name__}
    if payload.get("success") is False:
        error = payload.get("error")
        return {
            "success": False,
            "error": _redact_mapping(error),
        }
    data = payload.get("data")
    if isinstance(data, dict):
        return {
            "success": payload.get("success", True),
            "data": _redact_mapping(data),
        }
    return {str(key): _redact_mapping(item) for key, item in payload.items()}


def _task_state_from_payload(payload: object) -> dict[str, object] | None:
    """从任务接口响应中读取状态，兼容 envelope 和裸对象。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    # 任务概览把 X 状态嵌套在 twitterQuest，详情接口则直接返回这些字段。
    nested_state = data.get("twitterQuest")
    if isinstance(nested_state, dict):
        data = nested_state
    keys = ("status", "boundHandle", "repostVerified", "needsRebind", "failReason", "tweetUrl")
    if not any(key in data for key in keys):
        return None
    return {key: data.get(key) for key in keys}


def _latest_task_state(api_responses: list[dict[str, object]]) -> dict[str, object] | None:
    """从最近的任务详情或概览响应中读取最新 X 任务状态。"""
    for item in reversed(api_responses):
        path = str(item.get("path") or "")
        if not (path.endswith("/tasks/twitter") or path.endswith("/tasks/overview")):
            continue
        state = _task_state_from_payload(item.get("payload"))
        if state is not None:
            return state
    return None


def _load_x_cookies(path: Path) -> list[dict[str, object]]:
    """读取已有 X 会话文件，不在输出中暴露 Cookie 值。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("X session file must contain a cookies list")
    cookies: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        cookie = {
            "name": str(item["name"]),
            "value": str(item.get("value", "")),
            "domain": str(item.get("domain") or ".x.com"),
            "path": str(item.get("path") or "/"),
            "secure": bool(item.get("secure", True)),
            "httpOnly": bool(item.get("httpOnly", False)),
            "sameSite": str(item.get("sameSite") or "None"),
        }
        cookies.append(cookie)
    if not any(item["name"] == "auth_token" for item in cookies):
        raise ValueError("X session file is missing auth_token")
    return cookies


def _click_first_visible(page: Any, labels: tuple[str, ...]) -> str | None:
    """点击第一个可见的授权按钮，并返回匹配到的文字。"""
    for label in labels:
        locator = page.get_by_role("button", name=label, exact=True)
        if locator.count() and locator.first.is_visible():
            locator.first.click(timeout=5000)
            return label
        text = page.get_by_text(label, exact=True)
        if text.count() and text.first.is_visible():
            text.first.click(timeout=5000)
            return label
    return None


def _click_first_visible_containing(page: Any, labels: tuple[str, ...]) -> str | None:
    """点击包含指定文案的第一个可见按钮，兼容按钮内含状态标签的情况。"""
    for label in labels:
        locator = page.get_by_role("button", name=label, exact=False)
        if locator.count() and locator.first.is_visible():
            locator.first.click(timeout=5000)
            return label
        # X 的授权页在有头模式下可能先渲染普通 button，再补充可访问性名称。
        button = page.locator("button").filter(has_text=label)
        if button.count() and button.first.is_visible():
            button.first.click(timeout=5000)
            return label
        text = page.get_by_text(label, exact=True)
        if text.count() and text.first.is_visible():
            text.first.click(timeout=5000)
            return label
    return None


def _click_authorize_button(page: Any, timeout_ms: int = 20_000) -> str | None:
    """等待 X 授权按钮真正可见后点击，并兼容按钮文案的细微变化。"""
    deadline = time.time() + timeout_ms / 1000
    labels = (
        "Authorize app",
        "授权应用",
        "Authorize",
        "授权",
        "Allow",
    )
    while time.time() < deadline:
        try:
            clicked = _click_first_visible(page, labels)
            if clicked:
                return clicked
            clicked = _click_first_visible_containing(page, labels)
            if clicked:
                return clicked
        except Exception:
            # 页面切换瞬间 locator 可能失效，下一轮重新获取节点。
            pass
        page.wait_for_timeout(250)
    return None


def _is_api_response(url: str, suffix: str) -> bool:
    """判断响应是否来自 Kredo API 的指定端点。"""
    return url.startswith(KREDO_API_PREFIX) and urlparse(url).path.endswith(suffix)


def _is_task_api_response(url: str) -> bool:
    """判断响应是否来自 Kredo 任务 API，兼容 overview 和状态接口。"""
    path = urlparse(url).path
    return url.startswith(KREDO_API_PREFIX) and path.startswith("/api/v1/tasks")


def _callback_result(url: str) -> str | None:
    """读取 callback 重定向中的 twitter 结果值。"""
    query = parse_qs(urlparse(url).query)
    value = query.get("twitter", [None])[0]
    return str(value) if value is not None else None


def _oauth_url_state(url: str, popup_closed: bool = False) -> str:
    """根据 OAuth 窗口当前状态判断回调生命周期。"""
    if popup_closed:
        return "popup_closed"
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path.endswith(OAUTH_CALLBACK_PATH):
        return "kredo_callback"
    if parsed.netloc in {"kredo.fun", "www.kredo.fun"} and parsed.path.startswith("/tasks"):
        return "kredo_tasks"
    if parsed.netloc.endswith("x.com") and query.get("error"):
        return "x_oauth_error"
    if parsed.netloc.endswith("twitter.com") and query.get("error"):
        return "x_oauth_error"
    return "pending"


def _latest_kredo_tasks_url(pages: list[Any]) -> str:
    """从 OAuth popup 标签页中取最后一个 Kredo 任务页 URL。"""
    for candidate in reversed(pages):
        candidate_url = candidate.url
        if _oauth_url_state(candidate_url) == "kredo_tasks":
            return candidate_url
    return ""


def _tweet_id_from_url(value: str) -> str | None:
    """从 X 推文 URL 提取数字 ID。"""
    parsed = urlparse(value)
    match = re.search(r"/status/(\d+)", parsed.path)
    return match.group(1) if match else None


def _find_tweet_url(page: Any) -> str:
    """从任务页链接中提取官方推文 URL。"""
    links = page.locator('a[href*="/status/"]')
    for index in range(links.count()):
        href = links.nth(index).get_attribute("href") or ""
        if href.startswith(("https://x.com/", "https://twitter.com/")):
            return href.split("?", 1)[0]
    return ""


def _x_repost_state(page: Any) -> str:
    """读取 X 推文当前转发按钮状态。"""
    for selector in (
        '[data-testid="unretweet"]',
        '[aria-label="Undo repost"]',
        '[aria-label="Undo Retweet"]',
        '[aria-label="Undo Repost"]',
    ):
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible():
            return "already_reposted"
    for selector in (
        '[data-testid="retweet"]',
        '[aria-label="Repost"]',
        '[aria-label="Retweet"]',
    ):
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible():
            return "ready"
    return "unknown"


def _repost_tweet(page: Any, tweet_url: str, timeout_ms: int = 30_000) -> dict[str, object]:
    """打开官方推文，幂等地执行一次转发并确认按钮状态。"""
    tweet_id = _tweet_id_from_url(tweet_url)
    if not tweet_id:
        return {"status": "invalid_tweet_url", "tweet_url": tweet_url}

    page.goto(tweet_url, wait_until="domcontentloaded", timeout=60_000)
    deadline = time.time() + timeout_ms / 1000
    initial_state = "unknown"
    while time.time() < deadline:
        initial_state = _x_repost_state(page)
        if initial_state != "unknown":
            break
        page.wait_for_timeout(500)
    if initial_state == "already_reposted":
        return {
            "status": "already_reposted",
            "tweet_id": tweet_id,
            "tweet_url": tweet_url,
        }
    if initial_state != "ready":
        return {
            "status": "repost_button_not_found",
            "tweet_id": tweet_id,
            "tweet_url": tweet_url,
        }

    button = page.locator(
        '[data-testid="retweet"], [aria-label="Repost"], [aria-label="Retweet"]'
    ).first
    button.click(timeout=5_000)
    menu_deadline = time.time() + min(timeout_ms, 10_000) / 1000
    clicked_menu = False
    while time.time() < menu_deadline:
        for label in ("Repost", "Retweet"):
            menu_item = page.get_by_role("menuitem", name=label, exact=True)
            if menu_item.count() and menu_item.first.is_visible():
                menu_item.first.click(timeout=5_000)
                clicked_menu = True
                break
            text = page.get_by_text(label, exact=True)
            if text.count() and text.first.is_visible():
                text.first.click(timeout=5_000)
                clicked_menu = True
                break
        if clicked_menu:
            break
        page.wait_for_timeout(250)
    if not clicked_menu:
        return {
            "status": "repost_menu_not_found",
            "tweet_id": tweet_id,
            "tweet_url": tweet_url,
        }

    verify_deadline = time.time() + timeout_ms / 1000
    while time.time() < verify_deadline:
        if _x_repost_state(page) == "already_reposted":
            return {
                "status": "reposted",
                "tweet_id": tweet_id,
                "tweet_url": tweet_url,
            }
        page.wait_for_timeout(500)
    return {
        "status": "repost_click_unconfirmed",
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
    }


class LocalWallet:
    """本地钱包 RPC 处理器，只允许账户发现和登录签名。"""

    def __init__(self, private_key: str) -> None:
        from eth_account import Account

        self.private_key = private_key
        self.account = Account.from_key(private_key)
        self.calls: list[dict[str, object]] = []

    @property
    def address(self) -> str:
        """返回私钥派生出的 EVM 地址。"""
        return self.account.address

    def request(self, source: Any, arg: dict[str, Any]) -> object:
        """处理页面发来的 EIP-1193 RPC 请求。"""
        method = arg.get("method")
        params = arg.get("params") or []
        self.calls.append({"method": method, "params_seen": bool(params)})

        if method in ("eth_requestAccounts", "eth_accounts"):
            return [self.address]
        if method == "eth_chainId":
            return "0x1"
        if method == "net_version":
            return "1"
        if method in ("wallet_switchEthereumChain", "wallet_addEthereumChain"):
            return None
        if method == "wallet_getPermissions":
            return [
                {
                    "parentCapability": "eth_accounts",
                    "caveats": [{"type": "restrictReturnedAccounts", "value": [self.address]}],
                }
            ]
        if method == "wallet_requestPermissions":
            return {"eth_accounts": {}}
        if method in ("personal_sign", "eth_sign"):
            from eth_account import Account
            from eth_account.messages import encode_defunct

            message = _decode_personal_sign_message(params[0] if params else "")
            if any(keyword in message.lower() for keyword in TRANSACTION_KEYWORDS):
                raise RuntimeError("transaction-like signature rejected")
            signature = Account.sign_message(
                encode_defunct(text=message),
                self.private_key,
            ).signature.hex()
            return "0x" + signature.removeprefix("0x")
        if method in ("eth_signTypedData", "eth_signTypedData_v3", "eth_signTypedData_v4"):
            try:
                from eth_account import Account
                from eth_account.messages import encode_typed_data
            except ImportError as error:
                raise RuntimeError("typed-data signing is unavailable") from error

            typed = params[-1]
            if isinstance(typed, str):
                typed = json.loads(typed)
            text = json.dumps(typed, sort_keys=True)
            if any(keyword in text.lower() for keyword in TRANSACTION_KEYWORDS):
                raise RuntimeError("transaction-like typed signature rejected")
            typed_message = encode_typed_data(full_message=typed)
            signature = Account.sign_message(typed_message, self.private_key).signature.hex()
            return "0x" + signature.removeprefix("0x")
        if method in ("eth_sendTransaction", "eth_sendRawTransaction"):
            raise RuntimeError("transaction methods are disabled")
        raise RuntimeError(f"unsupported wallet method: {method}")


def run(
    private_key: str,
    screenshot: Path,
    timeout: int,
    x_cookie_file: Path | None = None,
    bind_twitter: bool = True,
    repost: bool = False,
    claim: bool = False,
    headed: bool = False,
    keep_open: bool = False,
) -> dict[str, object]:
    """打开 Kredo，完成钱包登录并诊断 X 绑定状态。"""
    from playwright.sync_api import sync_playwright

    wallet = LocalWallet(private_key)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=not headed)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        if x_cookie_file:
            context.add_cookies(_load_x_cookies(x_cookie_file))
        context.expose_binding("__kredoWalletRequest", wallet.request)
        context.add_init_script(_provider_script(wallet.address))
        page = context.new_page()
        task_page = page
        oauth_pages: list[Any] = []
        api_responses: list[dict[str, object]] = []
        bind_authorize_urls: list[str] = []
        redirects: list[dict[str, object]] = []
        oauth_responses: list[dict[str, object]] = []
        console_messages: list[dict[str, str]] = []
        page_errors: list[str] = []
        navigation_events: list[dict[str, str]] = []
        request_failures: list[dict[str, str]] = []

        def attach_page_diagnostics(target: Any) -> None:
            """为主页面和 OAuth popup 统一记录导航与请求失败。"""
            target.on(
                "framenavigated",
                lambda frame: (
                    navigation_events.append({"url": _redact_url(frame.url)})
                    if frame.parent_frame is None
                    else None
                ),
            )
            target.on(
                "requestfailed",
                lambda request: request_failures.append(
                    {
                        "url": _redact_url(request.url),
                        "failure": str(request.failure or "unknown"),
                    }
                ),
            )

        def on_response(response: Any) -> None:
            """采集 API 与 OAuth callback 响应，避免依赖页面内部 fetch。"""
            url = response.url
            parsed_url = urlparse(url)
            if parsed_url.netloc.endswith(("x.com", "twitter.com")) and (
                parsed_url.path.startswith("/i/oauth2/")
                or parsed_url.path.startswith("/i/api/2/oauth2/")
            ):
                oauth_responses.append(
                    {
                        "status": response.status,
                        "url": _redact_url(url),
                    }
                )
            if _is_task_api_response(url):
                try:
                    payload = response.json()
                except Exception:
                    payload = {"body": "[non-json]"}
                api_responses.append(
                    {
                        "status": response.status,
                        "path": urlparse(url).path,
                        "payload": _unwrap_api_payload(payload),
                    }
                )
                if urlparse(url).path.endswith("/tasks/twitter/bind"):
                    raw_data = payload.get("data") if isinstance(payload, dict) else None
                    if isinstance(raw_data, dict):
                        raw_authorize_url = raw_data.get("authorizeUrl")
                        if isinstance(raw_authorize_url, str) and raw_authorize_url:
                            bind_authorize_urls.append(raw_authorize_url)
            if "/api/v1/tasks/twitter/callback" in url:
                headers = response.headers
                redirects.append(
                    {
                        "status": response.status,
                        "url": _redact_url(url),
                        "location": _redact_url(headers.get("location", "")),
                        "callback": _callback_result(headers.get("location", "")),
                    }
                )

        # OAuth 可能在新标签页完成；监听整个 context 才不会漏掉 bind/callback。
        context.on("response", on_response)
        context.on("page", attach_page_diagnostics)
        attach_page_diagnostics(page)
        page.on(
            "console",
            lambda message: console_messages.append(
                {"type": message.type, "text": message.text[:240]}
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)[:240]))

        page.goto(KREDO_HOME, wait_until="networkidle", timeout=60_000)
        page.get_by_text("登錄", exact=True).click()
        page.wait_for_timeout(2500)
        page.get_by_text("MetaMask", exact=True).click()

        deadline = time.time() + timeout
        body = ""
        logged_in = False
        while time.time() < deadline:
            for label in ("跳過", "Skip"):
                skip = page.get_by_text(label, exact=True)
                if skip.count() and skip.first.is_visible():
                    # 首次登录会弹 Kredo 欢迎引导，跳过后才能稳定读取钱包状态。
                    skip.first.click()
                    page.wait_for_timeout(1000)
                    break
            body = page.locator("body").inner_text(timeout=10_000)
            short_address = f"{wallet.address[:5]}...{wallet.address[-4:]}".lower()
            if "資產組合" in body and short_address in body.lower():
                logged_in = True
                break
            page.wait_for_timeout(1000)

        task_state: dict[str, object] | None = None
        callback_url = ""
        bind_clicked = False
        task_modal_opened = False
        bind_api_seen = False
        oauth_popup_opened = False
        oauth_navigation_fallback = False
        popup_url = ""
        authorize_url = ""
        callback_location = ""
        final_task_url = ""
        oauth_completion = "not_started"
        repost_result: dict[str, object] | None = None
        if logged_in and bind_twitter:
            page.goto(KREDO_TASKS, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(1000)
            try:
                task_modal_opened = (
                    _click_first_visible(
                        page,
                        ("去完成", "前往 X", "Go to X", "Start"),
                    )
                    is not None
                )
            except Exception:
                pass
            if task_modal_opened and not repost:
                # 第二层按钮才会触发 bind API，并同步创建 OAuth popup。
                bind_label: str | None = None
                popup = None
                try:
                    with context.expect_page(timeout=8_000) as popup_info:
                        modal_deadline = time.time() + min(timeout, 15)
                        while time.time() < modal_deadline and bind_label is None:
                            bind_label = _click_first_visible_containing(
                                page,
                                (
                                    "綁定 X 賬號",
                                    "绑定 X 账号",
                                    "Connect an X account",
                                    "Bind X account",
                                ),
                            )
                            if bind_label is None:
                                page.wait_for_timeout(300)
                    popup = popup_info.value
                    oauth_popup_opened = True
                except Exception:
                    # 没有新页时，仍保留按钮点击结果并继续检查当前页导航。
                    pass
                bind_clicked = bind_label is not None

                if popup is not None:
                    oauth_pages.append(popup)
                    popup.wait_for_load_state("domcontentloaded", timeout=30_000)
                    page = popup

            if bind_clicked:
                # bind API 是异步请求，先等待响应，再读取 popup 的最终 URL。
                bind_deadline = time.time() + min(timeout, 15)
                while time.time() < bind_deadline:
                    bind_api_seen = any(
                        str(item.get("path") or "").endswith("/tasks/twitter/bind")
                        and item.get("status") in (200, 201)
                        for item in api_responses
                    )
                    if bind_api_seen:
                        break
                    page.wait_for_timeout(300)

                url_deadline = time.time() + min(timeout, 15)
                while time.time() < url_deadline and not authorize_url:
                    for candidate in context.pages:
                        candidate_url = candidate.url
                        if candidate_url.startswith(("https://x.com/", "https://twitter.com/")):
                            authorize_url = candidate_url
                            if candidate not in oauth_pages:
                                oauth_pages.append(candidate)
                            page = candidate
                            oauth_popup_opened = True
                            break
                    if not authorize_url:
                        page.wait_for_timeout(300)

                if not authorize_url and bind_authorize_urls:
                    # 若前端未完成 popup.location.href，使用 bind 响应中的同一地址补齐导航。
                    authorize_url = bind_authorize_urls[-1]
                    oauth_navigation_fallback = True
                    if page.url == "about:blank":
                        page.goto(authorize_url, wait_until="domcontentloaded", timeout=60_000)
                        oauth_popup_opened = True
                popup_url = page.url
                if authorize_url:
                    clicked = _click_authorize_button(page)
                    if clicked:
                        # 授权页不会立即关闭，等待它进入 Kredo callback、任务页或明确错误状态。
                        oauth_deadline = time.time() + max(15, min(timeout, 90))
                        oauth_completion = "pending"
                        while time.time() < oauth_deadline:
                            is_closed = page.is_closed()
                            current_url = "" if is_closed else page.url
                            final_task_url = _latest_kredo_tasks_url(oauth_pages)
                            if redirects:
                                # callback 通常是 302，popup 可能短暂停留在 X URL；
                                # 先记录 callback，再等待任意标签页落到任务页。
                                oauth_completion = "kredo_tasks" if final_task_url else "kredo_callback"
                            else:
                                oauth_completion = _oauth_url_state(current_url, is_closed)
                            if oauth_completion != "pending":
                                if oauth_completion != "kredo_callback" or final_task_url:
                                    break
                            page.wait_for_timeout(250)
                        if oauth_completion == "pending":
                            oauth_completion = "timeout"
                    else:
                        oauth_completion = _oauth_url_state(page.url, page.is_closed())
                    callback_url = "" if page.is_closed() else page.url
                    final_task_url = final_task_url or _latest_kredo_tasks_url(oauth_pages)
                    if redirects:
                        callback_location = str(redirects[-1].get("location") or "")
                    callback_result = _callback_result(callback_url) or _callback_result(
                        callback_location
                    )
                else:
                    oauth_completion = "authorize_url_missing"
                    callback_result = None
            else:
                oauth_completion = "bind_not_clicked"
                callback_result = None
        else:
            oauth_completion = "not_started"
            callback_result = None

        # callback 后通过前端自身加载任务接口，避免跨域 page.evaluate fetch 误判。
        if logged_in and bind_twitter:
            # 授权 popup 可能在回调后关闭，任务状态始终从主任务页读取。
            status_page = task_page
            # 回调完成后后端和任务卡存在短暂最终一致性，不能只读一次。
            status_deadline = time.time() + max(10, min(timeout, 60))
            while time.time() < status_deadline:
                status_page.goto(KREDO_TASKS, wait_until="domcontentloaded", timeout=60_000)
                status_page.wait_for_timeout(1200)
                task_state = _latest_task_state(api_responses)
                if task_state and (
                    task_state.get("status") == "bound"
                    or task_state.get("repostVerified") is True
                ):
                    break
                status_page.wait_for_timeout(1000)

        if logged_in and repost:
            # 绑定完成后只读取官方推文并执行一次转发，避免重复转发。
            tweet_url = str((task_state or {}).get("tweetUrl") or "")
            status_page = task_page
            if not tweet_url:
                status_page.goto(KREDO_TASKS, wait_until="domcontentloaded", timeout=60_000)
                status_page.wait_for_timeout(1500)
                task_state = _latest_task_state(api_responses) or task_state
                tweet_url = str((task_state or {}).get("tweetUrl") or "")
                if not tweet_url:
                    tweet_url = _find_tweet_url(status_page)
            if tweet_url:
                repost_result = _repost_tweet(status_page, tweet_url)
                # Kredo 校验也有异步延迟，回到任务页等待最终状态。
                verify_deadline = time.time() + max(10, min(timeout, 90))
                while time.time() < verify_deadline:
                    status_page.goto(
                        KREDO_TASKS,
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    status_page.wait_for_timeout(1200)
                    task_state = _latest_task_state(api_responses) or task_state
                    if task_state and task_state.get("repostVerified") is True:
                        break
                    status_page.wait_for_timeout(1500)
            else:
                repost_result = {"status": "tweet_url_missing"}

        if claim:
            raise RuntimeError("claim execution is not implemented in this probe")

        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot_page = task_page if page.is_closed() else page
        screenshot_page.screenshot(path=str(screenshot), full_page=True)
        result = {
            "ok": logged_in,
            "address": wallet.address,
            "screenshot": str(screenshot),
            "wallet_methods": [item["method"] for item in wallet.calls],
            "twitter": {
                "bind_clicked": bind_clicked,
                "task_modal_opened": task_modal_opened,
                "bind_api_seen": bind_api_seen,
                "oauth_popup_opened": oauth_popup_opened,
                "oauth_navigation_fallback": oauth_navigation_fallback,
                "popup_url": _redact_url(popup_url) if popup_url else "",
                "authorize_url": _redact_url(authorize_url) if authorize_url else "",
                "callback_url": _redact_url(callback_url) if callback_url else "",
                "callback_location": callback_location,
                "final_task_url": _redact_url(final_task_url) if final_task_url else "",
                "oauth_completion": oauth_completion,
                "callback_result": callback_result,
                "task_state": task_state,
                "repost": repost_result,
                "api_responses": api_responses[-8:],
                "redirects": redirects[-8:],
                "oauth_responses": oauth_responses[-8:],
                "navigation_events": navigation_events[-20:],
                "request_failures": request_failures[-20:],
            },
            "console": console_messages[-20:],
            "page_errors": page_errors[-10:],
        }
        if keep_open and headed:
            # 有头验证结束后保留浏览器，方便人工确认最终奖励页状态。
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            while any(not candidate.is_closed() for candidate in context.pages):
                time.sleep(1)
        context.close()

    return result


def main() -> int:
    """命令入口：读取私钥环境变量并输出 JSON 状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path, default=Path("/tmp/kredo-wallet-login.png"))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="以可见 Chrome 窗口运行 OAuth 流程",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="有头模式完成后保持浏览器窗口打开，直到手动关闭",
    )
    parser.add_argument(
        "--twitter-cookie-file",
        type=Path,
        default=Path(os.environ[TWITTER_COOKIE_FILE_ENV])
        if os.environ.get(TWITTER_COOKIE_FILE_ENV)
        else None,
        help="已有 X Playwright session JSON；只用于绑定诊断，不打印 Cookie",
    )
    parser.add_argument(
        "--no-bind-twitter",
        action="store_true",
        help="只验证钱包登录，不启动 X 绑定",
    )
    parser.add_argument("--repost", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--claim", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    private_key = os.environ.get("KREDO_PRIVATE_KEY", "")
    if not private_key:
        print("Set KREDO_PRIVATE_KEY before running.", file=sys.stderr)
        return 2
    result = run(
        private_key,
        args.screenshot,
        args.timeout,
        x_cookie_file=args.twitter_cookie_file,
        bind_twitter=not args.no_bind_twitter,
        repost=args.repost,
        claim=args.claim,
        headed=args.headed,
        keep_open=args.keep_open,
    )
    if not args.keep_open:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
