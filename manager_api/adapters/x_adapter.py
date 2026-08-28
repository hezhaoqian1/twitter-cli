"""Injected X adapter backed by the existing twitter-cli client contract."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from typing import Any

from ..models.accounts import AccountHealth
from .protocol import (
    AccountHealthResult,
    AccountMaterial,
    AdapterError,
    AdapterEvidence,
    ExternalOperation,
    ExternalStatus,
    OperationMaterial,
    TwitterClientProtocol,
    TwitterClientFactory,
)

_TWEET_ID_PATTERN = re.compile(r"/status/(\d+)")


def _cookie_values(raw_cookie: str) -> dict[str, str]:
    """从导入记录中提取 Cookie 名值，不把原始内容带入错误信息。"""
    value = raw_cookie.strip()
    if not value:
        return {}

    candidates: list[object] = []
    try:
        candidates.append(json.loads(value))
    except (TypeError, ValueError):
        pass
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if decoded:
        try:
            candidates.append(json.loads(decoded.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            pass

    for candidate in candidates:
        if isinstance(candidate, list):
            list_values = {
                str(item["name"]): str(item.get("value", ""))
                for item in candidate
                if isinstance(item, dict) and item.get("name")
            }
            if list_values:
                return list_values
        if isinstance(candidate, dict):
            nested = candidate.get("cookies")
            if isinstance(nested, list):
                nested_values = {
                    str(item["name"]): str(item.get("value", ""))
                    for item in nested
                    if isinstance(item, dict) and item.get("name")
                }
                if nested_values:
                    return nested_values
            dict_values = {
                str(key): str(item)
                for key, item in candidate.items()
                if key in {"auth_token", "ct0"} and item
            }
            if dict_values:
                return dict_values

    # 兼容已经展开的 "name=value; name2=value2" Cookie Header。
    header_values: dict[str, str] = {}
    for part in value.split(";"):
        name, separator, item_value = part.partition("=")
        if separator and name.strip() in {"auth_token", "ct0"}:
            header_values[name.strip()] = item_value.strip()
    return header_values


def _cookie_header(raw_cookie: str, values: dict[str, str]) -> str:
    """优先保留完整 Cookie，缺失时用解析出的认证 Cookie 重建请求头。"""
    if raw_cookie.strip() and ";" in raw_cookie and "=" in raw_cookie:
        return raw_cookie.strip()
    if values:
        return "; ".join(f"{name}={value}" for name, value in values.items())
    return ""


def build_twitter_client_factory(
    rate_limit_config: dict[str, Any] | None = None,
) -> TwitterClientFactory:
    """Build the manager's production bridge to the existing TwitterClient."""

    def factory(account: AccountMaterial) -> TwitterClientProtocol:
        """Construct one isolated client from one decrypted account material."""
        values = _cookie_values(account.cookie)
        auth_token = values.get("auth_token") or account.auth_token
        ct0 = values.get("ct0")
        if not auth_token or not ct0:
            raise ValueError("account session requires auth_token and ct0")

        # 延迟导入，避免 manager_api 的纯单元测试初始化网络客户端依赖。
        from twitter_cli.client import TwitterClient

        return TwitterClient(
            auth_token,
            ct0,
            rate_limit_config,
            cookie_string=_cookie_header(account.cookie, values),
        )

    return factory


class XAdapter:
    """Normalize X verification and repost calls without owning transport setup."""

    def __init__(self, client_factory: TwitterClientFactory) -> None:
        self._client_factory = client_factory

    def verify_account(self, account: AccountMaterial) -> AccountHealthResult:
        """Verify an account through a freshly created injected Twitter client."""
        try:
            profile = self._client_factory(account).fetch_me()
        except Exception as error:
            error_code = str(getattr(error, "error_code", "provider_error"))
            if error_code == "not_authenticated":
                return AccountHealthResult(
                    health=AccountHealth.INVALID,
                    handle=None,
                    user_id=None,
                    evidence=AdapterEvidence(
                        code="account_not_authenticated",
                        summary="X session is not authenticated",
                    ),
                )
            raise AdapterError.from_exception(
                "verify_account",
                error,
                retryable=error_code in {"network_error", "rate_limited"},
            ) from error

        handle = str(getattr(profile, "screen_name", "") or "").strip() or None
        user_id = str(getattr(profile, "id", "") or "").strip() or None
        if handle is None or user_id is None:
            raise AdapterError(
                "verify_account_invalid_response",
                "X returned an incomplete account profile",
            )
        return AccountHealthResult(
            health=AccountHealth.HEALTHY,
            handle=handle,
            user_id=user_id,
            evidence=AdapterEvidence(
                code="account_verified",
                summary="X account verified",
                attributes={"handle": handle, "user_id": user_id},
            ),
        )

    def repost(
        self,
        account: AccountMaterial,
        operation: OperationMaterial,
        *,
        already_reposted: Callable[[str], bool] | None = None,
    ) -> ExternalOperation:
        """Repost one tweet idempotently through the injected Twitter client."""
        tweet_id = self._tweet_id(operation.target)

        try:
            # 先读取外部完成态，避免重试把同一条推文重复提交。
            if already_reposted is not None and already_reposted(tweet_id):
                return ExternalOperation(
                    operation_ref=tweet_id,
                    status=ExternalStatus.ALREADY_COMPLETED,
                    evidence=AdapterEvidence(
                        code="already_reposted",
                        summary="X repost already exists",
                        attributes={"tweet_id": tweet_id},
                    ),
                )
            client = self._client_factory(account)
            accepted = client.retweet(tweet_id)
        except AdapterError:
            raise
        except Exception as error:
            error_code = str(getattr(error, "error_code", "provider_error"))
            raise AdapterError.from_exception(
                "repost",
                error,
                retryable=error_code in {"network_error", "rate_limited"},
            ) from error

        if not accepted:
            return ExternalOperation(
                operation_ref=tweet_id,
                status=ExternalStatus.PENDING,
                evidence=AdapterEvidence(
                    code="repost_pending",
                    summary="X accepted the repost request without immediate confirmation",
                    attributes={"tweet_id": tweet_id},
                ),
            )
        return ExternalOperation(
            operation_ref=tweet_id,
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence(
                code="reposted",
                summary="X repost completed",
                attributes={"tweet_id": tweet_id},
            ),
        )

    @staticmethod
    def _tweet_id(target: str | None) -> str:
        """Normalize a numeric tweet id from either an id or a status URL."""
        value = (target or "").strip()
        if value.isdigit():
            return value
        match = _TWEET_ID_PATTERN.search(value)
        if match is not None:
            return match.group(1)
        raise AdapterError(
            "invalid_repost_target",
            "repost target must be a numeric tweet id or status URL",
        )
