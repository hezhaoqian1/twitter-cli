# X Cookie Authentication Flow

This project supports three authentication inputs:

1. `TWITTER_COOKIE_FILE`: preferred for exported full-cookie sessions.
2. `X_AUTH_TOKEN` with `scripts/x_login_export.py`: writes `auth_token` into a browser, lets X refresh `ct0`, then exports a full session.
3. `TWITTER_AUTH_TOKEN` plus `TWITTER_CT0`: legacy minimal-cookie mode.

## Verified Result

On 2026-08-28, four TSV rows in the format below were checked through the full-cookie path:

```text
username password totp email email_password auth_token cookie_base64
```

Result:

| Rows | Status | Check |
|---:|---|---|
| 4 | pass | each reported username matched the input username |

The important finding is that the original Base64 Cookie blobs were valid when forwarded as a complete Cookie header through `TWITTER_COOKIE_FILE`. The earlier `403 code 353` failure came from using the minimal `auth_token + ct0` path instead of importing the full cookie set.

## Session Notes

The investigation found three separate behaviors:

1. Minimal environment auth can fail with `HTTP 403 code 353` even when the pasted Cookie blob is valid.
2. Importing the full Base64 Cookie list into a session JSON and passing it with `TWITTER_COOKIE_FILE` succeeds.
3. Token-only browser login can also work: write `auth_token` into a temporary browser profile, visit `https://x.com/home`, let X issue a fresh `ct0`, then export the resulting full Cookie set.

The durable rule for this project is: prefer full-cookie forwarding for pasted account exports. Use token-only refresh only when the full Cookie blob is unavailable or needs regeneration.

## Single Cookie Blob

Convert a decoded cookie list into a session file containing:

```json
{
  "auth_token": "AUTH_TOKEN",
  "ct0": "CT0",
  "cookie_string": "auth_token=AUTH_TOKEN; ct0=CT0; ...",
  "cookies": []
}
```

Then run:

```bash
export TWITTER_COOKIE_FILE="/path/to/session.json"
twitter status --yaml
```

## Batch Check

Use the batch checker for TSV files:

```bash
uv run python scripts/x_cookie_batch_status.py /path/to/accounts.tsv --repo "$PWD"
```

The script writes temporary `0600` session files, calls `twitter status --yaml`, and prints a redacted table. It does not persist account passwords, TOTP secrets, or full Cookie values.

## Token-Only Refresh

When only `auth_token` is available:

```bash
export X_AUTH_TOKEN="AUTH_TOKEN"
uv run --with playwright python scripts/x_login_export.py --output .twitter-session-token.json
export TWITTER_COOKIE_FILE="$PWD/.twitter-session-token.json"
twitter status --yaml
```

This browser path lets X generate a fresh `ct0` and related runtime cookies. Use it when the full-cookie blob is missing or stale.
