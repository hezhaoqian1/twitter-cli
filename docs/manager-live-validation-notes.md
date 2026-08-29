# Manager Live Validation Notes

Date: 2026-08-29

This note records aggregate live-validation evidence only. It must not contain
database URLs, Redis URLs, passwords, recovery keys, private keys, account
cookies, tokens, email addresses, or full wallet addresses.

## Current Aggregate State

The live manager database currently contains:

- accounts: 5 active, 5 healthy;
- wallets: 5 active;
- bindings: 5 pending, 0 bound;
- tasks:
  - `verify_account`: 5 succeeded;
  - `bind`: 1 failed, 2 queued, 2 waiting external validation;
  - `repost`: 0;
  - `claim`: 0.

The stage summary after the staged-readiness fix is:

```text
verify: ready=5 waiting=0 failed=0
bind: ready=0 waiting=5 failed=1
repost: ready=0 waiting=0 failed=0
claim: ready=0 waiting=0 failed=0
```

## Browser Evidence

The latest binding screenshots showed two distinct outcomes:

- one browser page reached the X OAuth authorization screen and stayed on
  `Authorize app`;
- one Kredo task page still displayed the X task as incomplete, with the
  wallet logged in and the task CTA visible.

That means the current live run is still in the bind stage. It has not reached
the repost or claim stage yet.

## Code Corrections From This Evidence

- Fast binding mode now briefly waits for the OAuth popup to leave the X
  authorization page before returning.
- If the popup stays on the authorization page, the probe reports
  `authorize_not_completed`.
- The worker maps `authorize_not_completed`, OAuth `401`/`403`, and missing
  authorize URL to failed bind outcomes.
- A waiting bind status poll that sees Kredo still reporting `unbound` exits as
  failed instead of waiting forever.
- The operations summary counts pending bindings once; it no longer adds
  pending binding rows and waiting bind tasks together.

## Operational Interpretation

The fully automated bind worker is not the primary path for the current Kredo
operator flow. The working product direction is semi-automatic:

1. the manager imports X sessions and wallet material into the Vault;
2. the binding page creates immutable account-wallet rows;
3. `打开工作台` launches up to 10 headed browsers at a time;
4. each browser is preloaded with the row's X Cookie and wallet provider;
5. the main tab stays on Kredo's task page;
6. the operator clicks Kredo's `前往 X`, completes X binding and reposting in
   the separate X tab, and closes that tab when finished;
7. the operator manually finishes Kredo task refresh and claim in the main tab.

The next real run should focus on workbench validation:

1. Start one workbench from the binding page and confirm it opens Kredo with
   the expected X and wallet state.
2. Click Kredo's `前往 X` and confirm the X tab opens, stays open, and leaves
   reposting to the operator.
3. Click the Kredo modal's manual X-binding action and confirm an
   `about:blank` popup is redirected to the X authorization page.
4. Complete the Kredo binding manually in the main tab.
5. Refresh the manager binding page or run read-only sync after Kredo updates.
6. Scale the same action to a wave of up to 10 browser windows.

The synthetic acceptance path already proves the manager can execute
independent stage batches through bind, delayed repost polling, and claim when
the provider states advance as expected.
