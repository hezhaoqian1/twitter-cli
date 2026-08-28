# Kredo Wallet Login Flow

Checked on 2026-08-28 against `https://kredo.fun/`.

## Site Behavior

Kredo is a prediction-market web app using Privy for authentication. The top-right login modal offers:

- MetaMask
- Coinbase Wallet
- Rainbow
- Other wallets
- Email or social login

Clicking wallet login loads Privy resources from `https://auth.privy.io` and expects an EIP-1193 browser wallet provider.

## Verified Login Path

The tested private key derives to:

```text
0x5a29Eb88D35ADfd305dF2dC3F0188eFE327E1F0E
```

The successful flow was:

1. Inject a local EIP-1193 provider before page load.
2. Respond to `eth_accounts`, `eth_requestAccounts`, `eth_chainId`, and `wallet_switchEthereumChain`.
3. Reject transaction methods such as `eth_sendTransaction`.
4. Sign only the Privy login challenge sent through `personal_sign`.
5. After signing, Kredo shows the logged-in wallet state with portfolio text and the shortened address.

Observed logged-in markers:

```text
資產組合
0x5a2...1f0e
```

## Notes

Kredo requested chain switch to `0xb1` during login. The login itself completed with message signing, not transaction submission.

## Current Verification Record

- Date: 2026-08-28
- Result: wallet login succeeded
- Address shown by the site: `0x5a2...1f0e`
- Wallet methods observed: account discovery, chain identification, chain switch, and message signing
- Transaction methods: deliberately rejected; no transfer, approval, wager, or other on-chain action was sent

## Credential Handling

The private key used for this verification was supplied in chat and must be treated as exposed. Do not place it in source files, `.env` files, screenshots, shell history, or logs. Rotate or migrate the wallet before using it for any asset-bearing activity.

## X Task OAuth Probe

On 2026-08-28, the wallet-authenticated task page returned the X task card and a
`100 HSK` reward. Clicking the first `前往 X` action called:

```text
POST https://api.kredo.fun/api/v1/tasks/twitter/bind
```

The response is an envelope. The authorization URL is under
`data.authorizeUrl`, and the OAuth state is under `data.state`.

The X session passed both `twitter status --yaml` and a direct `x.com/home`
check. The important endpoint distinction is:

- Browser consent page: `/i/oauth2/authorize`
- Structured authorization response: `/i/api/2/oauth2/authorize`

Using the full X request headers and the project Bearer token, the structured
endpoint returned an `auth_code`. The Kredo callback then returned HTTP 200 and
redirected to `/tasks`, but a subsequent request to the actual API host still
reported:

```json
{
  "status": "unbound",
  "boundHandle": null,
  "repostVerified": false,
  "needsRebind": false
}
```

Therefore the current evidence proves wallet login, Kredo bind initiation, and
X authorization-code issuance. It does not prove successful X account binding.
No repost, task claim, or other public account action was completed.

The earlier probe also used relative requests such as
`/api/v1/tasks/twitter`; those resolve to the Kredo frontend and return the SPA
HTML. Task state must be read from `https://api.kredo.fun`.

For repeatable diagnostics, `scripts/kredo_wallet_login_probe.py`:

- Reads the private key from `KREDO_PRIVATE_KEY`.
- Injects an EIP-1193 provider.
- Logs wallet RPC method names without printing secrets.
- Rejects transaction methods.
- Writes a screenshot or JSON status report after login.

The X/Kredo callback should remain a diagnostic-only step until the backend
accepts the returned code and the API reports a non-`unbound` state.

## Why An Existing X Login Is Not Enough

An existing X session only skips the X login screen. The binding sequence still
has three separate transitions:

1. Kredo creates a bind transaction and returns `data.authorizeUrl`.
2. X authorizes the application and returns an OAuth authorization code.
3. Kredo receives the callback, exchanges the code, validates the state, and
   persists the X account association.

The first automated run completed steps 1 and 2, but the initial browser
observation was:

```text
https://www.kredo.fun/tasks?twitter=failed
```

The later headed run showed the X consent page, and manually reopening the task
modal after a short delay displayed `MakylaGaylord 已绑定`. This confirms that
the callback can finish before the task card updates. A single immediate read
of `unbound` is therefore not conclusive; the probe must keep reloading the
task page and polling the task detail or overview response.

The probe records the following evidence without printing secrets:

- bind API response status and sanitized envelope fields;
- callback response status and sanitized redirect route;
- final `twitter` callback result;
- task API state after repeated reloads;
- browser console and page errors.

`repost` and `claim` remain disabled in this diagnostic probe. They should only
be tested after the task API reports a successfully bound account.

## Two-stage Task Interaction

The task card has two separate UI actions:

1. The task-list action `去完成` / `Start` opens the X task modal only.
2. The modal action `綁定 X 賬號` / `Connect an X account` creates a new
   `about:blank` popup, calls `POST /api/v1/tasks/twitter/bind`, then navigates
   that popup to `data.authorizeUrl`.

The probe reports these transitions independently:

```json
{
  "task_modal_opened": true,
  "bind_clicked": true,
  "bind_api_seen": true,
  "oauth_popup_opened": true
}
```

`bind_clicked: true` only proves that the modal button was clicked. Successful
bind initiation also requires `bind_api_seen: true` and a non-empty redacted
`authorize_url`; the callback result and task API state determine whether Kredo
persisted the X association. The probe also records `popup_url` and
`oauth_navigation_fallback`; the latter is true when Kredo returned an
`authorizeUrl` but the browser popup stayed at `about:blank`.

After `Authorize app`, the X page is expected to remain open while the OAuth
code is sent to Kredo. The probe now polls until one of these states occurs:

- `kredo_callback`: the popup reached Kredo's callback endpoint;
- `kredo_tasks`: Kredo redirected back to the task page;
- `x_oauth_error`: X returned an OAuth error;
- `popup_closed`: the popup closed before the redirect;
- `timeout`: no terminal state appeared before the configured timeout.

The diagnostic output keeps these values separate:

- `callback_location`: the redacted `Location` header returned by Kredo's
  callback response;
- `callback_url`: the popup's final URL;
- `final_task_url`: the last Kredo task-page URL observed across all tabs;
- `navigation_events`: top-level popup navigation history;
- `request_failures`: redacted browser request failures.

This lets us distinguish a slow browser transition from a Kredo callback
exchange or persistence failure. A final `kredo_tasks` URL with
`task_state.status: "unbound"` during the propagation window means the result
is still pending. The final verdict should be made after the polling deadline;
`status: "bound"` with a non-empty `boundHandle` is the successful state.

## Eventual Consistency

After X redirects back to Kredo, the first task-card render can still show the
old state. Reopening the task modal or refreshing the task page causes the
latest `twitterQuest` state to appear. The probe now polls for up to 60 seconds
and reads both `/tasks/twitter` and `/tasks/overview` so this UI delay does not
become a false failure.

Run it with:

```bash
KREDO_PRIVATE_KEY="PRIVATE_KEY" uv run --with playwright --with eth-account \
  python scripts/kredo_wallet_login_probe.py
```

For a visible manual verification that leaves the final task page open:

```bash
KREDO_PRIVATE_KEY="PRIVATE_KEY" TWITTER_COOKIE_FILE=".twitter-session-token.json" \
  uv run --with playwright --with eth-account \
  python scripts/kredo_wallet_login_probe.py --headed --keep-open --timeout 90
```
