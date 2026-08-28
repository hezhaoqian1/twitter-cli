"""Account TSV parsing, classification, and encrypted persistence."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from ..models.imports import ImportBatch, ImportRow, ImportRowStatus
from .vault import VaultService

ACCOUNT_TSV_COLUMNS = (
    "handle",
    "password",
    "totp",
    "email",
    "email_password",
    "token",
    "cookie",
)
REQUIRED_COLUMN_COUNT = len(ACCOUNT_TSV_COLUMNS)


@dataclass(frozen=True)
class ParsedAccount:
    """Parsed secret material held only for the current service call."""

    handle: str
    password: str
    totp: str
    email: str
    email_password: str
    token: str
    cookie: str

    def secret_payload(self) -> dict[str, str]:
        """Return the normalized secret fields before encryption."""
        return {
            "password": self.password,
            "totp": self.totp,
            "email_password": self.email_password,
            "token": self.token,
            "cookie": self.cookie,
        }


@dataclass
class ImportDecision:
    """Internal row decision; API conversion deliberately omits secrets."""

    line_number: int
    status: ImportRowStatus
    parsed: ParsedAccount | None = None
    diagnostic_code: str | None = None
    diagnostic_detail: str | None = None
    handle_masked: str | None = None
    email_masked: str | None = None
    fingerprint: str | None = None


@dataclass
class ImportPreview:
    """Prepared import result shared by preview and commit."""

    source_sha256: str
    decisions: list[ImportDecision]

    @property
    def total_rows(self) -> int:
        """Return the number of physical input rows considered."""
        return len(self.decisions)


class AccountImportService:
    """Own the account TSV contract and encrypted account insertion."""

    def __init__(self, session: Session, vault: VaultService | None = None) -> None:
        self.session = session
        self.vault = vault or VaultService(session)

    def preview(self, content: str | bytes) -> ImportPreview:
        """Parse and classify rows without persisting source or secret values."""
        raw = self._as_utf8_bytes(content)
        decisions = self._classify(raw.decode("utf-8"))
        return ImportPreview(source_sha256=hashlib.sha256(raw).hexdigest(), decisions=decisions)

    def commit(
        self,
        content: str | bytes,
        *,
        source_name: str | None = None,
    ) -> tuple[ImportBatch, ImportPreview]:
        """Persist every row outcome and encrypt each accepted secret field."""
        prepared = self.preview(content)
        batch = ImportBatch(
            source_name=source_name,
            source_sha256=prepared.source_sha256,
            total_rows=prepared.total_rows,
            committed_rows=0,
            skipped_rows=0,
            malformed_rows=0,
        )
        self.session.add(batch)
        self.session.flush()

        for decision in prepared.decisions:
            result_metadata: dict[str, object] = {}
            account_id: UUID | None = None
            status = decision.status

            if decision.status is ImportRowStatus.VALID and decision.parsed is not None:
                account = self._create_account(decision.parsed)
                self.session.flush()
                self._create_secret(account, decision.parsed)
                account_id = account.id
                status = ImportRowStatus.COMMITTED
                batch.committed_rows += 1
                result_metadata["decision"] = "committed"
            else:
                if decision.status is ImportRowStatus.MALFORMED:
                    batch.malformed_rows += 1
                else:
                    batch.skipped_rows += 1
                result_metadata["decision"] = "skipped"

            row = ImportRow(
                import_batch_id=batch.id,
                line_number=decision.line_number,
                status=status,
                handle_masked=decision.handle_masked,
                email_masked=decision.email_masked,
                diagnostic_code=decision.diagnostic_code,
                diagnostic_detail=decision.diagnostic_detail,
                result_metadata=result_metadata,
                social_account_id=account_id,
            )
            self.session.add(row)

        self.session.flush()
        return batch, prepared

    def _classify(self, text: str) -> list[ImportDecision]:
        rows = [line.split("\t") for line in text.splitlines()]
        parsed_rows: list[tuple[int, ParsedAccount]] = []
        decisions: list[ImportDecision] = []

        for line_number, columns in enumerate(rows, start=1):
            parsed = self._parse_row(columns)
            if parsed is None:
                decisions.append(
                    ImportDecision(
                        line_number=line_number,
                        status=ImportRowStatus.MALFORMED,
                        diagnostic_code="invalid_column_count_or_empty_field",
                        diagnostic_detail=f"expected {REQUIRED_COLUMN_COUNT} non-empty columns",
                    )
                )
                continue
            parsed_rows.append((line_number, parsed))

        normalized_handles = {self._normalize_handle(parsed.handle) for _, parsed in parsed_rows}
        existing_accounts = self._existing_accounts(normalized_handles)
        seen_handles: set[str] = set()
        for line_number, parsed in parsed_rows:
            normalized_handle = self._normalize_handle(parsed.handle)
            handle_masked = self._mask_handle(parsed.handle)
            email_masked = self._mask_email(parsed.email)
            fingerprint = self._fingerprint(parsed)

            if normalized_handle in seen_handles:
                decisions.append(
                    ImportDecision(
                        line_number=line_number,
                        status=ImportRowStatus.DUPLICATE_IN_FILE,
                        parsed=parsed,
                        diagnostic_code="duplicate_handle_in_file",
                        diagnostic_detail="same normalized handle appeared earlier in this file",
                        handle_masked=handle_masked,
                        email_masked=email_masked,
                        fingerprint=fingerprint,
                    )
                )
                continue
            seen_handles.add(normalized_handle)

            existing = existing_accounts.get(normalized_handle)
            if existing is not None:
                if self._is_conflicting_active_session(existing, fingerprint):
                    status = ImportRowStatus.CONFLICTING_SESSION
                    code = "active_session_conflict"
                    detail = "an active account already has different session material"
                else:
                    status = ImportRowStatus.EXISTING_ACCOUNT
                    code = "account_already_exists"
                    detail = "normalized handle already exists"
                decisions.append(
                    ImportDecision(
                        line_number=line_number,
                        status=status,
                        parsed=parsed,
                        diagnostic_code=code,
                        diagnostic_detail=detail,
                        handle_masked=handle_masked,
                        email_masked=email_masked,
                        fingerprint=fingerprint,
                    )
                )
                continue

            decisions.append(
                ImportDecision(
                    line_number=line_number,
                    status=ImportRowStatus.VALID,
                    parsed=parsed,
                    handle_masked=handle_masked,
                    email_masked=email_masked,
                    fingerprint=fingerprint,
                )
            )

        return sorted(decisions, key=lambda decision: decision.line_number)

    def _existing_accounts(self, normalized_handles: set[str]) -> dict[str, SocialAccount]:
        if not normalized_handles:
            return {}
        accounts = self.session.scalars(
            select(SocialAccount).where(SocialAccount.normalized_handle.in_(normalized_handles))
        ).all()
        return {account.normalized_handle: account for account in accounts}

    @staticmethod
    def _parse_row(columns: list[str]) -> ParsedAccount | None:
        if len(columns) != REQUIRED_COLUMN_COUNT or any(not column.strip() for column in columns):
            return None
        values = [column.strip() for column in columns]
        return ParsedAccount(*values)

    @staticmethod
    def _as_utf8_bytes(content: str | bytes) -> bytes:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        raw.decode("utf-8")
        return raw

    @staticmethod
    def _normalize_handle(handle: str) -> str:
        return handle.strip().lstrip("@").casefold()

    @staticmethod
    def _mask_handle(handle: str) -> str:
        visible = handle.strip().lstrip("@")
        if len(visible) <= 2:
            return "*" * len(visible)
        return f"{visible[:2]}***{visible[-1:]}"

    @staticmethod
    def _mask_email(email: str) -> str:
        local, separator, domain = email.partition("@")
        if not separator:
            return "***"
        visible_local = local[:1] if local else "*"
        return f"{visible_local}***@{domain}"

    @staticmethod
    def _fingerprint(parsed: ParsedAccount) -> str:
        payload = json.dumps(
            parsed.secret_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _is_conflicting_active_session(account: SocialAccount, fingerprint: str) -> bool:
        return (
            account.state is LifecycleState.ACTIVE
            and account.secret is not None
            and account.secret.secret_fingerprint != fingerprint
        )

    def _create_account(self, parsed: ParsedAccount) -> SocialAccount:
        account = SocialAccount(
            handle=parsed.handle.lstrip("@"),
            normalized_handle=self._normalize_handle(parsed.handle),
            email_masked=self._mask_email(parsed.email),
            state=LifecycleState.ACTIVE,
            health=AccountHealth.UNKNOWN,
        )
        self.session.add(account)
        return account

    def _create_secret(self, account: SocialAccount, parsed: ParsedAccount) -> None:
        secret = AccountSecret(
            social_account_id=account.id,
            version=1,
            is_current=True,
            envelope=b"pending",
            envelope_version=1,
            secret_fingerprint=self._fingerprint(parsed),
            redacted_metadata=json.dumps(
                {"fields": sorted(parsed.secret_payload()), "format": "account-tsv-v1"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self.session.add(secret)
        self.session.flush()
        encrypted_fields = {
            field_name: base64.urlsafe_b64encode(
                self.vault.encrypt_field(
                    "account_secrets",
                    secret.id,
                    field_name,
                    field_value,
                )
            ).decode("ascii")
            for field_name, field_value in parsed.secret_payload().items()
        }
        secret.envelope = json.dumps(
            encrypted_fields,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def summarize_preview(preview: ImportPreview) -> dict[str, int]:
    """Build safe summary counts for API schemas and tests."""
    counts = {
        "total_rows": preview.total_rows,
        "valid_rows": 0,
        "malformed_rows": 0,
        "duplicate_rows": 0,
        "existing_rows": 0,
        "conflicting_rows": 0,
        "committed_rows": 0,
        "skipped_rows": 0,
    }
    for decision in preview.decisions:
        if decision.status is ImportRowStatus.VALID:
            counts["valid_rows"] += 1
        elif decision.status is ImportRowStatus.MALFORMED:
            counts["malformed_rows"] += 1
        elif decision.status is ImportRowStatus.DUPLICATE_IN_FILE:
            counts["duplicate_rows"] += 1
        elif decision.status is ImportRowStatus.EXISTING_ACCOUNT:
            counts["existing_rows"] += 1
        elif decision.status is ImportRowStatus.CONFLICTING_SESSION:
            counts["conflicting_rows"] += 1
        elif decision.status is ImportRowStatus.COMMITTED:
            counts["committed_rows"] += 1
    counts["skipped_rows"] = (
        counts["malformed_rows"]
        + counts["duplicate_rows"]
        + counts["existing_rows"]
        + counts["conflicting_rows"]
    )
    return counts
