from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
from typing import Any

import pytest

from manager_api.adapters import (
    AccountHealthResult,
    AccountMaterial,
    AdapterError,
    AdapterEvidence,
    ExternalStatus,
    KredoAdapter,
    OperationMaterial,
    WalletMaterial,
    XAdapter,
    build_twitter_client_factory,
)
from manager_api.models.accounts import AccountHealth


ACCOUNT = AccountMaterial(
    handle="fixture-account",
    password="password-fixture",
    totp="totp-fixture",
    email="fixture@example.test",
    email_password="mail-password-fixture",
    token="token-fixture",
    cookie="cookie-fixture",
)
WALLET = WalletMaterial(
    address="0x" + "1" * 40,
    private_key="private-key-fixture",
)


@dataclass
class FakeProfile:
    id: str = "user-fixture"
    screen_name: str = "fixture-account"


class FakeTwitterClient:
    def __init__(self, *, reposted: bool = False, retweet_result: bool = True) -> None:
        self.reposted = reposted
        self.retweet_result = retweet_result
        self.retweet_calls: list[str] = []

    def fetch_me(self) -> FakeProfile:
        return FakeProfile()

    def retweet(self, tweet_id: str) -> bool:
        self.retweet_calls.append(tweet_id)
        return self.retweet_result


def test_material_and_evidence_redact_secret_values() -> None:
    evidence = AdapterEvidence(
        code="fixture",
        summary="fixture",
        attributes={
            "token": "secret-token",
            "nested": {"cookie": "secret-cookie"},
            "visible": "ok",
        },
    )

    assert "secret-token" not in repr(ACCOUNT)
    assert "private-key-fixture" not in repr(WALLET)
    assert "secret-token" not in repr(evidence)
    assert evidence.to_dict()["attributes"] == {
        "token": "[REDACTED]",
        "nested": {"cookie": "[REDACTED]"},
        "visible": "ok",
    }


def test_x_verify_uses_injected_client_and_returns_normalized_health() -> None:
    clients: list[FakeTwitterClient] = []

    def factory(material: AccountMaterial) -> FakeTwitterClient:
        assert material is ACCOUNT
        client = FakeTwitterClient()
        clients.append(client)
        return client

    result = XAdapter(factory).verify_account(ACCOUNT)

    assert isinstance(result, AccountHealthResult)
    assert result.health is AccountHealth.HEALTHY
    assert result.handle == "fixture-account"
    assert result.user_id == "user-fixture"
    assert len(clients) == 1


def test_x_repost_is_idempotent_when_external_check_says_already_done() -> None:
    client = FakeTwitterClient()
    operation = OperationMaterial(kind="repost", target="https://x.com/user/status/12345")

    result = XAdapter(lambda _: client).repost(
        ACCOUNT,
        operation,
        already_reposted=lambda tweet_id: tweet_id == "12345",
    )

    assert result.status is ExternalStatus.ALREADY_COMPLETED
    assert result.operation_ref == "12345"
    assert client.retweet_calls == []


def test_x_repost_maps_false_confirmation_to_pending() -> None:
    client = FakeTwitterClient(retweet_result=False)
    operation = OperationMaterial(kind="repost", target="12345")

    result = XAdapter(lambda _: client).repost(ACCOUNT, operation)

    assert result.status is ExternalStatus.PENDING
    assert result.operation_ref == "12345"
    assert client.retweet_calls == ["12345"]


def test_x_provider_errors_are_typed_and_redacted() -> None:
    class RateLimitedClient(FakeTwitterClient):
        def retweet(self, tweet_id: str) -> bool:
            raise RuntimeError("provider-secret-fixture")

    with pytest.raises(AdapterError) as error:
        XAdapter(lambda _: RateLimitedClient()).repost(
            ACCOUNT,
            OperationMaterial(kind="repost", target="12345"),
        )

    assert error.value.code == "repost_provider_error"
    assert error.value.retryable is False
    assert "provider-secret-fixture" not in str(error.value)


def test_x_factory_errors_are_typed() -> None:
    def broken_factory(_: AccountMaterial) -> FakeTwitterClient:
        raise RuntimeError("factory-secret-fixture")

    with pytest.raises(AdapterError) as error:
        XAdapter(broken_factory).repost(
            ACCOUNT,
            OperationMaterial(kind="repost", target="12345"),
        )

    assert error.value.code == "repost_provider_error"
    assert "factory-secret-fixture" not in str(error.value)


def test_twitter_client_factory_extracts_auth_values_from_cookie_blob(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeTwitterClient:
        def __init__(
            self,
            auth_token: str,
            ct0: str,
            rate_limit_config: dict[str, object] | None,
            *,
            cookie_string: str,
        ) -> None:
            captured.update(
                auth_token=auth_token,
                ct0=ct0,
                rate_limit_config=rate_limit_config,
                cookie_string=cookie_string,
            )

    monkeypatch.setattr("twitter_cli.client.TwitterClient", FakeTwitterClient)
    cookie_blob = base64.b64encode(
        json.dumps(
            [
                {"name": "auth_token", "value": "blob-token"},
                {"name": "ct0", "value": "blob-csrf"},
                {"name": "lang", "value": "en"},
            ]
        ).encode("utf-8")
    ).decode("ascii")
    account = AccountMaterial(
        handle="fixture-account",
        token="column-token",
        cookie=cookie_blob,
    )

    client = build_twitter_client_factory({"requestDelay": 0})(account)

    assert isinstance(client, FakeTwitterClient)
    assert captured == {
        "auth_token": "blob-token",
        "ct0": "blob-csrf",
        "rate_limit_config": {"requestDelay": 0},
        "cookie_string": "auth_token=blob-token; ct0=blob-csrf; lang=en",
    }


def test_twitter_client_factory_uses_token_with_raw_cookie_header(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeTwitterClient:
        def __init__(
            self,
            auth_token: str,
            ct0: str,
            rate_limit_config: dict[str, object] | None,
            *,
            cookie_string: str,
        ) -> None:
            captured.update(
                auth_token=auth_token,
                ct0=ct0,
                rate_limit_config=rate_limit_config,
                cookie_string=cookie_string,
            )

    monkeypatch.setattr("twitter_cli.client.TwitterClient", FakeTwitterClient)
    account = AccountMaterial(
        handle="fixture-account",
        token="column-token",
        cookie="ct0=header-csrf; lang=en",
    )

    build_twitter_client_factory()(account)

    assert captured == {
        "auth_token": "column-token",
        "ct0": "header-csrf",
        "rate_limit_config": None,
        "cookie_string": "ct0=header-csrf; lang=en",
    }


def test_twitter_client_factory_rejects_incomplete_session() -> None:
    with pytest.raises(ValueError, match="auth_token and ct0"):
        build_twitter_client_factory()(AccountMaterial(handle="fixture-account"))


class FakeWorkflow(AbstractContextManager["FakeWorkflow"]):
    def __init__(
        self,
        *,
        status_payload: object,
        action_payload: object,
        calls: list[str],
    ) -> None:
        self.status_payload = status_payload
        self.action_payload = action_payload
        self.calls = calls

    def __enter__(self) -> FakeWorkflow:
        self.calls.append("enter")
        return self

    def __exit__(self, *args: Any) -> None:
        self.calls.append("exit")

    def status(self, operation: OperationMaterial) -> object:
        self.calls.append("status")
        return self.status_payload

    def bind(self, account: AccountMaterial, wallet: WalletMaterial, operation: OperationMaterial) -> object:
        self.calls.append("bind")
        return self.action_payload

    def repost(self, account: AccountMaterial, wallet: WalletMaterial, operation: OperationMaterial) -> object:
        self.calls.append("repost")
        return self.action_payload

    def claim(self, account: AccountMaterial, wallet: WalletMaterial, operation: OperationMaterial) -> object:
        self.calls.append("claim")
        return self.action_payload


def test_kredo_bind_maps_external_pending_and_closes_context() -> None:
    contexts: list[list[str]] = []

    def factory(operation: OperationMaterial) -> FakeWorkflow:
        calls: list[str] = []
        contexts.append(calls)
        return FakeWorkflow(
            status_payload={"status": "unknown"},
            action_payload={"status": "pending", "operation_ref": "op-fixture"},
            calls=calls,
        )

    result = KredoAdapter(factory).bind(
        ACCOUNT,
        WALLET,
        OperationMaterial(kind="bind"),
    )

    assert result.status is ExternalStatus.PENDING
    assert result.operation_ref == "op-fixture"
    assert contexts == [["enter", "bind", "exit"]]


def test_kredo_accepted_state_is_waiting_or_pending() -> None:
    calls: list[str] = []
    workflow = FakeWorkflow(
        status_payload={"status": "accepted"},
        action_payload={"status": "accepted"},
        calls=calls,
    )

    result = KredoAdapter(lambda _: workflow).bind(
        ACCOUNT,
        WALLET,
        OperationMaterial(kind="bind"),
    )

    assert result.status in {ExternalStatus.PENDING, ExternalStatus.WAITING}
    assert calls == ["enter", "bind", "exit"]


def test_kredo_factory_creates_one_context_per_call() -> None:
    workflows: list[FakeWorkflow] = []

    def factory(_: OperationMaterial) -> FakeWorkflow:
        workflow = FakeWorkflow(
            status_payload={"status": "waiting"},
            action_payload={"status": "succeeded"},
            calls=[],
        )
        workflows.append(workflow)
        return workflow

    adapter = KredoAdapter(factory)
    operation = OperationMaterial(kind="status")
    adapter.status(operation)
    adapter.status(operation)

    assert len(workflows) == 2
    assert workflows[0] is not workflows[1]
    assert all(workflow.calls == ["enter", "status", "exit"] for workflow in workflows)


def test_kredo_repost_checks_status_before_replaying_completed_action() -> None:
    calls: list[str] = []
    workflow = FakeWorkflow(
        status_payload={"status": "already_reposted", "operation_ref": "op-fixture"},
        action_payload={"status": "succeeded"},
        calls=calls,
    )

    result = KredoAdapter(lambda _: workflow).repost(
        ACCOUNT,
        WALLET,
        OperationMaterial(kind="repost", operation_ref="op-fixture"),
    )

    assert result.status is ExternalStatus.ALREADY_COMPLETED
    assert calls == ["enter", "status", "exit"]


def test_kredo_claim_preserves_delayed_status_without_calling_action() -> None:
    calls: list[str] = []
    workflow = FakeWorkflow(
        status_payload={"status": "pending_claim", "operation_ref": "claim-fixture"},
        action_payload={"status": "succeeded"},
        calls=calls,
    )

    result = KredoAdapter(lambda _: workflow).claim(
        ACCOUNT,
        WALLET,
        OperationMaterial(kind="claim", operation_ref="claim-fixture"),
    )

    assert result.status is ExternalStatus.PENDING
    assert result.operation_ref == "claim-fixture"
    assert calls == ["enter", "status", "exit"]


def test_kredo_status_normalizes_unbound_as_pending() -> None:
    calls: list[str] = []
    workflow = FakeWorkflow(
        status_payload={"status": "unbound", "boundHandle": None},
        action_payload={"status": "unknown"},
        calls=calls,
    )

    result = KredoAdapter(lambda _: workflow).status(
        OperationMaterial(kind="bind", operation_ref="bind-fixture"),
    )

    assert result.status is ExternalStatus.PENDING
    assert result.operation_ref == "bind-fixture"
    assert calls == ["enter", "status", "exit"]


def test_kredo_workflow_errors_are_typed_without_provider_message() -> None:
    class BrokenWorkflow(FakeWorkflow):
        def __enter__(self) -> BrokenWorkflow:
            raise RuntimeError("wallet-private-key-fixture")

    with pytest.raises(AdapterError) as error:
        KredoAdapter(
            lambda operation: BrokenWorkflow(
                status_payload={},
                action_payload={},
                calls=[],
            )
        ).status(OperationMaterial(kind="status"))

    assert error.value.code == "status_provider_error"
    assert "wallet-private-key-fixture" not in str(error.value)


def test_kredo_malformed_payload_is_a_typed_error() -> None:
    calls: list[str] = []
    workflow = FakeWorkflow(
        status_payload=[],
        action_payload={"status": "succeeded"},
        calls=calls,
    )

    with pytest.raises(AdapterError) as error:
        KredoAdapter(lambda _: workflow).status(OperationMaterial(kind="status"))

    assert error.value.code == "status_invalid_response"
