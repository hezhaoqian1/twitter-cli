# Windows Local Manager Runbook

This runbook is for running the manager UI, API, and semi-automatic headed
browser workbench on a Windows desktop.

Do not commit real database URLs, Redis URLs, Vault passwords, recovery keys,
account cookies, or wallet private keys. Put them in the untracked
`.env.manager` file only.

## Prerequisites

Install these once:

```powershell
winget install Git.Git
winget install Python.Python.3.13
winget install OpenJS.NodeJS.LTS
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart PowerShell after installing `uv`, then verify:

```powershell
git --version
python --version
node --version
npm --version
uv --version
```

## Get The Code

```powershell
git clone https://github.com/hezhaoqian1/twitter-cli.git
cd twitter-cli
git fetch origin --prune
git switch codex/kredo-windows-workbench
```

When updating an existing checkout:

```powershell
cd twitter-cli
git fetch origin --prune
git pull --ff-only
```

## Create `.env.manager`

Copy the example file and fill in real values locally:

```powershell
Copy-Item .env.manager.example .env.manager
notepad .env.manager
```

Required values:

```text
DATABASE_URL=postgresql://HOST:PORT/DATABASE
PGUSER=USER
PGPASSWORD=PASSWORD
REDIS_URL=redis://HOST:PORT/0
SESSION_SECRET=replace-with-a-random-secret-at-least-16-characters
WORKER_VAULT_PASSWORD=replace-with-your-vault-unlock-password
MANAGER_KREDO_WORKFLOW_FACTORY=manager_api.adapters.kredo_browser_workflow:kredo_workflow_factory
MANAGER_KREDO_BROWSER_ARTIFACT_DIR=artifacts/kredo-worker
MANAGER_KREDO_BROWSER_TIMEOUT_SECONDS=120
MANAGER_KREDO_BROWSER_HEADED=false
WORKER_CONCURRENCY=3
BROWSER_CONCURRENCY=2
```

If the provider gives you password-bearing database or Redis connection
strings, place those full values only in `.env.manager`. Keep tracked docs and
examples in the redacted form above.

If the Vault already exists, use the same `WORKER_VAULT_PASSWORD` that was used
to initialize it. Keep the recovery key in your password manager or offline
notes; it is only needed for backup verification or recovery.

## Install Dependencies

From the repository root:

```powershell
uv sync --extra dev
uv run playwright install chromium
npm install --prefix manager_ui
```

## Run Database Migrations

```powershell
uv run python scripts/manager_migrate.py
```

## Start The API

Open PowerShell terminal 1:

```powershell
cd twitter-cli
Get-Content .env.manager | ForEach-Object {
  if ($_ -and -not $_.TrimStart().StartsWith("#")) {
    $name, $value = $_.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}
uv run --with uvicorn python -m uvicorn manager_api.main:create_app --factory --host 127.0.0.1 --port 8000
```

Check readiness from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

Expected result:

```text
status ok, postgres ok, redis ok
```

## Start The UI

Open PowerShell terminal 2:

```powershell
cd twitter-cli
$env:VITE_API_TARGET = "http://127.0.0.1:8000"
npm run dev --prefix manager_ui -- --host 127.0.0.1 --port 5178
```

Open:

```text
http://127.0.0.1:5178/
```

## Use The Semi-Automatic Workbench

The workbench is the current primary path for Kredo binding and claiming.

1. Open `Vault` and unlock if needed.
2. Open `账号` and import the seven-column X account TSV.
3. Open `地址` and import private keys or derive addresses from a mnemonic.
4. Create account-wallet binding rows.
5. Open `绑定`.
6. Select up to 10 non-archived rows.
7. Select `打开工作台`.

The API starts one independent headed browser process per selected row. Each
process loads that row's X Cookie, injects that row's wallet provider, opens
Kredo, submits or confirms the fixed repost target, and stays open for manual
binding or claiming.

Default repost target:

```text
https://x.com/Kredofun/status/2092911885209444742
```

The API response only contains binding id, process id, screenshot path, and
repost target. It does not return X cookies, tokens, passwords, TOTP seeds,
private keys, or mnemonics.

## Optional Worker

The semi-automatic workbench does not require the Worker loop. Start the Worker
only when you want queued verify, bind, repost, claim, balance, or status jobs
to run in the background.

Open PowerShell terminal 3:

```powershell
cd twitter-cli
Get-Content .env.manager | ForEach-Object {
  if ($_ -and -not $_.TrimStart().StartsWith("#")) {
    $name, $value = $_.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}
uv run python scripts/manager_worker.py
```

## Local Validation Commands

Run these before pushing changes or moving to another machine:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy manager_api
npm run build --prefix manager_ui
git diff --check
```

## Troubleshooting

If `uv` is not found, restart PowerShell or add the path printed by the uv
installer to your user `PATH`.

If the API is healthy but the UI shows request failures, make sure the UI was
started with:

```powershell
$env:VITE_API_TARGET = "http://127.0.0.1:8000"
```

If clicking `打开工作台` does not open a visible browser, confirm the API is
running inside an interactive Windows desktop session. Do not run the API as a
Windows Service for this mode.

If Playwright reports that Chromium is missing, run:

```powershell
uv run playwright install chromium
```

If Railway access requires a local HTTP proxy, start the API through the tunnel:

```powershell
uv run python scripts/manager_clash_tunnel.py --proxy http://127.0.0.1:7890 --postgres-port 45462 --redis-port 46402 --idle-timeout 3600 -- uv run --with uvicorn python -m uvicorn manager_api.main:create_app --factory --host 127.0.0.1 --port 8000
```
