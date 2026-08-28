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
  kind: "bind" | "repost" | "claim" | "verify_account";
  state: string;
  attempt: number;
  priority: number;
  social_account_id: string | null;
  wallet_id: string | null;
  binding_id: string | null;
  idempotency_key: string;
  lease_keys: string[];
  scheduled_at: string;
  started_at: string | null;
  finished_at: string | null;
  external_operation_ref: string | null;
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

export const api = {
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
  accounts: () => request<Page<Account>>("/api/accounts?limit=500"),
  wallets: () => request<Page<Wallet>>("/api/wallets?limit=500"),
  bindings: () => request<Page<Binding>>("/api/bindings?limit=500"),
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
  taskCommand: (id: string, command: "pause" | "cancel" | "retry" | "poll") =>
    request<Task>(`/api/tasks/${id}/${command}`, { method: "POST" })
};
