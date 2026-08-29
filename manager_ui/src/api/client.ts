export type Account = {
  id: string;
  handle: string;
  email_masked: string | null;
  state: string;
  health: string;
  has_secret: boolean;
};

export type Wallet = {
  id: string;
  address: string;
  source_type: "private_key" | "mnemonic" | null;
  derivation_path: string | null;
  derivation_index: number | null;
  state: string;
  has_secret: boolean;
  is_bound: boolean;
};

export type Binding = {
  id: string;
  social_account_id: string;
  wallet_id: string;
  account_handle: string;
  wallet_address: string;
  binding_key: string;
  state: string;
  bound_at: string | null;
  external_reference: string | null;
  archived_at: string | null;
  stage?: {
    repost_state: string | null;
    claim_state: string | null;
    can_repost: boolean;
    can_claim: boolean;
    repost_waiting: boolean;
    claim_waiting: boolean;
  };
  balance: Balance | null;
};

export type ManualWorkbenchResult = {
  launched: number;
  items: Array<{
    binding_id: string;
    process_id: number;
    screenshot: string;
    repost_target: string;
  }>;
};

export type Balance = {
  id: string | null;
  binding_id: string;
  account_handle: string;
  wallet_address: string;
  points: number | string | null;
  cash_hsk_available: number | string | null;
  positions_value_hsk: number | string | null;
  total_hsk: number | string | null;
  sync_status: string;
  error_code: string | null;
  last_synced_at: string | null;
};

export type TaskEvent = {
  id: string;
  sequence: number;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  summary: string | null;
  created_at: string;
};

export type Task = {
  id: string;
  kind: "bind" | "repost" | "claim" | "verify_account" | "balance_sync";
  state: string;
  attempt: number;
  priority: number;
  social_account_id: string | null;
  wallet_id: string | null;
  binding_id: string | null;
  depends_on_task_id: string | null;
  idempotency_key: string;
  lease_keys: string[];
  scheduled_at: string;
  started_at: string | null;
  finished_at: string | null;
  external_operation_ref: string | null;
  target_configured: boolean;
  result_summary: string | null;
  failure_code: string | null;
  poll_deadline_at: string | null;
  next_poll_at: string | null;
  cancel_requested_at: string | null;
  events: TaskEvent[];
};

export type TaskBatch = {
  id: string;
  name: string;
  kind: Task["kind"];
  workflow_type: string;
  state: string;
  dispatch_limit: number;
  created_at: string;
  paused_at: string | null;
  jobs: Task[];
};

export type VaultStatus = {
  initialized: boolean;
  unlocked: boolean;
  initialized_at: string | null;
};

export type VaultBackupSummary = {
  format_version: number;
  table_count: number;
  row_count: number;
  vault_recovery_key_valid: boolean;
  checksums_valid: boolean;
};

export type AccountImportRow = {
  line_number: number;
  status: string;
  handle_masked: string | null;
  email_masked: string | null;
  diagnostic_code: string | null;
  diagnostic_detail: string | null;
};

export type AccountImportResult = {
  source_sha256: string;
  summary: {
    total_rows: number;
    valid_rows: number;
    malformed_rows: number;
    duplicate_rows: number;
    existing_rows: number;
    conflicting_rows: number;
    committed_rows: number;
    skipped_rows: number;
  };
  rows: AccountImportRow[];
};

export type WalletPreview = {
  source_type: "private_key" | "mnemonic";
  label: string | null;
  summary: {
    total: number;
    valid: number;
    duplicate_in_file: number;
    duplicate_existing: number;
    committed: number;
    skipped: number;
  };
  wallets: Array<{
    index: number | null;
    address: string;
    derivation_path: string | null;
    status: string;
    diagnostic_code: string | null;
    diagnostic_detail: string | null;
  }>;
};

export type RuntimeMetrics = {
  generated_at: string;
  queues: {
    ready: number;
    processing: number;
  };
  tasks: {
    total: number;
    active: number;
    counts: Record<string, number>;
    last_finished_at: string | null;
  };
  leases: {
    active: number;
    expiring_soon: number;
  };
  workers: {
    active: number;
    heartbeat_at: string | null;
  };
};

export type OperationsSummary = {
  generated_at: string;
  resources: {
    accounts_total: number;
    accounts_active: number;
    accounts_healthy: number;
    accounts_available_for_binding: number;
    wallets_total: number;
    wallets_active: number;
    wallets_available_for_binding: number;
    bindings_total: number;
    bindings_pending: number;
    bindings_bound: number;
  };
  stages: Array<{
    key: "verify" | "bind" | "repost" | "claim";
    label: string;
    ready: number;
    waiting: number;
    failed: number;
    pollable: number;
    retryable: number;
    status_syncable: number;
    detail: string;
  }>;
};

export type NextStageRecommendation = {
  action: "poll" | "retry" | "create_stage" | "wait" | string;
  stage: "verify" | "bind" | "repost" | "claim" | null;
  command: string;
  reason: string;
};

export type AcceptanceAudit = {
  resources: Record<string, number>;
  stages: Array<{
    stage: "verify" | "bind" | "repost" | "claim";
    ready: number;
    waiting: number;
    failed: number;
    pollable: number;
    retryable: number;
    status_syncable: number;
  }>;
  next_action: NextStageRecommendation;
  actions: Array<{
    action: string;
    stage: "verify" | "bind" | "repost" | "claim";
    count: number;
    command: string;
  }>;
};

export type StagePollRequeueResult = {
  stage: "verify" | "bind" | "repost" | "claim";
  selected: number;
  requeued: number;
  skipped_missing_ref: number;
  apply: boolean;
};

export type StageRetryResult = {
  stage: "verify" | "bind" | "repost" | "claim";
  selected: number;
  retried: number;
  apply: boolean;
};

export type BindStatusSyncResult = {
  apply: boolean;
  name: string;
  limit: number;
  pending_bindings: number;
  selected: number;
  created_jobs: number;
  reused_jobs: number;
  paused_action_jobs: number;
  skipped_existing_status_job: number;
  skipped_active_lease: number;
  skipped_missing_secret: number;
};

export type PairedBindResult = {
  apply: boolean;
  name: string;
  limit: number;
  dispatch_limit: number;
  total_pairs: number;
  selected_pairs: number;
  created_jobs: number;
  counts: Record<string, number>;
};

export type HealthReady = {
  status: "ok" | "degraded";
  checks: Record<string, "ok" | "down">;
};

type Page<T> = {
  items: T[];
  offset: number;
  limit: number;
  total: number;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers
    }
  });
  if (!response.ok) {
    let message = "请求未完成";
    try {
      const body = (await response.json()) as { detail?: string | { message?: string } };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (body.detail?.message) {
        message = body.detail.message;
      }
    } catch {
      // The API might be restarting; keep the UI error concise.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(path, { method: "POST", body: form });
  if (!response.ok) {
    let message = "请求未完成";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // API restarting or returning a non-JSON error; keep the UI concise.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

async function requestBackup(path: string, form: FormData) {
  const response = await fetch(path, { method: "POST", body: form });
  if (!response.ok) {
    let message = "备份请求未完成";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep binary download failures readable without exposing response details.
    }
    throw new ApiError(response.status, message);
  }
  return {
    blob: await response.blob(),
    filename: response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ?? "manager-backup.json",
    formatVersion: response.headers.get("X-Backup-Format-Version"),
    tableCount: response.headers.get("X-Backup-Table-Count"),
    rowCount: response.headers.get("X-Backup-Row-Count")
  };
}

export const api = {
  healthReady: () => request<HealthReady>("/health/ready"),
  runtimeMetrics: () => request<RuntimeMetrics>("/api/runtime/metrics"),
  operationsSummary: () => request<OperationsSummary>("/api/runtime/operations-summary"),
  nextStage: () => request<NextStageRecommendation>("/api/runtime/next-stage"),
  acceptanceAudit: () => request<AcceptanceAudit>("/api/runtime/acceptance-audit"),
  vaultStatus: () => request<VaultStatus>("/api/vault/status"),
  initializeVault: (password: string) =>
    request<{ initialized: boolean; recovery_key: string }>("/api/vault/initialize", {
      method: "POST",
      body: JSON.stringify({ password })
    }),
  unlockVault: (password: string) =>
    request<VaultStatus>("/api/vault/unlock/password", {
      method: "POST",
      body: JSON.stringify({ password })
    }),
  lockVault: () => request<VaultStatus>("/api/vault/lock", { method: "POST" }),
  createBackup: (recoveryKey: string) => {
    const form = new FormData();
    form.set("recovery_key", recoveryKey);
    return requestBackup("/api/vault/backups", form);
  },
  verifyBackup: (recoveryKey: string, packageFile: File) => {
    const form = new FormData();
    form.set("recovery_key", recoveryKey);
    form.set("package", packageFile);
    return requestForm<VaultBackupSummary>("/api/vault/backups/verify", form);
  },
  restoreBackup: (recoveryKey: string, packageFile: File) => {
    const form = new FormData();
    form.set("recovery_key", recoveryKey);
    form.set("package", packageFile);
    return requestForm<VaultBackupSummary & { restored: boolean }>("/api/vault/restore", form);
  },
  accounts: () => request<Page<Account>>("/api/accounts?limit=500"),
  wallets: () => request<Page<Wallet>>("/api/wallets?limit=500"),
  bindings: () => request<Page<Binding>>("/api/bindings?limit=500"),
  balances: () => request<Page<Balance>>("/api/balances?limit=500"),
  tasks: () => request<Page<Task>>("/api/tasks?limit=500"),
  taskBatches: () => request<Page<TaskBatch>>("/api/tasks/batches?limit=100"),
  previewAccountImport: (content: string, sourceName: string) =>
    request<AccountImportResult>("/api/imports/accounts/preview", {
      method: "POST",
      body: JSON.stringify({ content, source_name: sourceName || null })
    }),
  commitAccountImport: (content: string, sourceName: string) =>
    request<AccountImportResult & { import_batch_id: string }>("/api/imports/accounts/commit", {
      method: "POST",
      body: JSON.stringify({ content, source_name: sourceName || null })
    }),
  previewWallet: (input: {
    source_type: "private_key" | "mnemonic";
    secret: string;
    label: string;
    start_index: number;
    count: number;
  }) =>
    request<WalletPreview>("/api/wallet-sources/preview", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  previewPrivateKeyBatch: (input: {
    content: string;
    label_prefix: string;
  }) =>
    request<WalletPreview>("/api/wallet-sources/private-keys/preview", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  commitPrivateKeyBatch: (input: {
    content: string;
    label_prefix: string;
  }) =>
    request<WalletPreview & { source_id: string | null }>("/api/wallet-sources/private-keys", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  commitWallet: (input: {
    source_type: "private_key" | "mnemonic";
    secret: string;
    label: string;
    start_index: number;
    count: number;
  }) =>
    request<WalletPreview & { source_id: string | null }>("/api/wallet-sources", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  createBinding: (socialAccountId: string, walletId: string) =>
    request<Binding>("/api/bindings", {
      method: "POST",
      body: JSON.stringify({
        social_account_id: socialAccountId,
        wallet_id: walletId
      })
    }),
  createTask: (input: {
    kind: Task["kind"];
    social_account_id?: string;
    wallet_id?: string;
    binding_id?: string;
    external_target: string;
    priority?: number;
  }) =>
    request<Task>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  createBatch: (input: {
    name: string;
    kind: Task["kind"];
    dispatch_limit: number;
    items: Array<{
      social_account_id?: string;
      wallet_id?: string;
      binding_id?: string;
      external_target: string;
      priority?: number;
    }>;
  }) =>
    request<TaskBatch>("/api/tasks/batches", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  createStageBatch: (input: {
    name: string;
    stage: "verify" | "bind" | "repost" | "claim";
    dispatch_limit: number;
    items: Array<{
      social_account_id?: string;
      wallet_id?: string;
      binding_id?: string;
      external_target?: string;
      priority?: number;
    }>;
  }) =>
    request<TaskBatch>("/api/tasks/stages", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  taskCommand: (id: string, command: "pause" | "cancel" | "retry" | "poll") =>
    request<Task>(`/api/tasks/${id}/${command}`, { method: "POST" }),
  batchCommand: (id: string, command: "pause" | "resume" | "cancel") =>
    request<TaskBatch>(`/api/tasks/batches/${id}/${command}`, { method: "POST" }),
  requeueStagePolls: (input: {
    stage: "bind" | "repost" | "claim";
    limit: number;
    apply: boolean;
  }) =>
    request<StagePollRequeueResult>("/api/tasks/stage-polls", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  retryStageFailures: (input: {
    stage: "verify" | "bind" | "repost" | "claim";
    limit: number;
    apply: boolean;
  }) =>
    request<StageRetryResult>("/api/tasks/stage-retries", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  bindStatusSync: (input: {
    name: string;
    limit: number;
    dispatch_limit: number;
    apply: boolean;
  }) =>
    request<BindStatusSyncResult>("/api/tasks/bind-status-sync", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  launchManualWorkbench: (input: {
    binding_ids: string[];
    repost_target: string;
    limit: number;
    timeout_seconds: number;
  }) =>
    request<ManualWorkbenchResult>("/api/bindings/manual-workbenches", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  launchOneManualWorkbench: (
    bindingId: string,
    input: {
      repost_target: string;
      timeout_seconds: number;
    }
  ) =>
    request<ManualWorkbenchResult>(`/api/bindings/${bindingId}/manual-workbench`, {
      method: "POST",
      body: JSON.stringify({ ...input, binding_ids: [], limit: 1 })
    }),
  pairedBind: (input: {
    accounts_content: string;
    private_keys_content: string;
    name: string;
    limit: number;
    dispatch_limit: number;
    include_unverified: boolean;
    apply: boolean;
  }) =>
    request<PairedBindResult>("/api/tasks/paired-bind", {
      method: "POST",
      body: JSON.stringify(input)
    }),
  syncBalances: (bindingIds?: string[]) =>
    request<{ task_ids: string[]; queued: number }>("/api/balances/sync", {
      method: "POST",
      body: JSON.stringify({ binding_ids: bindingIds?.length ? bindingIds : null })
    })
};
