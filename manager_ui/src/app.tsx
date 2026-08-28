import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock3,
  Copy,
  Database,
  Download,
  KeyRound,
  Layers3,
  Link2,
  LoaderCircle,
  LockKeyhole,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  WalletCards,
  X
} from "lucide-react";
import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState
} from "react";

import {
  Account,
  AccountImportResult,
  api,
  ApiError,
  Task,
  TaskBatch,
  VaultStatus,
  WalletPreview
} from "./api/client";

type PageKey = "overview" | "accounts" | "wallets" | "bindings" | "tasks" | "vault";
type Notice = { tone: "success" | "error"; message: string } | null;
type NoticeTone = "success" | "error";

const pageMeta: Array<{ key: PageKey; label: string; icon: typeof Activity }> = [
  { key: "overview", label: "总览", icon: Activity },
  { key: "accounts", label: "账号", icon: KeyRound },
  { key: "wallets", label: "地址", icon: WalletCards },
  { key: "bindings", label: "绑定", icon: Link2 },
  { key: "tasks", label: "任务", icon: Layers3 },
  { key: "vault", label: "Vault", icon: LockKeyhole }
];

function useResource<T>(load: () => Promise<T>, dependency: number) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setValue(await load());
      setError(null);
    } catch (caught) {
      setError(toMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    void reload();
  }, [dependency, reload]);

  return { value, error, loading, reload };
}

function toMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求未完成";
}

function compact(value: string | null | undefined, head = 9, tail = 6) {
  if (!value) {
    return "—";
  }
  return value.length > head + tail + 1 ? `${value.slice(0, head)}…${value.slice(-tail)}` : value;
}

function dateTime(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "short",
        hour12: false
      }).format(new Date(value))
    : "—";
}

function stateTone(state: string) {
  if (["succeeded", "bound", "healthy", "active"].includes(state)) return "success";
  if (["failed", "invalid", "archived", "cancelled"].includes(state)) return "danger";
  if (["waiting_external_validation", "running", "leased", "pending", "queued"].includes(state)) {
    return "warning";
  }
  return "neutral";
}

function StateChip({ value }: { value: string }) {
  return <span className={`chip chip-${stateTone(value)}`}>{value.replaceAll("_", " ")}</span>;
}

function IconButton({
  label,
  children,
  onClick,
  disabled = false,
  tone = "neutral"
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: "neutral" | "primary" | "danger";
}) {
  return (
    <button
      className={`icon-button icon-button-${tone}`}
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

function Button({
  children,
  onClick,
  disabled = false,
  type = "button",
  tone = "secondary"
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  tone?: "primary" | "secondary" | "danger";
}) {
  return (
    <button className={`button button-${tone}`} type={type} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function Panel({
  title,
  action,
  children,
  className = ""
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <div className="panel-heading">
          {title ? <h2>{title}</h2> : <span />}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <Database size={24} strokeWidth={1.5} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function LoadingRows() {
  return (
    <div className="loading-rows">
      <span />
      <span />
      <span />
    </div>
  );
}

function Dialog({
  title,
  children,
  onClose,
  wide = false
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={`dialog ${wide ? "dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <h2>{title}</h2>
          <IconButton label="关闭" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>
        {children}
      </section>
    </div>
  );
}

function copyText(value: string, notify: (message: string) => void) {
  void navigator.clipboard.writeText(value).then(
    () => notify("已复制到剪贴板"),
    () => notify("复制失败，请手动记录")
  );
}

export function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  const vault = useResource(api.vaultStatus, refresh);

  const notify = useCallback((message: string, tone: NoticeTone = "success") => {
    setNotice({ message, tone });
    window.setTimeout(() => setNotice(null), 4200);
  }, []);

  const complete = useCallback(
    (message: string) => {
      setRefresh((value) => value + 1);
      notify(message);
    },
    [notify]
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={19} />
          </div>
          <div>
            <strong>Operator</strong>
            <span>local control plane</span>
          </div>
        </div>
        <nav className="navigation" aria-label="主导航">
          {pageMeta.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              className={page === key ? "nav-link is-active" : "nav-link"}
              onClick={() => setPage(key)}
            >
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className={`vault-indicator ${vault.value?.unlocked ? "is-unlocked" : ""}`}>
            <LockKeyhole size={16} />
            <span>{vault.value?.unlocked ? "Vault 已解锁" : "Vault 已锁定"}</span>
          </div>
          <span>单管理员 · 本地优先</span>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <span className="eyebrow">OPERATIONS</span>
            <h1>{pageMeta.find((item) => item.key === page)?.label}</h1>
          </div>
          <div className="topbar-actions">
            <span className={`connection ${vault.error ? "is-down" : ""}`}>
              <span />
              {vault.error ? "API 未连接" : "API 在线"}
            </span>
            <IconButton label="刷新当前数据" onClick={() => setRefresh((value) => value + 1)}>
              <RefreshCw size={18} />
            </IconButton>
          </div>
        </header>
        {notice && (
          <div className={`notice notice-${notice.tone}`}>
            {notice.tone === "success" ? <CircleCheck size={18} /> : <CircleAlert size={18} />}
            <span>{notice.message}</span>
            <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>
              <X size={16} />
            </button>
          </div>
        )}
        {page === "overview" && <Overview refresh={refresh} onNavigate={setPage} />}
        {page === "accounts" && (
          <AccountsPage
            refresh={refresh}
            vault={vault.value}
            onComplete={complete}
            onNotice={notify}
          />
        )}
        {page === "wallets" && (
          <WalletsPage
            refresh={refresh}
            vault={vault.value}
            onComplete={complete}
            onNotice={notify}
          />
        )}
        {page === "bindings" && (
          <BindingsPage
            refresh={refresh}
            vault={vault.value}
            onComplete={complete}
            onNotice={notify}
          />
        )}
        {page === "tasks" && <TasksPage refresh={refresh} onComplete={complete} onNotice={notify} />}
        {page === "vault" && (
          <VaultPage refresh={refresh} status={vault.value} onComplete={complete} onNotice={notify} />
        )}
      </main>
    </div>
  );
}

function Overview({ refresh, onNavigate }: { refresh: number; onNavigate: (page: PageKey) => void }) {
  const accounts = useResource(api.accounts, refresh);
  const wallets = useResource(api.wallets, refresh);
  const bindings = useResource(api.bindings, refresh);
  const tasks = useResource(api.tasks, refresh);
  const summary = useMemo(() => {
    const taskItems = tasks.value?.items ?? [];
    return {
      accounts: accounts.value?.total ?? 0,
      wallets: wallets.value?.total ?? 0,
      bound: bindings.value?.items.filter((item) => item.state === "bound").length ?? 0,
      activeTasks: taskItems.filter((item) =>
        ["queued", "leased", "running", "waiting_external_validation"].includes(item.state)
      ).length
    };
  }, [accounts.value, bindings.value, tasks.value, wallets.value]);

  return (
    <div className="page-stack">
      <div className="metric-grid">
        <Metric label="账号" value={summary.accounts} icon={<KeyRound size={19} />} onClick={() => onNavigate("accounts")} />
        <Metric label="地址" value={summary.wallets} icon={<WalletCards size={19} />} onClick={() => onNavigate("wallets")} />
        <Metric label="已绑定" value={summary.bound} icon={<Link2 size={19} />} onClick={() => onNavigate("bindings")} />
        <Metric label="运行中任务" value={summary.activeTasks} icon={<Activity size={19} />} onClick={() => onNavigate("tasks")} />
      </div>
      <div className="overview-grid">
        <Panel title="近期任务" action={<span className="subtle-label">{tasks.value?.total ?? 0} total</span>}>
          {tasks.loading ? (
            <LoadingRows />
          ) : tasks.value?.items.length ? (
            <div className="timeline">
              {tasks.value.items.slice(-6).reverse().map((task) => (
                <button key={task.id} type="button" className="timeline-row" onClick={() => onNavigate("tasks")}>
                  <span className={`timeline-dot tone-${stateTone(task.state)}`} />
                  <span>
                    <strong>{task.kind}</strong>
                    <small>{task.result_summary || "任务已入队"}</small>
                  </span>
                  <StateChip value={task.state} />
                    <time>{dateTime(task.scheduled_at)}</time>
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无任务" detail="从绑定列表创建第一项操作。" />
          )}
        </Panel>
        <Panel title="操作状态">
          <div className="status-list">
            <StatusLine label="账号会话健康" value={`${accounts.value?.items.filter((account) => account.health === "healthy").length ?? 0} healthy`} tone="success" />
            <StatusLine label="等待外部校验" value={`${tasks.value?.items.filter((task) => task.state === "waiting_external_validation").length ?? 0} tasks`} tone="warning" />
            <StatusLine label="失败待处理" value={`${tasks.value?.items.filter((task) => task.state === "failed").length ?? 0} tasks`} tone="danger" />
            <StatusLine label="资源独占" value="调度器已启用" tone="neutral" />
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  icon,
  onClick
}: {
  label: string;
  value: number;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button type="button" className="metric" onClick={onClick}>
      <span className="metric-icon">{icon}</span>
      <strong>{value}</strong>
      <span>{label}</span>
      <ArrowRight size={16} />
    </button>
  );
}

function StatusLine({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="status-line">
      <span className={`status-dot tone-${tone}`} />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AccountsPage({
  refresh,
  vault,
  onComplete,
  onNotice
}: {
  refresh: number;
  vault: VaultStatus | null;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const accounts = useResource(api.accounts, refresh);
  const bindings = useResource(api.bindings, refresh);
  const [importOpen, setImportOpen] = useState(false);
  const [bindingAccount, setBindingAccount] = useState<Account | null>(null);
  const occupiedAccountIds = useMemo(
    () =>
      new Set(
        (bindings.value?.items ?? [])
          .filter((binding) => binding.state !== "archived")
          .map((binding) => binding.social_account_id)
      ),
    [bindings.value]
  );

  return (
    <div className="page-stack">
      <PageToolbar
        description="导入的 X 账号仅以掩码身份和会话状态显示。"
        action={
          <Button tone="primary" onClick={() => setImportOpen(true)} disabled={!vault?.unlocked}>
            <Plus size={17} />
            导入账号
          </Button>
        }
      />
      <Panel>
        {accounts.loading ? (
          <LoadingRows />
        ) : accounts.error ? (
          <EmptyState title="账号列表暂不可用" detail={accounts.error} />
        ) : accounts.value?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>账号</th>
                  <th>邮箱</th>
                  <th>会话</th>
                  <th>状态</th>
                  <th>凭据</th>
                  <th className="actions-column">操作</th>
                </tr>
              </thead>
              <tbody>
                {accounts.value.items.map((account) => (
                  <tr key={account.id}>
                    <td><strong>@{account.handle}</strong></td>
                    <td>{account.email_masked || "—"}</td>
                    <td><StateChip value={account.health} /></td>
                    <td><StateChip value={account.state} /></td>
                    <td>{account.has_secret ? "已加密" : "缺失"}</td>
                    <td className="row-actions">
                      {(() => {
                        const occupied = occupiedAccountIds.has(account.id);
                        const bindingLabel = bindings.value?.items.find(
                          (binding) =>
                            binding.social_account_id === account.id && binding.state !== "archived"
                        )?.state;
                        return (
                      <Button
                        onClick={() => setBindingAccount(account)}
                        disabled={account.state !== "active" || !vault?.unlocked || occupied}
                      >
                        {bindingLabel === "bound" ? "已绑定" : bindingLabel === "pending" ? "绑定中" : "绑定"}
                      </Button>
                        );
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无账号" detail="导入七列 TSV 后，账号会以掩码身份出现在这里。" />
        )}
      </Panel>
      {!vault?.unlocked && <LockedHint label="解锁 Vault 后可以导入账号或创建绑定。" />}
      {importOpen && (
        <AccountImportDialog
          onClose={() => setImportOpen(false)}
          onComplete={(message) => {
            setImportOpen(false);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
      {bindingAccount && (
        <BindDialog
          account={bindingAccount}
          refresh={refresh}
          onClose={() => setBindingAccount(null)}
          onComplete={(message) => {
            setBindingAccount(null);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
    </div>
  );
}

function WalletsPage({
  refresh,
  vault,
  onComplete,
  onNotice
}: {
  refresh: number;
  vault: VaultStatus | null;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const wallets = useResource(api.wallets, refresh);
  const [importOpen, setImportOpen] = useState(false);
  return (
    <div className="page-stack">
      <PageToolbar
        description="显示公开地址、来源类型和派生路径；私钥与助记词不进入列表。"
        action={
          <Button tone="primary" onClick={() => setImportOpen(true)} disabled={!vault?.unlocked}>
            <Plus size={17} />
            导入地址
          </Button>
        }
      />
      <Panel>
        {wallets.loading ? (
          <LoadingRows />
        ) : wallets.error ? (
          <EmptyState title="地址列表暂不可用" detail={wallets.error} />
        ) : wallets.value?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>地址</th>
                  <th>来源</th>
                  <th>派生路径</th>
                  <th>状态</th>
                  <th>绑定</th>
                  <th>凭据</th>
                </tr>
              </thead>
              <tbody>
                {wallets.value.items.map((wallet) => (
                  <tr key={wallet.id}>
                    <td className="mono">{compact(wallet.address, 12, 8)}</td>
                    <td>{wallet.source_type || "—"}</td>
                    <td className="mono">{wallet.derivation_path || "直接导入"}</td>
                    <td><StateChip value={wallet.state} /></td>
                    <td>{wallet.is_bound ? "已占用" : "可绑定"}</td>
                    <td>{wallet.has_secret ? "已加密" : "缺失"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无地址" detail="导入私钥或助记词，派生的公开地址会出现在这里。" />
        )}
      </Panel>
      {!vault?.unlocked && <LockedHint label="解锁 Vault 后可以导入或派生地址。" />}
      {importOpen && (
        <WalletImportDialog
          onClose={() => setImportOpen(false)}
          onComplete={(message) => {
            setImportOpen(false);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
    </div>
  );
}

function BindingsPage({
  refresh,
  vault,
  onComplete,
  onNotice
}: {
  refresh: number;
  vault: VaultStatus | null;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const bindings = useResource(api.bindings, refresh);
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<{ kind: "repost" | "claim"; bindingIds: string[] } | null>(null);
  const visibleBound = useMemo(
    () => bindings.value?.items.filter((binding) => binding.state === "bound") ?? [],
    [bindings.value]
  );

  useEffect(() => {
    setSelected((current) => current.filter((id) => visibleBound.some((binding) => binding.id === id)));
  }, [bindings.value, visibleBound]);

  const toggle = (id: string) => {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  return (
    <div className="page-stack">
      <PageToolbar
        description="绑定在外部确认后不可更换。批次按独立资源租约调度，默认并发窗口为 10。"
        action={
          <div className="toolbar-actions">
            <Button
              onClick={() => setOperation({ kind: "repost", bindingIds: selected })}
              disabled={!vault?.unlocked || selected.length === 0}
            >
              <Send size={16} />
              批量转发 ({selected.length})
            </Button>
            <Button
              tone="primary"
              onClick={() => setOperation({ kind: "claim", bindingIds: selected })}
              disabled={!vault?.unlocked || selected.length === 0}
            >
              <Play size={16} />
              批量领取 ({selected.length})
            </Button>
          </div>
        }
      />
      <Panel>
        {bindings.loading ? (
          <LoadingRows />
        ) : bindings.error ? (
          <EmptyState title="绑定列表暂不可用" detail={bindings.error} />
        ) : bindings.value?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="checkbox-column">
                    <input
                      aria-label="选择所有已绑定记录"
                      type="checkbox"
                      checked={visibleBound.length > 0 && selected.length === visibleBound.length}
                      onChange={() =>
                        setSelected(selected.length === visibleBound.length ? [] : visibleBound.map((item) => item.id))
                      }
                    />
                  </th>
                  <th>账号</th>
                  <th>地址</th>
                  <th>绑定状态</th>
                  <th>确认时间</th>
                  <th className="actions-column">操作</th>
                </tr>
              </thead>
              <tbody>
                {bindings.value.items.map((binding) => {
                  const eligible = binding.state === "bound";
                  return (
                    <tr key={binding.id}>
                      <td className="checkbox-column">
                        <input
                          aria-label={`选择 ${binding.account_handle}`}
                          type="checkbox"
                          checked={selected.includes(binding.id)}
                          disabled={!eligible}
                          onChange={() => toggle(binding.id)}
                        />
                      </td>
                      <td><strong>@{binding.account_handle}</strong></td>
                      <td className="mono">{compact(binding.wallet_address, 12, 8)}</td>
                      <td><StateChip value={binding.state} /></td>
                      <td>{dateTime(binding.bound_at)}</td>
                      <td className="row-actions">
                        <Button
                          disabled={!eligible || !vault?.unlocked}
                          onClick={() => setOperation({ kind: "repost", bindingIds: [binding.id] })}
                        >
                          转发
                        </Button>
                        <Button
                          tone="primary"
                          disabled={!eligible || !vault?.unlocked}
                          onClick={() => setOperation({ kind: "claim", bindingIds: [binding.id] })}
                        >
                          领取
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无绑定" detail="从账号页面选择一个未绑定账号，随后选择地址创建绑定任务。" />
        )}
      </Panel>
      {!vault?.unlocked && <LockedHint label="解锁 Vault 后才可创建转发或领取任务。" />}
      {operation && (
        <OperationDialog
          kind={operation.kind}
          bindingIds={operation.bindingIds}
          onClose={() => setOperation(null)}
          onComplete={(message) => {
            setOperation(null);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
    </div>
  );
}

function TasksPage({
  refresh,
  onComplete,
  onNotice
}: {
  refresh: number;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const tasks = useResource(api.tasks, refresh);
  const batches = useResource(api.taskBatches, refresh);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const command = async (task: Task, action: "pause" | "cancel" | "retry" | "poll") => {
    try {
      await api.taskCommand(task.id, action);
      onComplete(`任务已${action === "poll" ? "加入轮询" : action === "retry" ? "重试" : action === "pause" ? "暂停" : "取消"}`);
      setSelectedTask(null);
    } catch (error) {
      onNotice(toMessage(error), "error");
    }
  };

  return (
    <div className="page-stack">
      <PageToolbar
        description="每个任务都保留独立事件链、租约与重试状态。"
        action={<span className="subtle-label">{batches.value?.total ?? 0} 个批次</span>}
      />
      <div className="tasks-layout">
        <Panel className="task-table-panel">
          {tasks.loading ? (
            <LoadingRows />
          ) : tasks.error ? (
            <EmptyState title="任务列表暂不可用" detail={tasks.error} />
          ) : tasks.value?.items.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>状态</th>
                    <th>尝试</th>
                    <th>资源</th>
                    <th>计划时间</th>
                    <th className="actions-column">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.value.items.map((task) => (
                    <tr key={task.id} className={selectedTask?.id === task.id ? "is-selected" : ""}>
                      <td><strong>{task.kind}</strong></td>
                      <td><StateChip value={task.state} /></td>
                      <td>{task.attempt}</td>
                      <td className="mono">{compact(task.binding_id || task.social_account_id, 7, 5)}</td>
                      <td>{dateTime(task.scheduled_at)}</td>
                      <td className="row-actions">
                        <Button onClick={() => setSelectedTask(task)}>查看</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="暂无任务" detail="绑定、转发或领取操作会生成可恢复的任务记录。" />
          )}
        </Panel>
        <TaskDetail task={selectedTask} onCommand={command} onClose={() => setSelectedTask(null)} />
      </div>
      {batches.value?.items.length ? (
        <Panel title="最近批次">
          <div className="batch-list">
            {batches.value.items.slice(0, 5).map((batch) => (
              <BatchRow key={batch.id} batch={batch} />
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function TaskDetail({
  task,
  onCommand,
  onClose
}: {
  task: Task | null;
  onCommand: (task: Task, action: "pause" | "cancel" | "retry" | "poll") => void;
  onClose: () => void;
}) {
  if (!task) {
    return (
      <Panel className="task-detail">
        <EmptyState title="选择一个任务" detail="在这里查看状态、事件和可执行命令。" />
      </Panel>
    );
  }
  const canPause = task.state === "queued";
  const canCancel = ["queued", "leased", "paused"].includes(task.state);
  const canRetry = task.state === "failed";
  const canPoll = task.state === "waiting_external_validation";
  return (
    <Panel
      title="任务详情"
      className="task-detail"
      action={
        <IconButton label="关闭详情" onClick={onClose}>
          <X size={17} />
        </IconButton>
      }
    >
      <div className="detail-summary">
        <div>
          <span>类型</span>
          <strong>{task.kind}</strong>
        </div>
        <div>
          <span>状态</span>
          <StateChip value={task.state} />
        </div>
        <div>
          <span>外部引用</span>
          <strong className="mono">{compact(task.external_operation_ref)}</strong>
        </div>
      </div>
      <div className="command-row">
        <Button disabled={!canPoll} onClick={() => onCommand(task, "poll")}>
          <RefreshCw size={15} />
          轮询
        </Button>
        <Button disabled={!canRetry} onClick={() => onCommand(task, "retry")}>
          <RotateCcw size={15} />
          重试
        </Button>
        <Button disabled={!canPause} onClick={() => onCommand(task, "pause")}>
          <Clock3 size={15} />
          暂停
        </Button>
        <Button tone="danger" disabled={!canCancel} onClick={() => onCommand(task, "cancel")}>
          <X size={15} />
          取消
        </Button>
      </div>
      <div className="event-stream">
        {task.events.length ? (
          task.events.map((event) => (
            <div className="event-row" key={event.id}>
              <span className={`timeline-dot tone-${stateTone(event.to_state || "neutral")}`} />
              <div>
                <strong>{event.summary || event.event_type}</strong>
                <small>{event.from_state ? `${event.from_state} → ${event.to_state}` : event.to_state || event.event_type}</small>
              </div>
              <time>{dateTime(event.created_at)}</time>
            </div>
          ))
        ) : (
          <EmptyState title="暂无事件" detail="任务开始后会显示追加的状态记录。" />
        )}
      </div>
    </Panel>
  );
}

function BatchRow({ batch }: { batch: TaskBatch }) {
  const states = batch.jobs.reduce<Record<string, number>>((counts, task) => {
    counts[task.state] = (counts[task.state] ?? 0) + 1;
    return counts;
  }, {});
  return (
    <div className="batch-row">
      <div className="batch-icon"><Layers3 size={17} /></div>
      <div>
        <strong>{batch.name}</strong>
        <span>{batch.kind} · dispatch {batch.dispatch_limit}</span>
      </div>
      <div className="batch-states">
        {Object.entries(states).map(([state, count]) => (
          <span key={state}><StateChip value={state} /> {count}</span>
        ))}
      </div>
      <time>{dateTime(batch.created_at)}</time>
    </div>
  );
}

function VaultPage({
  refresh,
  status,
  onComplete,
  onNotice
}: {
  refresh: number;
  status: VaultStatus | null;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [mode, setMode] = useState<"initialize" | "unlock" | null>(null);
  const [recoveryKey, setRecoveryKey] = useState<string | null>(null);
  const vault = useResource(api.vaultStatus, refresh);
  const active = status || vault.value;

  const lock = async () => {
    try {
      await api.lockVault();
      onComplete("Vault 已锁定");
    } catch (error) {
      onNotice(toMessage(error), "error");
    }
  };
  return (
    <div className="page-stack vault-page">
      <Panel title="加密 Vault">
        <div className="vault-hero">
          <div className={`vault-icon ${active?.unlocked ? "is-unlocked" : ""}`}>
            <LockKeyhole size={28} />
          </div>
          <div>
            <h2>{active?.unlocked ? "Vault 当前已解锁" : active?.initialized ? "Vault 当前已锁定" : "尚未初始化 Vault"}</h2>
            <p>敏感材料使用 Argon2id 与 AES-256-GCM 加密。公开列表不会读取或显示任何原始凭据。</p>
          </div>
          <div className="vault-actions">
            {!active?.initialized && (
              <Button tone="primary" onClick={() => setMode("initialize")}>
                <KeyRound size={16} />
                初始化
              </Button>
            )}
            {active?.initialized && !active?.unlocked && (
              <Button tone="primary" onClick={() => setMode("unlock")}>
                <KeyRound size={16} />
                解锁
              </Button>
            )}
            {active?.unlocked && (
              <Button tone="danger" onClick={lock}>
                <LockKeyhole size={16} />
                立即锁定
              </Button>
            )}
          </div>
        </div>
      </Panel>
      <div className="overview-grid">
        <Panel title="恢复密钥">
          <div className="vault-note">
            <KeyRound size={18} />
            <div>
              <strong>初始化时仅显示一次</strong>
              <p>恢复密钥应离线保存。它与加密数据库一起支持迁移后的解锁。</p>
            </div>
          </div>
        </Panel>
        <Panel title="备份">
          <div className="vault-note">
            <Download size={18} />
            <div>
              <strong>下一切片接入</strong>
              <p>备份与恢复验证将在 Vault 导出路径完成后开放。</p>
            </div>
          </div>
        </Panel>
      </div>
      {mode && (
        <VaultDialog
          mode={mode}
          onClose={() => setMode(null)}
          onComplete={(result) => {
            setMode(null);
            if (result.recoveryKey) {
              setRecoveryKey(result.recoveryKey);
            }
            onComplete(result.message);
          }}
          onNotice={onNotice}
        />
      )}
      {recoveryKey && (
        <Dialog title="记录恢复密钥" onClose={() => setRecoveryKey(null)}>
          <div className="recovery-dialog">
            <CircleAlert size={21} />
            <p>此值只会在初始化响应中显示一次。请在关闭前离线记录。</p>
            <code>{recoveryKey}</code>
            <div className="dialog-actions">
              <Button onClick={() => copyText(recoveryKey, onNotice)}>
                <Copy size={16} />
                复制
              </Button>
              <Button tone="primary" onClick={() => setRecoveryKey(null)}>
                <Check size={16} />
                已记录
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function PageToolbar({ description, action }: { description: string; action?: ReactNode }) {
  return (
    <div className="page-toolbar">
      <p>{description}</p>
      {action}
    </div>
  );
}

function LockedHint({ label }: { label: string }) {
  return (
    <div className="locked-hint">
      <LockKeyhole size={17} />
      <span>{label}</span>
    </div>
  );
}

function AccountImportDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [sourceName, setSourceName] = useState("accounts.tsv");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState<AccountImportResult | null>(null);
  const [busy, setBusy] = useState(false);

  const previewRows = async () => {
    setBusy(true);
    try {
      setPreview(await api.previewAccountImport(content, sourceName));
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  const commit = async () => {
    setBusy(true);
    try {
      const result = await api.commitAccountImport(content, sourceName);
      onComplete(`已导入 ${result.summary.committed_rows} 个账号`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="导入账号" onClose={onClose} wide>
      <div className="form-grid">
        <label>
          <span>来源名称</span>
          <input value={sourceName} onChange={(event) => setSourceName(event.target.value)} />
        </label>
        <label className="span-all">
          <span>七列 TSV</span>
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder={"账号\t密码\t2FA\t邮箱\t邮箱密码\ttoken\tCookie"}
            rows={9}
            spellCheck={false}
          />
        </label>
      </div>
      {preview && <ImportPreview result={preview} />}
      <div className="dialog-actions">
        <Button onClick={onClose}>取消</Button>
        <Button onClick={previewRows} disabled={busy || !content.trim()}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
          预览
        </Button>
        <Button
          tone="primary"
          onClick={commit}
          disabled={busy || !preview || preview.summary.valid_rows === 0}
        >
          <Download size={16} />
          写入 Vault
        </Button>
      </div>
    </Dialog>
  );
}

function ImportPreview({ result }: { result: AccountImportResult }) {
  return (
    <div className="import-preview">
      <div className="preview-summary">
        <span>{result.summary.total_rows} 行</span>
        <span className="success-text">{result.summary.valid_rows} 有效</span>
        <span className="danger-text">{result.summary.malformed_rows + result.summary.conflicting_rows} 异常</span>
        <span>{result.summary.duplicate_rows + result.summary.existing_rows} 重复</span>
      </div>
      <div className="preview-rows">
        {result.rows.slice(0, 10).map((row) => (
          <div key={row.line_number}>
            <span>{row.line_number}</span>
            <strong>{row.handle_masked || "—"}</strong>
            <span>{row.email_masked || "—"}</span>
            <StateChip value={row.status} />
            <small>{row.diagnostic_detail || "可写入"}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function WalletImportDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [sourceType, setSourceType] = useState<"private_key" | "mnemonic">("private_key");
  const [secret, setSecret] = useState("");
  const [label, setLabel] = useState("");
  const [startIndex, setStartIndex] = useState(0);
  const [count, setCount] = useState(1);
  const [preview, setPreview] = useState<WalletPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const input = { source_type: sourceType, secret, label, start_index: startIndex, count };

  const previewWallet = async () => {
    setBusy(true);
    try {
      setPreview(await api.previewWallet(input));
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  const commit = async () => {
    setBusy(true);
    try {
      const result = await api.commitWallet(input);
      onComplete(`已写入 ${result.summary.committed} 个地址`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="导入地址" onClose={onClose} wide>
      <div className="segmented-control" role="group" aria-label="导入类型">
        <button
          type="button"
          className={sourceType === "private_key" ? "is-active" : ""}
          onClick={() => {
            setSourceType("private_key");
            setCount(1);
            setPreview(null);
          }}
        >
          私钥
        </button>
        <button
          type="button"
          className={sourceType === "mnemonic" ? "is-active" : ""}
          onClick={() => {
            setSourceType("mnemonic");
            setPreview(null);
          }}
        >
          助记词
        </button>
      </div>
      <div className="form-grid">
        <label className="span-all">
          <span>{sourceType === "private_key" ? "私钥" : "助记词"}</span>
          <textarea
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            rows={sourceType === "private_key" ? 3 : 4}
            spellCheck={false}
          />
        </label>
        <label>
          <span>标签</span>
          <input value={label} onChange={(event) => setLabel(event.target.value)} />
        </label>
        {sourceType === "mnemonic" && (
          <>
            <label>
              <span>起始索引</span>
              <input type="number" min={0} value={startIndex} onChange={(event) => setStartIndex(Number(event.target.value))} />
            </label>
            <label>
              <span>派生数量</span>
              <input type="number" min={1} max={100} value={count} onChange={(event) => setCount(Number(event.target.value))} />
            </label>
          </>
        )}
      </div>
      {preview && (
        <div className="import-preview">
          <div className="preview-summary">
            <span>{preview.summary.total} 地址</span>
            <span className="success-text">{preview.summary.valid} 有效</span>
            <span>{preview.summary.duplicate_existing + preview.summary.duplicate_in_file} 重复</span>
          </div>
          <div className="preview-rows">
            {preview.wallets.slice(0, 10).map((wallet) => (
              <div key={`${wallet.index}-${wallet.address}`}>
                <span>{wallet.index ?? "—"}</span>
                <strong className="mono">{compact(wallet.address, 12, 8)}</strong>
                <span className="mono">{wallet.derivation_path || "直接导入"}</span>
                <StateChip value={wallet.status} />
                <small>{wallet.diagnostic_detail || "可写入"}</small>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="dialog-actions">
        <Button onClick={onClose}>取消</Button>
        <Button onClick={previewWallet} disabled={busy || !secret.trim()}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
          预览
        </Button>
        <Button tone="primary" onClick={commit} disabled={busy || !preview || preview.summary.valid === 0}>
          <Download size={16} />
          写入 Vault
        </Button>
      </div>
    </Dialog>
  );
}

function BindDialog({
  account,
  refresh,
  onClose,
  onComplete,
  onNotice
}: {
  account: Account;
  refresh: number;
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const wallets = useResource(api.wallets, refresh);
  const [walletId, setWalletId] = useState("");
  const [target, setTarget] = useState("kredo:bind");
  const [busy, setBusy] = useState(false);
  const eligibleWallets = wallets.value?.items.filter((wallet) => wallet.state === "active" && !wallet.is_bound) ?? [];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!walletId) return;
    setBusy(true);
    try {
      const binding = await api.createBinding(account.id, walletId);
      await api.createTask({
        kind: "bind",
        binding_id: binding.id,
        external_target: target
      });
      onComplete(`已创建 @${account.handle} 的绑定任务`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="创建绑定任务" onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        <div className="binding-pair">
          <span>@{account.handle}</span>
          <ArrowRight size={17} />
          <select value={walletId} onChange={(event) => setWalletId(event.target.value)} required>
            <option value="">选择一个未绑定地址</option>
            {eligibleWallets.map((wallet) => (
              <option key={wallet.id} value={wallet.id}>{compact(wallet.address, 12, 8)}</option>
            ))}
          </select>
        </div>
        <label>
          <span>任务目标</span>
          <input value={target} onChange={(event) => setTarget(event.target.value)} required />
        </label>
        <p className="form-hint">创建后会锁定这对账号与地址，直到外部状态确认或任务终止。</p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button tone="primary" type="submit" disabled={busy || !walletId}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Link2 size={16} />}
            创建任务
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function OperationDialog({
  kind,
  bindingIds,
  onClose,
  onComplete,
  onNotice
}: {
  kind: "repost" | "claim";
  bindingIds: string[];
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [target, setTarget] = useState("");
  const [name, setName] = useState(`${kind === "repost" ? "转发" : "领取"} ${new Date().toLocaleDateString("zh-CN")}`);
  const [busy, setBusy] = useState(false);
  const isBatch = bindingIds.length > 1;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (isBatch) {
        await api.createBatch({
          name,
          kind,
          dispatch_limit: 10,
          items: bindingIds.map((bindingId) => ({
            binding_id: bindingId,
            external_target: target
          }))
        });
        onComplete(`已创建 ${bindingIds.length} 项${kind === "repost" ? "转发" : "领取"}批次`);
      } else {
        await api.createTask({ kind, binding_id: bindingIds[0], external_target: target });
        onComplete(`已创建${kind === "repost" ? "转发" : "领取"}任务`);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={isBatch ? `批量${kind === "repost" ? "转发" : "领取"}` : kind === "repost" ? "创建转发任务" : "创建领取任务"} onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        {isBatch && (
          <label>
            <span>批次名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
        )}
        <label>
          <span>{kind === "repost" ? "X 推文链接或 ID" : "领取任务目标"}</span>
          <input
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder={kind === "repost" ? "https://x.com/…/status/…" : "任务标识或链接"}
            required
          />
        </label>
        {isBatch && <p className="form-hint">已选 {bindingIds.length} 个绑定；调度器每轮最多派发 10 个独立任务。</p>}
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button tone="primary" type="submit" disabled={busy || !target.trim()}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
            入队
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function VaultDialog({
  mode,
  onClose,
  onComplete,
  onNotice
}: {
  mode: "initialize" | "unlock";
  onClose: () => void;
  onComplete: (result: { message: string; recoveryKey?: string }) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const initialize = mode === "initialize";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (initialize && password !== confirm) {
      onNotice("两次输入的密码不一致", "error");
      return;
    }
    setBusy(true);
    try {
      if (initialize) {
        const result = await api.initializeVault(password);
        onComplete({ message: "Vault 已初始化并解锁", recoveryKey: result.recovery_key });
      } else {
        await api.unlockVault(password);
        onComplete({ message: "Vault 已解锁" });
      }
    } catch (error) {
      onNotice(error instanceof ApiError && error.status === 401 ? "解锁失败" : toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={initialize ? "初始化 Vault" : "解锁 Vault"} onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        <label>
          <span>管理密码</span>
          <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        </label>
        {initialize && (
          <label>
            <span>确认密码</span>
            <input type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} required />
          </label>
        )}
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button tone="primary" type="submit" disabled={busy || password.length === 0}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <KeyRound size={16} />}
            {initialize ? "初始化" : "解锁"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
