from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path("scripts/kredo_wallet_login_probe.py")
    spec = importlib.util.spec_from_file_location("kredo_wallet_login_probe", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_redact_url_preserves_route_and_hides_oauth_values() -> None:
    mod = _load_module()

    result = mod._redact_url(
        "https://api.kredo.fun/api/v1/tasks/twitter/callback"
        "?code=secret-code&state=secret-state&twitter=failed"
    )

    assert result == (
        "https://api.kredo.fun/api/v1/tasks/twitter/callback"
        "?code=%5BREDACTED%5D&state=%5BREDACTED%5D&twitter=%5BREDACTED%5D"
    )


def test_unwrap_api_payload_redacts_nested_credentials() -> None:
    mod = _load_module()

    result = mod._unwrap_api_payload(
        {
            "success": True,
            "data": {
                "authorizeUrl": "https://x.com/i/oauth2/authorize?code=secret",
                "state": "secret-state",
                "status": "unbound",
            },
        }
    )

    assert result == {
        "success": True,
        "data": {
            "authorizeUrl": "https://x.com/i/oauth2/authorize?code=secret",
            "state": "[REDACTED]",
            "status": "unbound",
        },
    }


def test_task_state_reads_envelope_and_keeps_known_fields() -> None:
    mod = _load_module()

    result = mod._task_state_from_payload(
        {
            "success": True,
            "data": {
                "status": "unbound",
                "boundHandle": None,
                "repostVerified": False,
                "needsRebind": True,
                "failReason": "callback_exchange_failed",
                "tweetUrl": None,
                "ignored": "not copied",
            },
        }
    )

    assert result == {
        "status": "unbound",
        "boundHandle": None,
        "repostVerified": False,
        "needsRebind": True,
        "failReason": "callback_exchange_failed",
        "tweetUrl": None,
    }


def test_task_state_reads_nested_twitter_quest_from_overview() -> None:
    mod = _load_module()

    result = mod._task_state_from_payload(
        {
            "success": True,
            "data": {
                "twitterQuest": {
                    "status": "bound",
                    "boundHandle": "fixture-bound-handle",
                    "repostVerified": False,
                    "needsRebind": False,
                    "failReason": None,
                    "tweetUrl": None,
                }
            },
        }
    )

    assert result == {
        "status": "bound",
        "boundHandle": "fixture-bound-handle",
        "repostVerified": False,
        "needsRebind": False,
        "failReason": None,
        "tweetUrl": None,
    }


def test_latest_task_state_prefers_latest_detail_or_overview_response() -> None:
    mod = _load_module()

    result = mod._latest_task_state(
        [
            {
                "path": "/api/v1/tasks/overview",
                "payload": {
                    "data": {
                        "twitterQuest": {
                            "status": "unbound",
                            "boundHandle": None,
                        }
                    }
                },
            },
            {
                "path": "/api/v1/tasks/twitter",
                "payload": {
                    "data": {
                        "status": "bound",
                        "boundHandle": "fixture-bound-handle",
                    }
                },
            },
        ]
    )

    assert result == {
        "status": "bound",
        "boundHandle": "fixture-bound-handle",
        "repostVerified": None,
        "needsRebind": None,
        "failReason": None,
        "tweetUrl": None,
    }


def test_fetch_task_state_from_api_uses_logged_in_page_context() -> None:
    """状态判定可直接读取 Kredo 任务接口，而不依赖按钮显示。"""
    mod = _load_module()

    class FakePage:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def evaluate(self, _script: str, url: str) -> dict[str, object]:
            self.urls.append(url)
            return {
                "status": 200,
                "payload": {
                    "success": True,
                    "data": {
                        "status": "bound",
                        "boundHandle": "fixture-bound-handle",
                        "repostVerified": False,
                        "needsRebind": False,
                        "failReason": None,
                        "tweetUrl": None,
                    },
                },
            }

    responses: list[dict[str, object]] = []
    page = FakePage()

    result = mod._fetch_task_state_from_api(page, responses)

    assert result == {
        "status": "bound",
        "boundHandle": "fixture-bound-handle",
        "repostVerified": False,
        "needsRebind": False,
        "failReason": None,
        "tweetUrl": None,
    }
    assert page.urls == ["https://api.kredo.fun/api/v1/tasks/twitter"]
    assert responses[0]["path"] == "/api/v1/tasks/twitter"


def test_fetch_task_state_from_api_records_redacted_fetch_failures() -> None:
    """接口读取失败只记录稳定错误码，不把异常细节写入结果。"""
    mod = _load_module()

    class FakePage:
        def evaluate(self, _script: str, _url: str) -> dict[str, object]:
            raise RuntimeError("secret-cookie-fixture")

    responses: list[dict[str, object]] = []

    assert mod._fetch_task_state_from_api(FakePage(), responses) is None
    rendered = json.dumps(responses)
    assert "browser_fetch_failed" in rendered
    assert "secret-cookie-fixture" not in rendered


def test_callback_result_reads_final_tasks_redirect() -> None:
    mod = _load_module()

    assert mod._callback_result("https://www.kredo.fun/tasks?twitter=failed") == "failed"
    assert mod._callback_result("https://www.kredo.fun/tasks") is None


def test_oauth_url_state_distinguishes_callback_tasks_and_error() -> None:
    mod = _load_module()

    assert (
        mod._oauth_url_state(
            "https://api.kredo.fun/api/v1/tasks/twitter/callback?code=secret"
        )
        == "kredo_callback"
    )
    assert mod._oauth_url_state("https://www.kredo.fun/tasks?twitter=success") == "kredo_tasks"
    assert (
        mod._oauth_url_state("https://x.com/i/oauth2/authorize?error=access_denied")
        == "x_oauth_error"
    )
    assert mod._oauth_url_state("https://x.com/i/oauth2/authorize?state=waiting") == "pending"
    assert mod._oauth_url_state("", popup_closed=True) == "popup_closed"


def test_oauth_authorize_not_completed_is_a_distinct_completion_code() -> None:
    """快速绑定可用该状态区分未离开授权页和 Kredo 慢回写。"""
    mod = _load_module()

    assert "authorize_not_completed" not in {
        mod._oauth_url_state("https://x.com/i/oauth2/authorize?state=waiting")
    }


def test_latest_kredo_tasks_url_uses_last_matching_page() -> None:
    mod = _load_module()

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url

    pages = [
        FakePage("https://x.com/i/oauth2/authorize"),
        FakePage("https://www.kredo.fun/tasks?twitter=success"),
    ]

    assert mod._latest_kredo_tasks_url(pages) == pages[-1].url


def test_tweet_id_from_url_extracts_status_id() -> None:
    mod = _load_module()

    assert mod._tweet_id_from_url("https://x.com/kredo/status/1234567890?ref=task") == (
        "1234567890"
    )
    assert mod._tweet_id_from_url("https://x.com/kredo/post/1234567890") is None


def test_x_repost_state_detects_already_reposted_button() -> None:
    mod = _load_module()

    class FakeLocator:
        def __init__(self, visible: bool) -> None:
            self.visible = visible
            self.first = self

        def count(self) -> int:
            return 1

        def is_visible(self) -> bool:
            return self.visible

    class FakePage:
        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator('unretweet' in selector)

    assert mod._x_repost_state(FakePage()) == "already_reposted"


def test_click_first_visible_containing_handles_extra_button_text() -> None:
    mod = _load_module()

    class FakeLocator:
        def __init__(self, visible: bool) -> None:
            self.visible = visible
            self.clicked = False
            self.first = self

        def count(self) -> int:
            return 1

        def is_visible(self) -> bool:
            return self.visible

        def click(self, timeout: int) -> None:
            del timeout
            self.clicked = True

    class FakePage:
        def __init__(self) -> None:
            self.button = FakeLocator(visible=True)

        def get_by_role(self, role: str, name: str, exact: bool) -> FakeLocator:
            assert role == "button"
            assert name == "Connect an X account"
            assert exact is False
            return self.button

        def get_by_text(self, label: str, exact: bool) -> FakeLocator:
            del label, exact
            return FakeLocator(visible=False)

    page = FakePage()

    assert mod._click_first_visible_containing(page, ("Connect an X account",)) == (
        "Connect an X account"
    )
    assert page.button.clicked is True


def test_load_x_cookies_accepts_playwright_session_file(tmp_path: Path) -> None:
    mod = _load_module()
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "auth_token",
                        "value": "session-token",
                        "domain": ".x.com",
                        "path": "/",
                        "sameSite": "None",
                    },
                    {
                        "name": "ct0",
                        "value": "csrf",
                        "domain": ".x.com",
                        "path": "/",
                        "sameSite": "Lax",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = mod._load_x_cookies(session)

    assert result == [
        {
            "name": "auth_token",
            "value": "session-token",
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
        },
        {
            "name": "ct0",
            "value": "csrf",
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        },
    ]


def test_run_accepts_headed_mode_argument() -> None:
    mod = _load_module()

    assert "headed" in mod.run.__annotations__ or "headed" in mod.run.__code__.co_varnames
    assert "keep_open" in mod.run.__code__.co_varnames
