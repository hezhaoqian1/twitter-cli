#!/usr/bin/env python3
"""Log into X in a real browser and export the resulting session cookies."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def totp(secret: str, timestamp: int | None = None) -> str:
    """Generate the current six-digit TOTP code from a Base32 secret."""
    # 清理常见分隔符并补齐 Base32 填充，兼容导出的密钥格式。
    normalized = "".join(secret.split()).replace("-", "").upper()
    normalized += "=" * (-len(normalized) % 8)
    key = base64.b32decode(normalized, casefold=True)
    counter = int((timestamp or int(time.time())) // 30).to_bytes(8, "big")
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def _visible_input(page: Any, selectors: list[str]) -> Any:
    """Return the first visible input matching the supplied selectors."""
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible():
            return locator.first
    return None


def _click_button(page: Any, labels: list[str]) -> None:
    """Click the first visible button whose accessible name matches."""
    for label in labels:
        buttons = page.locator("button")
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if not button.is_visible():
                continue
            try:
                matches = button.inner_text(timeout=500).strip() == label
            except Exception:
                matches = False
            if matches:
                try:
                    button.click(timeout=3000)
                except Exception:
                    button.evaluate("el => el.click()")
                return

        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.first.is_visible():
            try:
                button.first.click(timeout=3000)
            except Exception:
                button.first.evaluate("el => el.click()")
            return
        text = page.get_by_text(label, exact=True)
        if text.count() and text.first.is_visible():
            try:
                text.first.click(timeout=3000)
            except Exception:
                text.first.evaluate("el => el.click()")
            return

    # X 登录页经常改版，优先识别稳定 test id，最后用回车提交当前表单。
    for selector in [
        '[data-testid="ocfEnterTextNextButton"]',
        '[data-testid="LoginForm_Login_Button"]',
        'button[type="submit"]',
    ]:
        button = page.locator(selector)
        if button.count() and button.first.is_visible():
            button.first.click()
            return
    page.keyboard.press("Enter")


def _cookie_payload(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """构造 twitter-cli 可直接读取的会话文件结构。"""
    selected = [
        item
        for item in cookies
        if item.get("domain", "").endswith(("x.com", "twitter.com"))
    ]
    values = {item["name"]: item["value"] for item in selected}
    if not values.get("auth_token") or not values.get("ct0"):
        raise RuntimeError("Login finished without both auth_token and ct0 cookies")
    return {
        "auth_token": values["auth_token"],
        "ct0": values["ct0"],
        "cookie_string": "; ".join("%s=%s" % (item["name"], item["value"]) for item in selected),
        "cookies": selected,
        "created_at": int(time.time()),
    }


def _try_cookie_payload(cookies: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Cookie 尚未生成完整时返回 None，方便轮询等待。"""
    try:
        return _cookie_payload(cookies)
    except RuntimeError:
        return None


def _cookies_from_base64(encoded: str) -> list[dict[str, Any]]:
    """从用户导出的 Base64 Cookie JSON 中恢复 Playwright Cookie。"""
    raw = base64.b64decode(encoded)
    source = json.loads(raw)
    cookies = []
    for item in source:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        cookie = {
            "name": item["name"],
            "value": str(item.get("value", "")),
            "domain": item.get("domain") or ".x.com",
            "path": item.get("path") or "/",
            "secure": bool(item.get("secure", True)),
            "httpOnly": bool(item.get("httpOnly", False)),
            "sameSite": "None",
        }
        cookies.append(cookie)
    return cookies


def _write_payload(output: Path, payload: dict[str, Any]) -> None:
    """写入 0600 权限的会话文件，避免完整 Cookie 被普通文件权限暴露。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def export_from_token(
    auth_token: str,
    output: Path,
    profile: Path,
    ct0: str = "",
    cookie_base64: str = "",
) -> None:
    """通过写入 auth_token Cookie 进入浏览器登录态，并导出 X 刷新的 ct0。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Run: uv run --with playwright "
            "python scripts/x_login_export.py"
        ) from exc

    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                channel="chrome",
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
        except Exception:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                viewport={"width": 1280, "height": 900},
            )

        page = context.pages[0] if context.pages else context.new_page()
        cookies = _cookies_from_base64(cookie_base64) if cookie_base64 else []
        if not any(item.get("name") == "auth_token" for item in cookies):
            cookies.append(
                {
                    "name": "auth_token",
                    "value": auth_token,
                    "domain": ".x.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "None",
                }
            )
        if ct0 and not any(item.get("name") == "ct0" for item in cookies):
            cookies.append(
                {
                    "name": "ct0",
                    "value": ct0,
                    "domain": ".x.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "sameSite": "None",
                }
            )
        context.add_cookies(cookies)

        # 访问首页会让 X 补齐 ct0、guest_id 等运行时 Cookie。
        deadline = time.time() + 45
        payload = None
        while time.time() < deadline:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            payload = _try_cookie_payload(context.cookies(["https://x.com", "https://twitter.com"]))
            body = page.locator("body").inner_text(timeout=5000)
            if payload and ("电子邮箱或用户名" not in body and "Sign in" not in body):
                break
            page.wait_for_timeout(1000)
        if not payload:
            raise RuntimeError("Token login did not produce a usable X session within 45 seconds")
        _write_payload(output, payload)
        context.close()


def run(
    username: str,
    password: str,
    totp_secret: str,
    output: Path,
    profile: Path,
    email: str = "",
) -> None:
    """运行账号密码登录流程并写入最新 Cookie 会话。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Run: uv run --with playwright "
            "python scripts/x_login_export.py"
        ) from exc

    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium
        try:
            context = browser.launch_persistent_context(
                str(profile),
                channel="chrome",
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
        except Exception:
            context = browser.launch_persistent_context(
                str(profile),
                headless=False,
                viewport={"width": 1280, "height": 900},
            )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)

        username_input = _visible_input(
            page,
            [
                'input[autocomplete="username"]',
                'input[name="username_or_email"]',
                'input[name="text"]',
                'input[type="text"]',
            ],
        )
        if username_input:
            username_input.fill(username)
            _click_button(page, ["Next", "下一步", "继续"])

        page.wait_for_timeout(1200)
        challenge_input = _visible_input(
            page,
            [
                'input[data-testid="ocfEnterTextTextInput"]',
                'input[name="text"]',
                'input[name="username_or_email"]',
                'input[type="text"]',
            ],
        )
        password_input = _visible_input(page, ['input[name="password"]', 'input[type="password"]'])
        if challenge_input and not password_input:
            # X 有时会要求补充邮箱或用户名，优先使用邮箱字段继续登录。
            challenge_input.fill(email or username)
            _click_button(page, ["Next", "下一步", "继续"])
            page.wait_for_timeout(1200)

        password_input = _visible_input(page, ['input[name="password"]', 'input[type="password"]'])
        if password_input:
            password_input.fill(password)
            _click_button(page, ["Log in", "Login", "登录"])

        page.wait_for_timeout(1200)
        code_input = _visible_input(
            page,
            [
                'input[autocomplete="one-time-code"]',
                'input[inputmode="numeric"]',
                'input[name="text"]',
            ],
        )
        if code_input and totp_secret:
            code_input.fill(totp(totp_secret))
            _click_button(page, ["Next", "Verify", "Log in", "Login", "下一步", "验证", "登录"])

        # 某些账号会出现额外风控页；保留可见浏览器让用户完成一次性挑战。
        deadline = time.time() + 45
        payload = None
        while time.time() < deadline:
            payload = _try_cookie_payload(context.cookies(["https://x.com", "https://twitter.com"]))
            if payload:
                break
            page.wait_for_timeout(1000)

        if not payload:
            raise RuntimeError("Login did not produce a usable X session within 45 seconds")
        _write_payload(output, payload)
        context.close()


def main() -> int:
    """读取环境变量凭据，并导出最新可用会话。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".twitter-session.json"))
    parser.add_argument("--profile", type=Path, default=Path.home() / ".twitter-cli-x-profile")
    args = parser.parse_args()

    username = os.environ.get("X_USERNAME", "")
    password = os.environ.get("X_PASSWORD", "")
    totp_secret = os.environ.get("X_TOTP_SECRET", "")
    email = os.environ.get("X_EMAIL", "")
    auth_token = os.environ.get("X_AUTH_TOKEN", "")
    ct0 = os.environ.get("X_CT0", "")
    cookie_base64 = os.environ.get("X_COOKIE_BASE64", "")

    try:
        if auth_token or cookie_base64:
            export_from_token(auth_token, args.output, args.profile, ct0=ct0, cookie_base64=cookie_base64)
        else:
            if not username or not password:
                print("Set X_AUTH_TOKEN or X_USERNAME and X_PASSWORD before running.", file=sys.stderr)
                return 2
            run(username, password, totp_secret, args.output, args.profile, email=email)
    except Exception as exc:
        print(f"Login/export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Session exported to {args.output}")
    print(f"Run: TWITTER_COOKIE_FILE={args.output} twitter status --yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
