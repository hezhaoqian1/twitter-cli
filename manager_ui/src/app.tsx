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
  Search,
  Send,
  ShieldCheck,
  Upload,
  WalletCards,
  X
} from "lucide-react";
import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  Account,
  AccountImportResult,
  AcceptanceAudit,
  api,
  ApiError,
  Balance,
  BindStatusSyncResult,
  Binding,
  NextStageRecommendation,
  PairedBindResult,
  RuntimeMetrics,
  OperationsSummary,
  StagePollRequeueResult,
  StageRetryResult,
  Task,
  TaskBatch,
  VaultBackupSummary,
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

const RESOURCE_CACHE_TTL_MS = 30_000;
const DEFAULT_REPOST_TARGET = "https://x.com/Kredofun/status/2092911885209444742";
const WORKBENCH_LIMIT = 10;

type ResourceCacheEntry<T> = {
  value: T | null;
  error: string | null;
  promise: Promise<T> | null;
  updatedAt: number;
  dependency: number | null;
};

const resourceCache = new Map<string, ResourceCacheEntry<unknown>>();

function resourceEntry<T>(key: string): ResourceCacheEntry<T> {
  const cached = resourceCache.get(key) as ResourceCacheEntry<T> | undefined;
  if (cached) return cached;
  const created: ResourceCacheEntry<T> = {
    value: null,
    error: null,
    promise: null,
    updatedAt: 0,
    dependency: null
  };
  resourceCache.set(key, created as ResourceCacheEntry<unknown>);
  return created;
}

function useResource<T>(
  key: string,
  load: () => Promise<T>,
  dependency: number,
  options: { ttlMs?: number; keepPrevious?: boolean } = {}
) {
  const entry = resourceEntry<T>(key);
  const ttlMs = options.ttlMs ?? RESOURCE_CACHE_TTL_MS;
  const keepPrevious = options.keepPrevious ?? true;
  const [value, setValue] = useState<T | null>(() => entry.value);
  const [error, setError] = useState<string | null>(() => entry.error);
  const [loading, setLoading] = useState(() => entry.value === null);
  const mounted = useRef(true);

  useEffect(() => {
    return () => {
      mounted.current = false;
    };
  }, []);

  const syncFromEntry = useCallback((next: ResourceCacheEntry<T>) => {
    if (!mounted.current) return;
    setValue(next.value);
    setError(next.error);
    setLoading(false);
  }, []);

  const reload = useCallback(
    async (force = false) => {
      const current = resourceEntry<T>(key);
      const now = Date.now();
      const dependencyChanged = current.dependency !== dependency;
      const cacheFresh = current.value !== null && now - current.updatedAt < ttlMs;

      // 同一个刷新版本内切换页面时直接复用缓存；慢接口在后台更新时不清空旧数据。
      if (!force && !dependencyChanged && cacheFresh) {
        syncFromEntry(current);
        return current.value;
      }

      if (current.promise) {
        setLoading(!keepPrevious || current.value === null);
        try {
          const result = await current.promise;
          syncFromEntry(current);
          return result;
        } catch {
          syncFromEntry(current);
          return current.value;
        }
      }

      setLoading(!keepPrevious || current.value === null);
      current.dependency = dependency;
      current.promise = load()
        .then((result) => {
          current.value = result;
          current.error = null;
          current.updatedAt = Date.now();
          return result;
        })
        .catch((caught) => {
          current.error = toMessage(caught);
          current.updatedAt = Date.now();
          throw caught;
        })
        .finally(() => {
          current.promise = null;
        });

      try {
        const result = await current.promise;
        syncFromEntry(current);
        return result;
      } catch {
        syncFromEntry(current);
        return current.value;
      }
    },
    [dependency, keepPrevious, key, load, syncFromEntry, ttlMs]
  );

  useEffect(() => {
    mounted.current = true;
    void reload();
  }, [reload]);

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

function amount(value: number | string | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(Number(value));
}

function stateTone(state: string) {
  if (["succeeded", "bound", "healthy", "active", "valid"].includes(state)) return "success";
  if (["failed", "invalid", "archived", "cancelled"].includes(state)) return "danger";
  if (["waiting_external_validation", "running", "leased", "pending", "queued"].includes(state)) {
    return "warning";
  }
  return "neutral";
}

const stateLabels: Record<string, string> = {
  active: "可用",
  archived: "已归档",
  bound: "已绑定",
  cancelled: "已取消",
  failed: "失败",
  healthy: "健康",
  invalid: "无效",
  leased: "已租约",
  paused: "已暂停",
  pending: "待确认",
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  valid: "有效",
  waiting_external_validation: "等待外部校验"
};

const kindLabels: Record<string, string> = {
  balance_sync: "余额同步",
  bind: "绑定地址",
  claim: "领取奖励",
  repost: "转发推文",
  verify_account: "校验会话"
};

function stateLabel(value: string) {
  return stateLabels[value] ?? value.replaceAll("_", " ");
}

function kindLabel(value: string) {
  return kindLabels[value] ?? value.replaceAll("_", " ");
}

function workflowLabel(value: string) {
  if (value.startsWith("stage:")) {
    const stage = value.slice("stage:".length);
    return {
      verify: "会话校验批次",
      bind: "绑定批次",
      repost: "转发批次",
      claim: "领取批次"
    }[stage] ?? `${stage} 批次`;
  }
  if (value === "account_wallet") {
    return "账号-地址工作流";
  }
  return kindLabel(value);
}

function bindingStage(binding: Binding) {
  return binding.stage ?? {
    repost_state: null,
    claim_state: null,
    can_repost: binding.state === "bound",
    can_claim: false,
    repost_waiting: false,
    claim_waiting: false
  };
}

function StateChip({ value }: { value: string }) {
  return <span className={`chip chip-${stateTone(value)}`}>{stateLabel(value)}</span>;
}

function healthCheckLabel(value: string) {
  return {
    postgres: "PostgreSQL",
    redis: "Redis"
  }[value] ?? value;
}

function HealthChecks({ checks }: { checks: Record<string, "ok" | "down"> }) {
  return (
    <div className="health-checks" aria-label="依赖健康状态">
      {Object.entries(checks).map(([name, state]) => (
        <span className={`health-check health-${state}`} key={name} title={`${healthCheckLabel(name)}：${state === "ok" ? "正常" : "不可用"}`}>
          <span />
          {healthCheckLabel(name)}
        </span>
      ))}
    </div>
  );
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
  tone = "secondary",
  title
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  tone?: "primary" | "secondary" | "danger";
  title?: string;
}) {
  return (
    <button className={`button button-${tone}`} type={type} onClick={onClick} disabled={disabled} title={title}>
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
  const dialogRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={`dialog ${wide ? "dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={dialogRef}
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
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const noticeTimer = useRef<number | null>(null);
  const vault = useResource("vault-status", api.vaultStatus, refresh);
  const health = useResource("health-ready", api.healthReady, refresh);

  const notify = useCallback((message: string, tone: NoticeTone = "success") => {
    if (noticeTimer.current) {
      window.clearTimeout(noticeTimer.current);
    }
    setNotice({ message, tone });
    noticeTimer.current = window.setTimeout(() => {
      setNotice(null);
      noticeTimer.current = null;
    }, 4200);
  }, []);

  useEffect(() => () => {
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
  }, []);

  useEffect(() => {
    if (refresh === 0) return;
    setRefreshing(true);
    const timer = window.setTimeout(() => {
      setRefreshing(false);
      setLastRefreshAt(Date.now());
    }, 850);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const refreshAll = () => {
    setRefreshing(true);
    setRefresh((value) => value + 1);
  };

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
            <span className={`connection ${health.error ? "is-down" : ""}`}>
              <span />
              {health.error ? "API 未连接" : "API 在线"}
            </span>
            {health.value && <HealthChecks checks={health.value.checks} />}
            <span className="refresh-state">
              {refreshing ? "刷新中…" : lastRefreshAt ? `更新于 ${dateTime(new Date(lastRefreshAt).toISOString())}` : "等待刷新"}
            </span>
            <IconButton label="刷新当前数据" onClick={refreshAll} disabled={refreshing} tone={refreshing ? "primary" : "neutral"}>
              <RefreshCw size={18} className={refreshing ? "spin" : ""} />
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
        {page === "overview" && (
          <Overview
            refresh={refresh}
            onNavigate={setPage}
            onComplete={complete}
            onNotice={notify}
          />
        )}
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

function Overview({
  refresh,
  onNavigate,
  onComplete,
  onNotice
}: {
  refresh: number;
  onNavigate: (page: PageKey) => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const accounts = useResource("accounts", api.accounts, refresh);
  const wallets = useResource("wallets", api.wallets, refresh);
  const bindings = useResource("bindings", api.bindings, refresh);
  const balances = useResource("balances", api.balances, refresh);
  const tasks = useResource("tasks", api.tasks, refresh);
  const runtime = useResource("runtime-metrics", api.runtimeMetrics, refresh);
  const operations = useResource("operations-summary", api.operationsSummary, refresh);
  const nextStage = useResource("next-stage", api.nextStage, refresh);
  const acceptance = useResource("acceptance-audit", api.acceptanceAudit, refresh);
  const [acceptanceBusyKey, setAcceptanceBusyKey] = useState<string | null>(null);
  const summary = useMemo(() => {
    const taskItems = tasks.value?.items ?? [];
    return {
      accounts: accounts.value?.total ?? 0,
      wallets: wallets.value?.total ?? 0,
      bound: bindings.value?.items.filter((item) => item.state === "bound").length ?? 0,
      activeTasks: taskItems.filter((item) =>
        ["queued", "leased", "running", "waiting_external_validation"].includes(item.state)
      ).length,
      points: sumBalance(balances.value?.items ?? [], "points"),
      hsk: sumBalance(balances.value?.items ?? [], "total_hsk")
    };
  }, [accounts.value, balances.value, bindings.value, tasks.value, wallets.value]);

  const runAcceptanceAction = async (action: AcceptanceAudit["actions"][number]) => {
    const busyKey = `${action.action}-${action.stage}`;
    setAcceptanceBusyKey(busyKey);
    try {
      if (action.action === "poll" && isPollableStage(action.stage)) {
        const result = await api.requeueStagePolls({
          stage: action.stage,
          limit: action.count,
          apply: true
        });
        onComplete(`已重新入队 ${result.requeued} 个${kindLabel(action.stage)}状态轮询`);
        return;
      }
      if (action.action === "sync_bind_status") {
        const result = await api.bindStatusSync({
          name: `bind status sync ${new Date().toLocaleString("zh-CN")}`,
          limit: action.count,
          dispatch_limit: Math.min(action.count, 10),
          apply: true
        });
        onComplete(`已创建 ${result.created_jobs} 个绑定状态同步任务`);
        return;
      }
      if (action.action === "retry") {
        const result = await api.retryStageFailures({
          stage: action.stage,
          limit: action.count,
          apply: true
        });
        onComplete(`已重试 ${result.retried} 个${kindLabel(action.stage)}失败任务`);
        return;
      }
      onNavigate(recommendationPage({ action: action.action, stage: action.stage, command: "", reason: "" }));
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setAcceptanceBusyKey(null);
    }
  };

  return (
    <div className="page-stack">
      <div className="hero-grid">
        <Panel className="hero-panel">
          <div className="hero-panel-copy">
            <span className="eyebrow">OPERATIONS CENTER</span>
            <h2>分阶段批量控制台</h2>
            <p>账号校验、地址绑定、推文转发和领取奖励分离成独立批次。你可以单点跑，也可以批量跑，并随时看见哪一步在等待外部回写。</p>
          </div>
          <div className="hero-panel-actions">
            <Button tone="primary" onClick={() => onNavigate("accounts")}>
              <KeyRound size={16} />
              去校验
            </Button>
            <Button onClick={() => onNavigate("bindings")}>
              <Link2 size={16} />
              去绑定
            </Button>
            <Button onClick={() => onNavigate("tasks")}>
              <Layers3 size={16} />
              看任务流
            </Button>
          </div>
        </Panel>
        <div className="metric-grid">
          <Metric label="账号" value={summary.accounts} icon={<KeyRound size={19} />} onClick={() => onNavigate("accounts")} />
          <Metric label="地址" value={summary.wallets} icon={<WalletCards size={19} />} onClick={() => onNavigate("wallets")} />
          <Metric label="已绑定" value={summary.bound} icon={<Link2 size={19} />} onClick={() => onNavigate("bindings")} />
          <Metric label="未完成任务" value={summary.activeTasks} icon={<Activity size={19} />} onClick={() => onNavigate("tasks")} />
        </div>
      </div>
      <Panel
        title="阶段控制台"
        action={<span className="subtle-label">{operations.value ? dateTime(operations.value.generated_at) : "等待摘要"}</span>}
      >
        {operations.loading ? (
          <LoadingRows />
        ) : operations.error ? (
          <EmptyState title="阶段摘要暂不可用" detail={operations.error} />
        ) : operations.value ? (
          <>
            <div className="stage-grid">
              {operations.value.stages.map((stage) => (
                <StageCard key={stage.key} stage={stage} onNavigate={onNavigate} />
              ))}
            </div>
            <div className="resource-band">
              <ResourceCard label="账号池" primary={`${operations.value.resources.accounts_available_for_binding}`} detail={`${operations.value.resources.accounts_active} active · ${operations.value.resources.accounts_healthy} healthy`} />
              <ResourceCard label="地址池" primary={`${operations.value.resources.wallets_available_for_binding}`} detail={`${operations.value.resources.wallets_active} active · ${operations.value.resources.wallets_total} total`} />
              <ResourceCard label="绑定池" primary={`${operations.value.resources.bindings_bound}`} detail={`${operations.value.resources.bindings_pending} pending · ${operations.value.resources.bindings_total} total`} />
            </div>
          </>
        ) : null}
      </Panel>
      <NextStagePanel
        recommendation={nextStage.value}
        audit={acceptance.value}
        loading={nextStage.loading || acceptance.loading}
        error={nextStage.error || acceptance.error}
        busyKey={acceptanceBusyKey}
        onNavigate={onNavigate}
        onRunAction={(action) => void runAcceptanceAction(action)}
      />
      <Panel
        title="资产概览"
        action={
          <button type="button" className="text-link" onClick={() => onNavigate("bindings")}>
            查看绑定
            <ArrowRight size={14} />
          </button>
        }
      >
        {balances.loading ? (
          <LoadingRows />
        ) : balances.error ? (
          <EmptyState title="资产数据暂不可用" detail={balances.error} />
        ) : (
          <div className="balance-summary">
            <BalanceStat label="Points" value={summary.points} detail={`${balances.value?.total ?? 0} 个绑定记录`} />
            <BalanceStat label="HSK 总量" value={summary.hsk} detail="现金余额 + 持仓估值" />
            <BalanceStat label="最近同步" value={latestBalanceTime(balances.value?.items ?? [])} detail="仅显示最新缓存" />
          </div>
        )}
      </Panel>
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
                    <strong>{kindLabel(task.kind)}</strong>
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
      <RuntimePanel
        runtime={runtime.value}
        activeTasks={summary.activeTasks}
        loading={runtime.loading}
        error={runtime.error}
      />
    </div>
  );
}

function NextStagePanel({
  recommendation,
  audit,
  loading,
  error,
  busyKey,
  onNavigate,
  onRunAction
}: {
  recommendation: NextStageRecommendation | null;
  audit: AcceptanceAudit | null;
  loading: boolean;
  error: string | null;
  busyKey: string | null;
  onNavigate: (page: PageKey) => void;
  onRunAction: (action: AcceptanceAudit["actions"][number]) => void;
}) {
  const activeRecommendation = audit?.next_action ?? recommendation;
  const page = recommendationPage(activeRecommendation);
  return (
    <Panel
      title="接口验收清单"
      action={
        <button type="button" className="text-link" onClick={() => onNavigate(page)}>
          操作入口
          <ArrowRight size={14} />
        </button>
      }
    >
      {loading ? (
        <LoadingRows />
      ) : error ? (
        <EmptyState title="建议暂不可用" detail={error} />
      ) : activeRecommendation ? (
        <div className="recommendation-box">
          <div>
            <span>{recommendationActionLabel(activeRecommendation.action)}</span>
            <strong>{activeRecommendation.stage ? kindLabel(activeRecommendation.stage) : "等待状态更新"}</strong>
          </div>
          <p>{activeRecommendation.reason}</p>
          <code>{activeRecommendation.command}</code>
          {audit?.actions.length ? (
            <div className="acceptance-actions" aria-label="验收动作列表">
              {audit.actions.slice(0, 6).map((action) => (
                <button
                  key={`${action.action}-${action.stage}`}
                  type="button"
                  className="acceptance-action"
                  disabled={busyKey !== null}
                  onClick={() => onRunAction(action)}
                >
                  <span>{recommendationActionLabel(action.action)}</span>
                  <strong>{kindLabel(action.stage)}</strong>
                  <small>
                    {busyKey === `${action.action}-${action.stage}` ? (
                      <LoaderCircle className="spin" size={12} />
                    ) : (
                      action.count
                    )}
                  </small>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}

function isPollableStage(stage: string): stage is "bind" | "repost" | "claim" {
  return stage === "bind" || stage === "repost" || stage === "claim";
}

function recommendationActionLabel(action: string) {
  return {
    poll: "批量轮询",
    retry: "批量重试",
    sync_bind_status: "同步绑定状态",
    create_stage: "创建阶段",
    wait: "等待"
  }[action] || action;
}

function recommendationPage(recommendation: NextStageRecommendation | null): PageKey {
  if (
    !recommendation ||
    recommendation.action === "poll" ||
    recommendation.action === "retry" ||
    recommendation.action === "sync_bind_status"
  ) {
    return "tasks";
  }
  if (recommendation.stage === "verify" || recommendation.stage === "bind") {
    return "accounts";
  }
  return "bindings";
}

function StageCard({
  stage,
  onNavigate
}: {
  stage: OperationsSummary["stages"][number];
  onNavigate: (page: PageKey) => void;
}) {
  const icon = {
    verify: <ShieldCheck size={18} />,
    bind: <Link2 size={18} />,
    repost: <Send size={18} />,
    claim: <Play size={18} />
  }[stage.key];
  const targetPage = stage.key === "bind" && stage.status_syncable > 0
    ? "tasks"
    : stage.key === "verify" || stage.key === "bind"
      ? "accounts"
      : "bindings";
  return (
    <button type="button" className="stage-card" onClick={() => onNavigate(targetPage)}>
      <div className="stage-card-top">
        <span className="stage-icon">{icon}</span>
        <span className="stage-key">{stage.label}</span>
      </div>
      <strong>{stage.ready}</strong>
      <span className="stage-detail">{stage.detail}</span>
      <div className="stage-meta">
        <span>{stage.waiting} 待回写</span>
        <span>{stage.failed} 失败</span>
        <span>{stage.pollable} 可轮询</span>
        {stage.status_syncable > 0 && <span>{stage.status_syncable} 可同步</span>}
        <span>{stage.retryable} 可重试</span>
        <ArrowRight size={15} />
      </div>
    </button>
  );
}

function ResourceCard({ label, primary, detail }: { label: string; primary: string; detail: string }) {
  return (
    <div className="resource-card">
      <span>{label}</span>
      <strong>{primary}</strong>
      <small>{detail}</small>
    </div>
  );
}

function RuntimePanel({
  runtime,
  activeTasks,
  loading,
  error
}: {
  runtime: RuntimeMetrics | null;
  activeTasks: number;
  loading: boolean;
  error: string | null;
}) {
  return (
    <Panel title="运行态" action={<span className="subtle-label">{runtime ? dateTime(runtime.generated_at) : "等待数据"}</span>}>
      {loading ? (
        <LoadingRows />
      ) : error ? (
        <EmptyState title="运行态暂不可用" detail={error} />
      ) : runtime ? (
        <>
          {!runtime.workers.active && activeTasks > 0 && (
            <div className="runtime-warning">
              <CircleAlert size={16} />
              <div>
                <strong>任务已入库，等待 Worker</strong>
                <span>当前有 {activeTasks} 个未完成任务；启动 Worker 后才会继续执行。</span>
              </div>
            </div>
          )}
          <div className="runtime-grid">
            <StatusLine label="待处理队列" value={`${runtime.queues.ready}`} tone={runtime.queues.ready ? "warning" : "success"} />
            <StatusLine label="处理中队列" value={`${runtime.queues.processing}`} tone={runtime.queues.processing ? "warning" : "neutral"} />
            <StatusLine label="活跃租约" value={`${runtime.leases.active}`} tone={runtime.leases.active ? "warning" : "success"} />
            <StatusLine label="即将过期租约" value={`${runtime.leases.expiring_soon}`} tone={runtime.leases.expiring_soon ? "danger" : "success"} />
            <StatusLine label="活跃 Worker" value={`${runtime.workers.active}`} tone={runtime.workers.active ? "success" : "danger"} />
            <StatusLine label="最近完成" value={dateTime(runtime.tasks.last_finished_at)} tone="neutral" />
          </div>
        </>
      ) : (
        <EmptyState title="暂无运行态" detail="启动管理 Worker 后，这里会显示队列与租约。" />
      )}
    </Panel>
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
      <ArrowRight size={16} className="metric-arrow" />
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

function sumBalance(items: Balance[], field: "points" | "total_hsk") {
  return items.reduce((total, item) => total + (item[field] === null ? 0 : Number(item[field])), 0);
}

function latestBalanceTime(items: Balance[]) {
  const latest = items
    .map((item) => item.last_synced_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  return latest ? dateTime(latest) : "未同步";
}

function BalanceStat({ label, value, detail }: { label: string; value: number | string; detail: string }) {
  return (
    <div className="balance-stat">
      <span>{label}</span>
      <strong>{typeof value === "number" ? amount(value) : value}</strong>
      <small>{detail}</small>
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
  const accounts = useResource("accounts", api.accounts, refresh);
  const bindings = useResource("bindings", api.bindings, refresh);
  const [importOpen, setImportOpen] = useState(false);
  const [pairedBindOpen, setPairedBindOpen] = useState(false);
  const [bindingAccount, setBindingAccount] = useState<Account | null>(null);
  const [workflowMode, setWorkflowMode] = useState<"verify" | "bind" | null>(null);
  const [verifyingAccountId, setVerifyingAccountId] = useState<string | null>(null);
  const occupiedAccountIds = useMemo(
    () =>
      new Set(
        (bindings.value?.items ?? [])
          .filter((binding) => binding.state !== "archived")
          .map((binding) => binding.social_account_id)
      ),
    [bindings.value]
  );
  const verifyAccount = async (account: Account) => {
    setVerifyingAccountId(account.id);
    try {
      await api.createStageBatch({
        name: `会话校验 @${account.handle}`,
        stage: "verify",
        dispatch_limit: 1,
        items: [
          {
            social_account_id: account.id,
            external_target: "x:verify"
          }
        ]
      });
      onComplete(`已加入 @${account.handle} 的会话校验`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setVerifyingAccountId(null);
    }
  };

  return (
    <div className="page-stack">
      <PageToolbar
        description="导入的 X 账号仅以掩码身份和会话状态显示。"
        action={
          <div className="toolbar-actions">
            <Button onClick={() => setWorkflowMode("verify")} disabled={!vault?.unlocked}>
              <ShieldCheck size={16} />
              批量校验
            </Button>
            <Button onClick={() => setWorkflowMode("bind")} disabled={!vault?.unlocked}>
              <Layers3 size={16} />
              批量绑定
            </Button>
            <Button onClick={() => setPairedBindOpen(true)} disabled={!vault?.unlocked}>
              <Upload size={16} />
              文件配对绑定
            </Button>
            <Button tone="primary" onClick={() => setImportOpen(true)} disabled={!vault?.unlocked}>
              <Plus size={17} />
              导入账号
            </Button>
          </div>
        }
      />
      <Panel>
        {accounts.loading ? (
          <LoadingRows />
        ) : accounts.error ? (
          <EmptyState title="账号列表暂不可用" detail={accounts.error} />
        ) : accounts.value?.items.length ? (
          <div className="table-wrap binding-table">
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
                          <>
                            <Button
                              onClick={() => void verifyAccount(account)}
                              disabled={
                                account.state !== "active" ||
                                !vault?.unlocked ||
                                verifyingAccountId === account.id
                              }
                            >
                              {verifyingAccountId === account.id ? (
                                <LoaderCircle className="spin" size={15} />
                              ) : (
                                <ShieldCheck size={15} />
                              )}
                              校验会话
                            </Button>
                            <Button
                              onClick={() => setBindingAccount(account)}
                              disabled={account.state !== "active" || !vault?.unlocked || occupied}
                            >
                              <Link2 size={15} />
                              {bindingLabel === "bound"
                                ? "已绑定"
                                : bindingLabel === "pending"
                                  ? "绑定中"
                                  : "绑定"}
                            </Button>
                          </>
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
      {pairedBindOpen && (
        <PairedBindDialog
          onClose={() => setPairedBindOpen(false)}
          onComplete={(message) => {
            setPairedBindOpen(false);
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
      {workflowMode && (
        <WorkflowDialog
          mode={workflowMode}
          accounts={accounts.value?.items ?? []}
          refresh={refresh}
          onClose={() => setWorkflowMode(null)}
          onComplete={(message) => {
            setWorkflowMode(null);
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
  const wallets = useResource("wallets", api.wallets, refresh);
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
          <div className="table-wrap binding-table">
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
  const bindings = useResource("bindings", api.bindings, refresh);
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<{ kind: "repost" | "claim"; bindingIds: string[] } | null>(null);
  const [bindStatusSyncOpen, setBindStatusSyncOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [launchingWorkbench, setLaunchingWorkbench] = useState(false);
  const visibleSelectable = useMemo(
    () => bindings.value?.items.filter((binding) => binding.state !== "archived") ?? [],
    [bindings.value]
  );
  const pendingCount = bindings.value?.items.filter((binding) => binding.state === "pending").length ?? 0;
  const selectedBindings = useMemo(
    () => visibleSelectable.filter((binding) => selected.includes(binding.id)),
    [selected, visibleSelectable]
  );
  const selectedBoundBindings = useMemo(
    () => selectedBindings.filter((binding) => binding.state === "bound"),
    [selectedBindings]
  );
  const repostReadySelected = useMemo(
    () => selectedBoundBindings.filter((binding) => bindingStage(binding).can_repost).map((binding) => binding.id),
    [selectedBoundBindings]
  );
  const claimReadySelected = useMemo(
    () => selectedBoundBindings.filter((binding) => bindingStage(binding).can_claim).map((binding) => binding.id),
    [selectedBoundBindings]
  );
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const allVisibleSelected = visibleSelectable.length > 0 && selected.length === visibleSelectable.length;
  const someVisibleSelected = selected.length > 0 && !allVisibleSelected;
  const workbenchLaunchCount = Math.min(selected.length, WORKBENCH_LIMIT);
  const workbenchButtonLabel =
    selected.length > WORKBENCH_LIMIT
      ? `打开工作台 (前 ${WORKBENCH_LIMIT}/${selected.length})`
      : `打开工作台 (${workbenchLaunchCount})`;

  useEffect(() => {
    setSelected((current) => current.filter((id) => visibleSelectable.some((binding) => binding.id === id)));
  }, [bindings.value, visibleSelectable]);
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someVisibleSelected;
  }, [someVisibleSelected]);

  const toggle = (id: string) => {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const syncBalances = async (bindingIds?: string[]) => {
    setSyncing(true);
    try {
      const result = await api.syncBalances(bindingIds);
      onComplete(`已排队 ${result.queued} 条余额同步任务`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setSyncing(false);
    }
  };

  const launchWorkbench = async (bindingIds: string[]) => {
    if (!bindingIds.length || launchingWorkbench) return;
    setLaunchingWorkbench(true);
    try {
      const result = await api.launchManualWorkbench({
        binding_ids: bindingIds.slice(0, WORKBENCH_LIMIT),
        repost_target: DEFAULT_REPOST_TARGET,
        limit: Math.min(bindingIds.length, WORKBENCH_LIMIT),
        timeout_seconds: 45
      });
      onComplete(`已启动 ${result.launched} 个半自动浏览器工作台`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setLaunchingWorkbench(false);
    }
  };

  return (
    <div className="page-stack">
      <PageToolbar
        description="绑定在外部确认后不可更换。批次按独立资源租约调度，默认并发窗口为 10。"
        action={
          <div className="toolbar-actions">
            <Button
              onClick={() => void launchWorkbench(selected)}
              disabled={selected.length === 0 || launchingWorkbench}
              title={selected.length === 0 ? "先选择至少一个绑定" : "打开有头浏览器，预置 X Cookie 和钱包，并自动转发固定推文"}
            >
              {launchingWorkbench ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
              {workbenchButtonLabel}
            </Button>
            <Button
              onClick={() => setBindStatusSyncOpen(true)}
              disabled={!vault?.unlocked || pendingCount === 0}
              title={
                !vault?.unlocked
                  ? "Vault 已锁定，请先解锁"
                  : pendingCount === 0
                    ? "暂无 pending 绑定"
                    : "读取 Kredo 任务接口并同步 pending 绑定状态"
              }
            >
              <ShieldCheck size={16} />
              同步绑定状态 ({pendingCount})
            </Button>
            <Button
              onClick={() => setOperation({ kind: "repost", bindingIds: repostReadySelected })}
              disabled={!vault?.unlocked || repostReadySelected.length === 0}
              title={
                !vault?.unlocked
                  ? "Vault 已锁定，请先解锁"
                  : repostReadySelected.length === 0
                    ? "所选行暂无可创建的转发任务"
                    : "创建转发任务"
              }
            >
              <Send size={16} />
              批量转发 ({repostReadySelected.length})
            </Button>
            <Button
              tone="primary"
              onClick={() => setOperation({ kind: "claim", bindingIds: claimReadySelected })}
              disabled={!vault?.unlocked || claimReadySelected.length === 0}
              title={
                !vault?.unlocked
                  ? "Vault 已锁定，请先解锁"
                  : claimReadySelected.length === 0
                    ? "等待转发校验成功后再领取"
                    : "创建领取任务"
              }
            >
              <Play size={16} />
              批量领取 ({claimReadySelected.length})
            </Button>
            <Button
              onClick={() => void syncBalances(selectedBoundBindings.map((binding) => binding.id))}
              disabled={!vault?.unlocked || selectedBoundBindings.length === 0 || syncing}
              title={!vault?.unlocked ? "Vault 已锁定，请先解锁" : selectedBoundBindings.length === 0 ? "先选择至少一个已绑定行" : "同步所选余额"}
            >
              {syncing ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
              同步余额 ({selectedBoundBindings.length})
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
          <>
            <div className="table-wrap binding-table">
              <table>
              <thead>
                <tr>
                  <th className="checkbox-column">
                    <input
                      ref={selectAllRef}
                      aria-label="选择所有绑定记录"
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={() =>
                        setSelected(selected.length === visibleSelectable.length ? [] : visibleSelectable.map((item) => item.id))
                      }
                    />
                  </th>
                  <th>账号</th>
                  <th>地址</th>
                  <th>绑定状态</th>
                  <th>任务阶段</th>
                  <th>确认时间</th>
                  <th>Points</th>
                  <th>HSK</th>
                  <th>持仓 HSK</th>
                  <th>余额同步</th>
                  <th className="actions-column">操作</th>
                </tr>
              </thead>
              <tbody>
                {bindings.value.items.map((binding) => {
                  const eligible = binding.state === "bound";
                  const selectable = binding.state !== "archived";
                  const stage = bindingStage(binding);
                  const canRepost = eligible && stage.can_repost;
                  const canClaim = eligible && stage.can_claim;
                  return (
                    <tr key={binding.id}>
                      <td className="checkbox-column">
                        <input
                          aria-label={`选择 ${binding.account_handle}`}
                          type="checkbox"
                          checked={selected.includes(binding.id)}
                          disabled={!selectable}
                          onChange={() => toggle(binding.id)}
                        />
                      </td>
                      <td><strong>@{binding.account_handle}</strong></td>
                      <td className="mono">{compact(binding.wallet_address, 12, 8)}</td>
                      <td><StateChip value={binding.state} /></td>
                      <td>
                        <div className="stage-chip-group">
                          {stage.repost_state ? (
                            <span title="转发状态"><StateChip value={stage.repost_state} /></span>
                          ) : (
                            <span className="muted">未转发</span>
                          )}
                          {stage.claim_state ? (
                            <span title="领取状态"><StateChip value={stage.claim_state} /></span>
                          ) : canClaim ? (
                            <span className="chip chip-success">可领取</span>
                          ) : (
                            <span className="muted">未领取</span>
                          )}
                        </div>
                      </td>
                      <td>{dateTime(binding.bound_at)}</td>
                      <td className="mono">{amount(binding.balance?.points)}</td>
                      <td className="mono">{amount(binding.balance?.cash_hsk_available)}</td>
                      <td className="mono">{amount(binding.balance?.positions_value_hsk)}</td>
                      <td>
                        <span className={`sync-state sync-${binding.balance?.sync_status ?? "never"}`}>
                          {binding.balance?.sync_status === "success"
                            ? dateTime(binding.balance.last_synced_at)
                            : binding.balance?.sync_status === "error"
                              ? "失败"
                              : "未同步"}
                        </span>
                      </td>
                      <td className="row-actions">
                        <Button
                          disabled={!selectable || launchingWorkbench}
                          onClick={() => void launchWorkbench([binding.id])}
                          title="打开有头浏览器，预置 X Cookie 和钱包，并自动转发固定推文"
                        >
                          {launchingWorkbench ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}
                          工作台
                        </Button>
                        <Button
                          disabled={!eligible || !vault?.unlocked || syncing}
                          onClick={() => void syncBalances([binding.id])}
                        >
                          <RefreshCw size={14} />
                          同步
                        </Button>
                        <Button
                          disabled={!canRepost || !vault?.unlocked}
                          onClick={() => setOperation({ kind: "repost", bindingIds: [binding.id] })}
                        >
                          转发
                        </Button>
                        <Button
                          tone="primary"
                          disabled={!canClaim || !vault?.unlocked}
                          onClick={() => setOperation({ kind: "claim", bindingIds: [binding.id] })}
                          title={canClaim ? "创建领取任务" : "等待转发校验成功后再领取"}
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
            <div className="binding-mobile-list">
              {bindings.value.items.map((binding) => {
                const eligible = binding.state === "bound";
                const selectable = binding.state !== "archived";
                const stage = bindingStage(binding);
                const canRepost = eligible && stage.can_repost;
                const canClaim = eligible && stage.can_claim;
                return (
                  <article className="binding-mobile-card" key={binding.id}>
                  <div className="binding-mobile-heading">
                    <label className="binding-mobile-select">
                      <input
                        aria-label={`选择 ${binding.account_handle}`}
                        type="checkbox"
                        checked={selected.includes(binding.id)}
                        disabled={!selectable}
                        onChange={() => toggle(binding.id)}
                      />
                      <strong>@{binding.account_handle}</strong>
                    </label>
                    <StateChip value={binding.state} />
                  </div>
                  <div className="binding-mobile-meta">
                    <span className="mono">{compact(binding.wallet_address, 12, 8)}</span>
                    <span>绑定于 {dateTime(binding.bound_at)}</span>
                  </div>
                  <div className="binding-mobile-balances">
                    <span><small>Points</small><strong>{amount(binding.balance?.points)}</strong></span>
                    <span><small>HSK</small><strong>{amount(binding.balance?.total_hsk)}</strong></span>
                    <span><small>阶段</small><strong>{canClaim ? "可领取" : stage.repost_waiting ? "等回写" : stage.repost_state ? stateLabel(stage.repost_state) : "未转发"}</strong></span>
                  </div>
                  <div className="binding-mobile-actions">
                    <Button
                      disabled={!selectable || launchingWorkbench}
                      onClick={() => void launchWorkbench([binding.id])}
                    >
                      {launchingWorkbench ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}
                      工作台
                    </Button>
                    <Button
                      disabled={!eligible || !vault?.unlocked || syncing}
                      onClick={() => void syncBalances([binding.id])}
                    >
                      {syncing ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
                      同步
                    </Button>
                    <Button
                      disabled={!canRepost || !vault?.unlocked}
                      onClick={() => setOperation({ kind: "repost", bindingIds: [binding.id] })}
                    >
                      <Send size={14} />
                      转发
                    </Button>
                    <Button
                      tone="primary"
                      disabled={!canClaim || !vault?.unlocked}
                      onClick={() => setOperation({ kind: "claim", bindingIds: [binding.id] })}
                      title={canClaim ? "创建领取任务" : "等待转发校验成功后再领取"}
                    >
                      <Play size={14} />
                      领取
                    </Button>
                  </div>
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <EmptyState title="暂无绑定" detail="从账号页面选择一个未绑定账号，随后选择地址创建绑定任务。" />
        )}
      </Panel>
      {!vault?.unlocked && <LockedHint label="Vault 已锁定：解锁后才可创建同步、转发或领取任务。" />}
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
      {bindStatusSyncOpen && (
        <BindStatusSyncDialog
          onClose={() => setBindStatusSyncOpen(false)}
          onComplete={(message) => {
            setBindStatusSyncOpen(false);
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
  const [pollTick, setPollTick] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [batchFilter, setBatchFilter] = useState("all");
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [stagePollOpen, setStagePollOpen] = useState(false);
  const [stageRetryOpen, setStageRetryOpen] = useState(false);
  const [bindStatusSyncOpen, setBindStatusSyncOpen] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [busyCommand, setBusyCommand] = useState<string | null>(null);
  const resourceKey = refresh + pollTick;
  const tasks = useResource("tasks", api.tasks, resourceKey, { ttlMs: 7_500 });
  const batches = useResource("task-batches", api.taskBatches, resourceKey, { ttlMs: 7_500 });
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => setPollTick((value) => value + 1), 8000);
    return () => window.clearInterval(timer);
  }, [autoRefresh]);

  useEffect(() => {
    if (!tasks.loading && tasks.value) {
      setLastUpdatedAt(Date.now());
    }
  }, [tasks.loading, tasks.value]);

  useEffect(() => {
    if (selectedBatchId && !batches.value?.items.some((batch) => batch.id === selectedBatchId)) {
      setSelectedBatchId(null);
    }
  }, [batches.value, selectedBatchId]);

  const command = async (task: Task, action: "pause" | "cancel" | "retry" | "poll") => {
    if (busyCommand) return;
    setBusyCommand(`${task.id}:${action}`);
    try {
      await api.taskCommand(task.id, action);
      onComplete(`任务已${action === "poll" ? "加入轮询" : action === "retry" ? "重试" : action === "pause" ? "暂停" : "取消"}`);
      setSelectedTask(null);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusyCommand(null);
    }
  };
  const batchCommand = async (batch: TaskBatch, action: "pause" | "resume" | "cancel") => {
    if (busyCommand) return;
    setBusyCommand(`${batch.id}:${action}`);
    try {
      await api.batchCommand(batch.id, action);
      onComplete(
        action === "resume"
          ? `批次“${batch.name}”已恢复`
          : action === "pause"
            ? `批次“${batch.name}”已暂停`
            : `批次“${batch.name}”已取消`
      );
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusyCommand(null);
    }
  };
  const visibleTasks = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const selectedBatch = batches.value?.items.find((batch) => batch.id === batchFilter);
    const batchTaskIds = selectedBatch ? new Set(selectedBatch.jobs.map((job) => job.id)) : null;
    return (tasks.value?.items ?? []).filter((task) => {
      const matchesState = stateFilter === "all" || task.state === stateFilter;
      const matchesBatch = !batchTaskIds || batchTaskIds.has(task.id);
      const haystack = [
        task.kind,
        task.state,
        task.id,
        task.result_summary,
        task.failure_code,
        task.external_operation_ref,
        task.social_account_id,
        task.binding_id
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return matchesState && matchesBatch && (!needle || haystack.includes(needle));
    });
  }, [batchFilter, batches.value, search, stateFilter, tasks.value]);
  const selectedBatch = batches.value?.items.find((batch) => batch.id === selectedBatchId) ?? null;
  const stateOptions = useMemo(
    () => Array.from(new Set((tasks.value?.items ?? []).map((task) => task.state))).sort(),
    [tasks.value]
  );

  return (
    <div className="page-stack">
      <PageToolbar
        description="每个任务都保留独立事件链、租约与重试状态。"
        action={
          <div className="task-toolbar-meta">
            <span className="subtle-label">
              {autoRefresh ? "自动刷新 · 8s" : "手动刷新"}
              {lastUpdatedAt ? ` · ${dateTime(new Date(lastUpdatedAt).toISOString())}` : ""}
            </span>
            <IconButton
              label={autoRefresh ? "暂停自动刷新" : "开启自动刷新"}
              onClick={() => setAutoRefresh((value) => !value)}
              tone={autoRefresh ? "primary" : "neutral"}
            >
              <RefreshCw size={17} className={autoRefresh ? "spin-soft" : ""} />
            </IconButton>
            <IconButton label="立即刷新任务" onClick={() => setPollTick((value) => value + 1)}>
              <RefreshCw size={17} />
            </IconButton>
            <Button onClick={() => setStagePollOpen(true)}>
              <RefreshCw size={16} />
              批量轮询
            </Button>
            <Button onClick={() => setBindStatusSyncOpen(true)}>
              <ShieldCheck size={16} />
              同步绑定状态
            </Button>
            <Button onClick={() => setStageRetryOpen(true)}>
              <RotateCcw size={16} />
              批量重试
            </Button>
            <span className="subtle-label">{batches.value?.total ?? 0} 个批次</span>
          </div>
        }
      />
      <Panel className="task-filters">
        <div className="filter-field">
          <Search size={16} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索任务类型、结果、资源 ID"
            aria-label="搜索任务"
          />
        </div>
        <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)} aria-label="按状态筛选">
          <option value="all">全部状态</option>
          {stateOptions.map((state) => (
            <option key={state} value={state}>{stateLabel(state)}</option>
          ))}
        </select>
        <select value={batchFilter} onChange={(event) => setBatchFilter(event.target.value)} aria-label="按批次筛选">
          <option value="all">全部批次</option>
          {(batches.value?.items ?? []).map((batch) => (
            <option key={batch.id} value={batch.id}>{batch.name}</option>
          ))}
        </select>
        <span className="filter-count">
          {tasks.loading ? "加载中…" : `显示 ${visibleTasks.length} / ${tasks.value?.total ?? 0}`}
        </span>
      </Panel>
      <div className="tasks-layout">
        <Panel className="task-table-panel">
          {tasks.loading ? (
            <LoadingRows />
          ) : tasks.error ? (
            <EmptyState title="任务列表暂不可用" detail={tasks.error} />
          ) : visibleTasks.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>状态</th>
                    <th>尝试</th>
                    <th>资源</th>
                    <th>计划时间</th>
                    <th>目标</th>
                    <th className="actions-column">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleTasks.map((task) => (
                    <tr key={task.id} className={selectedTask?.id === task.id ? "is-selected" : ""}>
                      <td><strong>{kindLabel(task.kind)}</strong></td>
                      <td><StateChip value={task.state} /></td>
                      <td>{task.attempt}</td>
                      <td className="mono">{compact(task.binding_id || task.social_account_id, 7, 5)}</td>
                      <td>{dateTime(task.scheduled_at)}</td>
                      <td>
                        <span className={task.target_configured ? "sync-state sync-success" : "sync-state sync-error"}>
                          {task.target_configured ? "已配置" : "缺失"}
                        </span>
                      </td>
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
        <TaskDetail
          task={selectedTask}
          onCommand={command}
          onClose={() => setSelectedTask(null)}
          busyCommand={busyCommand}
        />
      </div>
      {batches.value?.items.length ? (
        <Panel title="最近批次">
            <div className="batch-list">
            {batches.value.items.slice(0, 5).map((batch) => (
              <BatchRow
                key={batch.id}
                batch={batch}
                selected={batch.id === selectedBatchId}
                onSelect={() => setSelectedBatchId(batch.id)}
                onCommand={batchCommand}
                busyCommand={busyCommand}
              />
            ))}
          </div>
        </Panel>
      ) : null}
      {selectedBatch && (
        <BatchDetail
          batch={selectedBatch}
          onTaskSelect={setSelectedTask}
          onClose={() => setSelectedBatchId(null)}
        />
      )}
      {stagePollOpen && (
        <StagePollDialog
          onClose={() => setStagePollOpen(false)}
          onComplete={(message) => {
            setStagePollOpen(false);
            setPollTick((value) => value + 1);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
      {stageRetryOpen && (
        <StageRetryDialog
          onClose={() => setStageRetryOpen(false)}
          onComplete={(message) => {
            setStageRetryOpen(false);
            setPollTick((value) => value + 1);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
      {bindStatusSyncOpen && (
        <BindStatusSyncDialog
          onClose={() => setBindStatusSyncOpen(false)}
          onComplete={(message) => {
            setBindStatusSyncOpen(false);
            setPollTick((value) => value + 1);
            onComplete(message);
          }}
          onNotice={onNotice}
        />
      )}
    </div>
  );
}

function StagePollDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [stage, setStage] = useState<"bind" | "repost" | "claim">("bind");
  const [limit, setLimit] = useState(10);
  const [preview, setPreview] = useState<StagePollRequeueResult | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (apply: boolean) => {
    setBusy(true);
    try {
      const result = await api.requeueStagePolls({ stage, limit, apply });
      setPreview(result);
      if (apply) {
        onComplete(`已重新入队 ${result.requeued} 个${kindLabel(stage)}状态轮询`);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="批量轮询等待任务" onClose={onClose}>
      <div className="dialog-form">
        <div className="workflow-header">
          <label>
            <span>阶段</span>
            <select
              value={stage}
              onChange={(event) => {
                setStage(event.target.value as "bind" | "repost" | "claim");
                setPreview(null);
              }}
            >
              <option value="bind">绑定地址</option>
              <option value="repost">转发推文</option>
              <option value="claim">领取奖励</option>
            </select>
          </label>
          <label>
            <span>数量上限</span>
            <input
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(event) => {
                setLimit(Number(event.target.value) || 1);
                setPreview(null);
              }}
            />
          </label>
        </div>
        {preview && (
          <div className="preview-summary">
            <span>选中 {preview.selected}</span>
            <span>已入队 {preview.requeued}</span>
            <span>缺少引用 {preview.skipped_missing_ref}</span>
          </div>
        )}
        <p className="form-hint">
          只处理已经有外部引用的等待任务。下一轮 worker 会读取外部状态，不重新发起该阶段动作。
        </p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button onClick={() => void submit(false)} disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            预览
          </Button>
          <Button
            tone="primary"
            onClick={() => void submit(true)}
            disabled={busy || !preview || preview.selected === 0}
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
            重新入队
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function BindStatusSyncDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [name, setName] = useState("bind status sync");
  const [limit, setLimit] = useState(10);
  const [dispatchLimit, setDispatchLimit] = useState(10);
  const [preview, setPreview] = useState<BindStatusSyncResult | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (apply: boolean) => {
    setBusy(true);
    try {
      const result = await api.bindStatusSync({
        name,
        limit,
        dispatch_limit: dispatchLimit,
        apply
      });
      setPreview(result);
      if (apply) {
        onComplete(`已创建 ${result.created_jobs} 个绑定状态同步任务`);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="同步绑定状态" onClose={onClose}>
      <div className="dialog-form">
        <div className="workflow-header">
          <label>
            <span>批次名称</span>
            <input
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setPreview(null);
              }}
            />
          </label>
          <label>
            <span>数量上限</span>
            <input
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(event) => {
                setLimit(Number(event.target.value) || 1);
                setPreview(null);
              }}
            />
          </label>
          <label>
            <span>并发窗口</span>
            <input
              type="number"
              min={1}
              max={32}
              value={dispatchLimit}
              onChange={(event) => {
                setDispatchLimit(Number(event.target.value) || 1);
                setPreview(null);
              }}
            />
          </label>
        </div>
        {preview && (
          <div className="preview-summary">
            <span>Pending {preview.pending_bindings}</span>
            <span>选中 {preview.selected}</span>
            <span>新建 {preview.created_jobs}</span>
            <span>复用 {preview.reused_jobs}</span>
            <span>暂停首绑 {preview.paused_action_jobs}</span>
            <span>已有状态任务 {preview.skipped_existing_status_job}</span>
            <span>租约跳过 {preview.skipped_active_lease}</span>
            <span>缺密钥 {preview.skipped_missing_secret}</span>
          </div>
        )}
        <p className="form-hint">
          只为 pending 绑定创建 Kredo 任务状态读取，不重新打开 X OAuth。已有未执行的首绑任务会暂停，避免重复点击绑定。
        </p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button onClick={() => void submit(false)} disabled={busy || !name.trim()}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            预览
          </Button>
          <Button
            tone="primary"
            onClick={() => void submit(true)}
            disabled={busy || !preview || preview.selected === 0 || !name.trim()}
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
            入队同步
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function StageRetryDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [stage, setStage] = useState<"verify" | "bind" | "repost" | "claim">("bind");
  const [limit, setLimit] = useState(10);
  const [preview, setPreview] = useState<StageRetryResult | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (apply: boolean) => {
    setBusy(true);
    try {
      const result = await api.retryStageFailures({ stage, limit, apply });
      setPreview(result);
      if (apply) {
        onComplete(`已重试 ${result.retried} 个${kindLabel(stage)}失败任务`);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="批量重试失败任务" onClose={onClose}>
      <div className="dialog-form">
        <div className="workflow-header">
          <label>
            <span>阶段</span>
            <select
              value={stage}
              onChange={(event) => {
                setStage(event.target.value as "verify" | "bind" | "repost" | "claim");
                setPreview(null);
              }}
            >
              <option value="verify">校验账号</option>
              <option value="bind">绑定地址</option>
              <option value="repost">转发推文</option>
              <option value="claim">领取奖励</option>
            </select>
          </label>
          <label>
            <span>数量上限</span>
            <input
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(event) => {
                setLimit(Number(event.target.value) || 1);
                setPreview(null);
              }}
            />
          </label>
        </div>
        {preview && (
          <div className="preview-summary">
            <span>选中 {preview.selected}</span>
            <span>已重试 {preview.retried}</span>
          </div>
        )}
        <p className="form-hint">
          只处理该阶段已经失败的任务。重试会保留原任务事件链并增加尝试次数，不创建新的绑定关系。
        </p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button onClick={() => void submit(false)} disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            预览
          </Button>
          <Button
            tone="primary"
            onClick={() => void submit(true)}
            disabled={busy || !preview || preview.selected === 0}
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />}
            重试
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function TaskDetail({
  task,
  onCommand,
  onClose,
  busyCommand
}: {
  task: Task | null;
  onCommand: (task: Task, action: "pause" | "cancel" | "retry" | "poll") => void;
  onClose: () => void;
  busyCommand: string | null;
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
          <strong>{kindLabel(task.kind)}</strong>
        </div>
        <div>
          <span>状态</span>
          <StateChip value={task.state} />
        </div>
        <div>
          <span>外部引用</span>
          <strong className="mono">{compact(task.external_operation_ref)}</strong>
        </div>
        <div>
          <span>任务目标</span>
          <strong className={task.target_configured ? "success-text" : "danger-text"}>
            {task.target_configured ? "已配置（原文已隐藏）" : "缺失"}
          </strong>
        </div>
      </div>
      <div className="command-row">
        <Button disabled={!canPoll || Boolean(busyCommand)} onClick={() => onCommand(task, "poll")}>
          <RefreshCw size={15} />
          轮询
        </Button>
        <Button disabled={!canRetry || Boolean(busyCommand)} onClick={() => onCommand(task, "retry")}>
          <RotateCcw size={15} />
          重试
        </Button>
        <Button disabled={!canPause || Boolean(busyCommand)} onClick={() => onCommand(task, "pause")}>
          <Clock3 size={15} />
          暂停
        </Button>
        <Button tone="danger" disabled={!canCancel || Boolean(busyCommand)} onClick={() => onCommand(task, "cancel")}>
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
                <small>
                  {event.from_state
                    ? `${stateLabel(event.from_state)} → ${stateLabel(event.to_state || "")}`
                    : stateLabel(event.to_state || event.event_type)}
                </small>
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

function BatchRow({
  batch,
  selected,
  onSelect,
  onCommand,
  busyCommand
}: {
  batch: TaskBatch;
  selected: boolean;
  onSelect: () => void;
  onCommand: (batch: TaskBatch, action: "pause" | "resume" | "cancel") => void;
  busyCommand: string | null;
}) {
  const states = batch.jobs.reduce<Record<string, number>>((counts, task) => {
    counts[task.state] = (counts[task.state] ?? 0) + 1;
    return counts;
  }, {});
  const completed = batch.jobs.filter((task) =>
    ["succeeded", "failed", "cancelled"].includes(task.state)
  ).length;
  const failed = states.failed ?? 0;
  const completion = batch.jobs.length ? Math.round((completed / batch.jobs.length) * 100) : 0;
  return (
    <div className={`batch-row ${selected ? "is-selected" : ""}`}>
      <div className="batch-icon"><Layers3 size={17} /></div>
      <div>
        <strong>{batch.name}</strong>
        <span>{workflowLabel(batch.workflow_type)} · 调度 {batch.dispatch_limit}</span>
        <div className="batch-progress" aria-label={`${batch.name} 完成 ${completion}%`}>
          <span><i style={{ width: `${completion}%` }} /></span>
          <small>{completed}/{batch.jobs.length} · {completion}%{failed ? ` · ${failed} 失败` : ""}</small>
        </div>
      </div>
      <div className="batch-states">
        <StateChip value={batch.state} />
        {Object.entries(states).map(([state, count]) => (
          <span key={state}><StateChip value={state} /> {count}</span>
        ))}
      </div>
      <div className="batch-actions">
        <IconButton label="查看批次详情" onClick={onSelect} tone={selected ? "primary" : "neutral"}>
          <ChevronRight size={15} />
        </IconButton>
        {batch.state === "active" && (
          <IconButton label="暂停批次" disabled={Boolean(busyCommand)} onClick={() => onCommand(batch, "pause")}>
            <Clock3 size={15} />
          </IconButton>
        )}
        {batch.state === "paused" && (
          <IconButton label="恢复批次" tone="primary" disabled={Boolean(busyCommand)} onClick={() => onCommand(batch, "resume")}>
            <Play size={15} />
          </IconButton>
        )}
        {["active", "paused"].includes(batch.state) && (
          <IconButton label="取消批次" tone="danger" disabled={Boolean(busyCommand)} onClick={() => onCommand(batch, "cancel")}>
            <X size={15} />
          </IconButton>
        )}
      </div>
      <time>{dateTime(batch.created_at)}</time>
    </div>
  );
}

function BatchDetail({
  batch,
  onTaskSelect,
  onClose
}: {
  batch: TaskBatch;
  onTaskSelect: (task: Task) => void;
  onClose: () => void;
}) {
  const counts = batch.jobs.reduce<Record<string, number>>((result, task) => {
    result[task.state] = (result[task.state] ?? 0) + 1;
    return result;
  }, {});
  const finished = batch.jobs.filter((task) =>
    ["succeeded", "failed", "cancelled"].includes(task.state)
  ).length;
  const completion = batch.jobs.length ? Math.round((finished / batch.jobs.length) * 100) : 0;
  const configuredTargets = batch.jobs.filter((task) => task.target_configured).length;
  return (
    <Panel
      title={`批次详情 · ${batch.name}`}
      className="batch-detail-panel"
      action={<IconButton label="关闭批次详情" onClick={onClose}><X size={17} /></IconButton>}
    >
      <div className="batch-detail-summary">
        <div><span>阶段</span><strong>{workflowLabel(batch.workflow_type)}</strong></div>
        <div><span>调度窗口</span><strong>{batch.dispatch_limit}</strong></div>
        <div><span>完成度</span><strong>{finished}/{batch.jobs.length} · {completion}%</strong></div>
        <div><span>目标配置</span><strong>{configuredTargets}/{batch.jobs.length} 已配置</strong></div>
        <div>
          <span>状态分布</span>
          <strong>
            {Object.entries(counts).map(([state, count]) => `${stateLabel(state)} ${count}`).join(" · ") || "—"}
          </strong>
        </div>
      </div>
      <div className="batch-job-list">
        {batch.jobs.map((task) => (
          <button key={task.id} type="button" className="batch-job-row" onClick={() => onTaskSelect(task)}>
            <span className={`timeline-dot tone-${stateTone(task.state)}`} />
            <strong>{kindLabel(task.kind)}</strong>
            <StateChip value={task.state} />
            <span className="mono">{compact(task.binding_id || task.social_account_id, 9, 6)}</span>
            <small>{task.result_summary || task.failure_code || "等待执行"}</small>
            <ChevronRight size={15} />
          </button>
        ))}
      </div>
    </Panel>
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
  const [backupMode, setBackupMode] = useState<"create" | "verify" | "restore" | null>(null);
  const [recoveryKey, setRecoveryKey] = useState<string | null>(null);
  const [backupResult, setBackupResult] = useState<(VaultBackupSummary & { restored?: boolean }) | null>(null);
  const vault = useResource("vault-status", api.vaultStatus, refresh);
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
              <strong>加密备份包</strong>
              <p>用恢复密钥生成可迁移的加密包，支持下载、校验和空库恢复。</p>
            </div>
            <div className="vault-backup-actions">
              <Button onClick={() => setBackupMode("create")} disabled={!active?.initialized}>
                <Download size={16} />
                导出
              </Button>
              <Button onClick={() => setBackupMode("verify")} disabled={!active?.initialized}>
                <ShieldCheck size={16} />
                校验
              </Button>
              <Button onClick={() => setBackupMode("restore")} disabled={!active?.initialized}>
                <Upload size={16} />
                恢复
              </Button>
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
      {backupMode && (
        <BackupDialog
          mode={backupMode}
          onClose={() => setBackupMode(null)}
          onResult={(result) => {
            setBackupMode(null);
            setBackupResult(result);
            onComplete(result.restored ? "备份已恢复并通过校验" : "备份校验通过");
          }}
          onNotice={onNotice}
        />
      )}
      {backupResult && (
        <Dialog title="备份结果" onClose={() => setBackupResult(null)}>
          <div className="backup-result">
            <CircleCheck size={22} />
            <strong>{backupResult.restored ? "恢复完成" : "备份校验通过"}</strong>
            <div className="backup-result-grid">
              <span>格式版本</span><strong>{backupResult.format_version}</strong>
              <span>数据表</span><strong>{backupResult.table_count}</strong>
              <span>数据行</span><strong>{backupResult.row_count}</strong>
              <span>恢复密钥</span><StateChip value={backupResult.vault_recovery_key_valid ? "valid" : "invalid"} />
              <span>校验和</span><StateChip value={backupResult.checksums_valid ? "valid" : "invalid"} />
            </div>
            <div className="dialog-actions">
              <Button tone="primary" onClick={() => setBackupResult(null)}>
                <Check size={16} />
                完成
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function BackupDialog({
  mode,
  onClose,
  onResult,
  onNotice
}: {
  mode: "create" | "verify" | "restore";
  onClose: () => void;
  onResult: (result: VaultBackupSummary & { restored?: boolean }) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [recoveryKey, setRecoveryKey] = useState("");
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const isCreate = mode === "create";
  const isRestore = mode === "restore";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!recoveryKey.trim() || (!isCreate && !packageFile)) {
      onNotice(isCreate ? "请输入恢复密钥" : "请输入恢复密钥并选择备份文件", "error");
      return;
    }
    setBusy(true);
    try {
      if (isCreate) {
        const result = await api.createBackup(recoveryKey.trim());
        const url = URL.createObjectURL(result.blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = result.filename;
        anchor.click();
        URL.revokeObjectURL(url);
        onResult({
          format_version: Number(result.formatVersion ?? 0),
          table_count: Number(result.tableCount ?? 0),
          row_count: Number(result.rowCount ?? 0),
          vault_recovery_key_valid: true,
          checksums_valid: true
        });
      } else {
        const result = isRestore
          ? await api.restoreBackup(recoveryKey.trim(), packageFile!)
          : await api.verifyBackup(recoveryKey.trim(), packageFile!);
        onResult(result);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={isCreate ? "导出加密备份" : isRestore ? "恢复加密备份" : "校验加密备份"} onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        <label>
          <span>恢复密钥</span>
          <input
            type="password"
            autoComplete="off"
            value={recoveryKey}
            onChange={(event) => setRecoveryKey(event.target.value)}
            placeholder="输入初始化时保存的恢复密钥"
            required
          />
        </label>
        {!isCreate && (
          <label>
            <span>备份文件</span>
            <input
              type="file"
              accept=".json,application/json,application/octet-stream"
              onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)}
              required
            />
          </label>
        )}
        <p className="form-hint">
          {isCreate
            ? "文件只在本地生成下载，不会把恢复密钥写入页面状态或服务端日志。"
            : isRestore
              ? "恢复只允许写入空的管理库，完成后应重新执行只读解密验证。"
              : "校验只读取文件，不会修改当前管理库。"}
        </p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button tone="primary" type="submit" disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : isCreate ? <Download size={16} /> : <ShieldCheck size={16} />}
            {busy ? "处理中" : isCreate ? "导出" : isRestore ? "恢复" : "开始校验"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function PageToolbar({ description, action }: { description: string; action?: ReactNode }) {
  return (
    <div className="page-toolbar">
      <div className="page-toolbar-copy">
        <span>WORKSPACE</span>
        <p>{description}</p>
      </div>
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
  const privateKeyInput = { content: secret, label_prefix: label };

  const previewWallet = async () => {
    setBusy(true);
    try {
      setPreview(
        sourceType === "private_key"
          ? await api.previewPrivateKeyBatch(privateKeyInput)
          : await api.previewWallet(input)
      );
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };
  const commit = async () => {
    setBusy(true);
    try {
      const result = sourceType === "private_key"
        ? await api.commitPrivateKeyBatch(privateKeyInput)
        : await api.commitWallet(input);
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
          <span>{sourceType === "private_key" ? "私钥（一行一个）" : "助记词"}</span>
          <textarea
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            rows={sourceType === "private_key" ? 8 : 4}
            spellCheck={false}
            placeholder={sourceType === "private_key" ? "0x...\n0x...\n# 可用 # 写注释行" : ""}
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
  const wallets = useResource("wallets", api.wallets, refresh);
  const [walletId, setWalletId] = useState("");
  const [target, setTarget] = useState("kredo:bind");
  const [busy, setBusy] = useState(false);
  const eligibleWallets = wallets.value?.items.filter((wallet) => wallet.state === "active" && !wallet.is_bound) ?? [];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!walletId) return;
    setBusy(true);
    try {
      await api.createStageBatch({
        name: `绑定 @${account.handle}`,
        stage: "bind",
        dispatch_limit: 1,
        items: [
          {
            social_account_id: account.id,
            wallet_id: walletId,
            external_target: target
          }
        ]
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

type WorkflowPair = { accountId: string; walletId: string };

function WorkflowDialog({
  mode,
  accounts,
  refresh,
  onClose,
  onComplete,
  onNotice
}: {
  mode: "verify" | "bind";
  accounts: Account[];
  refresh: number;
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const wallets = useResource("wallets", api.wallets, refresh);
  const bindings = useResource("bindings", api.bindings, refresh);
  const [name, setName] = useState(
    mode === "verify"
      ? `会话校验 ${new Date().toLocaleDateString("zh-CN")}`
      : `绑定 ${new Date().toLocaleDateString("zh-CN")}`
  );
  const [dispatchLimit, setDispatchLimit] = useState(10);
  const [pairs, setPairs] = useState<WorkflowPair[]>([{ accountId: "", walletId: "" }]);
  const [busy, setBusy] = useState(false);

  const occupiedAccounts = useMemo(
    () =>
      new Set(
        (bindings.value?.items ?? [])
          .filter((binding) => binding.state !== "archived")
          .map((binding) => binding.social_account_id)
      ),
    [bindings.value]
  );
  const occupiedWallets = useMemo(
    () =>
      new Set(
        (bindings.value?.items ?? [])
          .filter((binding) => binding.state !== "archived")
          .map((binding) => binding.wallet_id)
      ),
    [bindings.value]
  );
  const availableAccounts = accounts.filter((account) => account.state === "active" && !occupiedAccounts.has(account.id));
  const availableWallets = (wallets.value?.items ?? []).filter(
    (wallet) => wallet.state === "active" && !occupiedWallets.has(wallet.id)
  );
  const selectableAccounts = mode === "verify" ? accounts.filter((account) => account.state === "active") : availableAccounts;

  useEffect(() => {
    setName(
      mode === "verify"
        ? `会话校验 ${new Date().toLocaleDateString("zh-CN")}`
        : `绑定 ${new Date().toLocaleDateString("zh-CN")}`
    );
    setPairs([{ accountId: "", walletId: "" }]);
    setBusy(false);
  }, [mode]);

  const autoPair = () => {
    if (mode === "verify") {
      const pairCount = Math.min(10, selectableAccounts.length);
      setPairs(
        Array.from({ length: pairCount }, (_, index) => ({
          accountId: selectableAccounts[index].id,
          walletId: ""
        }))
      );
      return;
    }
    const pairCount = Math.min(10, availableAccounts.length, availableWallets.length);
    setPairs(
      Array.from({ length: pairCount }, (_, index) => ({
        accountId: availableAccounts[index].id,
        walletId: availableWallets[index].id
      }))
    );
  };

  const updatePair = (index: number, field: keyof WorkflowPair, value: string) => {
    setPairs((current) =>
      current.map((pair, pairIndex) =>
        pairIndex === index ? { ...pair, [field]: value } : pair
      )
    );
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pairs.some((pair) => !pair.accountId || (mode === "bind" && !pair.walletId)) || !name.trim()) {
      return;
    }
    setBusy(true);
    try {
      await api.createStageBatch({
        name,
        stage: mode,
        dispatch_limit: dispatchLimit,
        items: pairs.map((pair) =>
          mode === "verify"
            ? {
                social_account_id: pair.accountId,
                external_target: "x:verify"
              }
            : {
                social_account_id: pair.accountId,
                wallet_id: pair.walletId,
                external_target: "kredo:bind"
              }
        )
      });
      onComplete(`已创建 ${pairs.length} 组${mode === "verify" ? "校验" : "绑定"}任务`);
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title={mode === "verify" ? "创建批量校验" : "创建批量绑定"} onClose={onClose} wide>
      <form className="dialog-form" onSubmit={submit}>
        <div className="workflow-header">
          <label>
            <span>批次名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
          <label>
            <span>并发窗口</span>
            <input
              type="number"
              min={1}
              max={32}
              value={dispatchLimit}
              onChange={(event) => setDispatchLimit(Number(event.target.value) || 1)}
              required
            />
          </label>
        </div>
        <div className="workflow-pairs">
          <div className="workflow-pairs-heading">
            <strong>{mode === "verify" ? "账号选择" : "账号与地址配对"}</strong>
            <div className="toolbar-actions">
              <Button
                onClick={autoPair}
                disabled={!selectableAccounts.length || (mode === "bind" && !availableWallets.length)}
              >
                <RefreshCw size={15} />
                {mode === "verify" ? "自动填充前 10 个账号" : "自动配对前 10 组"}
              </Button>
              {mode === "bind" && (
                <Button
                  onClick={() => setPairs((current) => [...current, { accountId: "", walletId: "" }])}
                  disabled={pairs.length >= Math.min(availableAccounts.length, availableWallets.length)}
                >
                  <Plus size={15} />
                  添加配对
                </Button>
              )}
            </div>
          </div>
          {pairs.map((pair, index) => {
            const otherAccountIds = mode === "bind"
              ? new Set(pairs.filter((_, pairIndex) => pairIndex !== index).map((item) => item.accountId))
              : new Set<string>();
            const otherWalletIds = new Set(
              pairs.filter((_, pairIndex) => pairIndex !== index).map((item) => item.walletId)
            );
            return (
              <div className={`workflow-pair-row ${mode === "verify" ? "workflow-pair-row-single" : ""}`} key={index}>
                <span className="pair-index">{String(index + 1).padStart(2, "0")}</span>
                <select
                  value={pair.accountId}
                  onChange={(event) => updatePair(index, "accountId", event.target.value)}
                  required
                >
                  <option value="">选择账号</option>
                  {selectableAccounts
                    .filter((account) => mode === "verify" || account.id === pair.accountId || !otherAccountIds.has(account.id))
                    .map((account) => (
                      <option key={account.id} value={account.id}>@{account.handle}</option>
                    ))}
                </select>
                {mode === "bind" ? (
                  <>
                    <ArrowRight size={16} />
                    <select
                      value={pair.walletId}
                      onChange={(event) => updatePair(index, "walletId", event.target.value)}
                      required
                    >
                      <option value="">选择地址</option>
                      {availableWallets
                        .filter((wallet) => wallet.id === pair.walletId || !otherWalletIds.has(wallet.id))
                        .map((wallet) => (
                          <option key={wallet.id} value={wallet.id}>{compact(wallet.address, 12, 8)}</option>
                        ))}
                    </select>
                    <IconButton
                      label="移除配对"
                      onClick={() => setPairs((current) => current.filter((_, pairIndex) => pairIndex !== index))}
                      disabled={pairs.length === 1}
                      tone="danger"
                    >
                      <X size={16} />
                    </IconButton>
                  </>
                ) : (
                  <IconButton
                    label="移除账号"
                    onClick={() => setPairs((current) => current.filter((_, pairIndex) => pairIndex !== index))}
                    disabled={pairs.length === 1}
                    tone="danger"
                  >
                    <X size={16} />
                  </IconButton>
                )}
              </div>
            );
          })}
        </div>
        <p className="form-hint">
          {mode === "verify"
            ? "每组只做会话校验，单个账号失败不会影响其他账号。"
            : "每组只做地址绑定，单组失败不会影响其他配对。"}
        </p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button
            tone="primary"
            type="submit"
            disabled={busy || !availableAccounts.length || (mode === "bind" && !availableWallets.length)}
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <Layers3 size={16} />}
            创建 {pairs.length} 组
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const pairedBindStatusLabels: Record<string, string> = {
  selected: "可创建",
  binding_in_progress: "绑定中",
  already_bound: "已绑定",
  account_not_imported: "账号未导入",
  wallet_not_imported: "地址未导入",
  account_not_healthy: "会话未通过",
  duplicate_account_in_file: "账号重复",
  duplicate_wallet_in_file: "地址重复",
  malformed_account_row: "账号格式错误",
  missing_account_row: "缺账号行",
  missing_wallet_row: "缺私钥行",
  account_session_conflict: "会话冲突",
  resource_leased: "资源占用",
  over_limit: "超过上限"
};

function PairedBindDialog({
  onClose,
  onComplete,
  onNotice
}: {
  onClose: () => void;
  onComplete: (message: string) => void;
  onNotice: (message: string, tone?: "success" | "error") => void;
}) {
  const [accountsContent, setAccountsContent] = useState("");
  const [privateKeysContent, setPrivateKeysContent] = useState("");
  const [name, setName] = useState(`文件配对绑定 ${new Date().toLocaleDateString("zh-CN")}`);
  const [limit, setLimit] = useState(10);
  const [dispatchLimit, setDispatchLimit] = useState(10);
  const [includeUnverified, setIncludeUnverified] = useState(false);
  const [preview, setPreview] = useState<PairedBindResult | null>(null);
  const [busy, setBusy] = useState(false);

  const requestBody = (apply: boolean) => ({
    accounts_content: accountsContent,
    private_keys_content: privateKeysContent,
    name,
    limit,
    dispatch_limit: dispatchLimit,
    include_unverified: includeUnverified,
    apply
  });

  const run = async (apply: boolean) => {
    if (!accountsContent.trim() || !privateKeysContent.trim() || !name.trim()) return;
    setBusy(true);
    try {
      const result = await api.pairedBind(requestBody(apply));
      setPreview(result);
      if (apply) {
        onComplete(`已创建 ${result.created_jobs} 个按行配对绑定任务`);
      }
    } catch (error) {
      onNotice(toMessage(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title="文件配对绑定" onClose={onClose} wide>
      <div className="dialog-form">
        <div className="workflow-header">
          <label>
            <span>批次名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
          <label>
            <span>最多创建</span>
            <input
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) || 1)}
              required
            />
          </label>
          <label>
            <span>并发窗口</span>
            <input
              type="number"
              min={1}
              max={32}
              value={dispatchLimit}
              onChange={(event) => setDispatchLimit(Number(event.target.value) || 1)}
              required
            />
          </label>
        </div>
        <div className="two-column-form">
          <label>
            <span>账号 TSV</span>
            <textarea
              rows={10}
              value={accountsContent}
              onChange={(event) => {
                setAccountsContent(event.target.value);
                setPreview(null);
              }}
              placeholder="登录账号	密码	2FA	邮箱	邮箱密码	token	Cookie"
            />
          </label>
          <label>
            <span>私钥（一行一个）</span>
            <textarea
              rows={10}
              value={privateKeysContent}
              onChange={(event) => {
                setPrivateKeysContent(event.target.value);
                setPreview(null);
              }}
              placeholder="0x..."
            />
          </label>
        </div>
        <label className="inline-check">
          <input
            type="checkbox"
            checked={includeUnverified}
            onChange={(event) => {
              setIncludeUnverified(event.target.checked);
              setPreview(null);
            }}
          />
          <span>允许未校验通过的账号进入绑定批次</span>
        </label>
        {preview && (
          <div className="import-preview">
            <div className="preview-summary">
              <span>{preview.total_pairs} 行配对</span>
              <span className="success-text">{preview.selected_pairs} 可创建</span>
              <span>{preview.created_jobs} 已创建</span>
              <span>{preview.apply ? "已应用" : "仅预览"}</span>
            </div>
            <div className="preview-rows compact-preview-grid">
              {Object.entries(preview.counts).map(([status, count]) => (
                <div key={status}>
                  <strong>{pairedBindStatusLabels[status] ?? status}</strong>
                  <span>{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <p className="form-hint">第 N 行账号只会配第 N 行私钥；跳过行不会挤压后续配对。</p>
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button
            onClick={() => void run(false)}
            disabled={busy || !accountsContent.trim() || !privateKeysContent.trim() || !name.trim()}
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            预览
          </Button>
          <Button
            tone="primary"
            onClick={() => void run(true)}
            disabled={
              busy ||
              !accountsContent.trim() ||
              !privateKeysContent.trim() ||
              !name.trim() ||
              (preview !== null && preview.selected_pairs === 0)
            }
          >
            {busy ? <LoaderCircle className="spin" size={16} /> : <Link2 size={16} />}
            创建绑定任务
          </Button>
        </div>
      </div>
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
  const [target, setTarget] = useState(kind === "claim" ? "kredo:claim" : "");
  const [name, setName] = useState(`${kind === "repost" ? "转发" : "领取"} ${new Date().toLocaleDateString("zh-CN")}`);
  const [busy, setBusy] = useState(false);
  const isBatch = bindingIds.length > 1;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.createStageBatch({
        name,
        stage: kind,
        dispatch_limit: 10,
        items: bindingIds.map((bindingId) => ({
          binding_id: bindingId,
          external_target: target
        }))
      });
      onComplete(
        isBatch
          ? `已创建 ${bindingIds.length} 项${kind === "repost" ? "转发" : "领取"}批次`
          : `已创建${kind === "repost" ? "转发" : "领取"}任务`
      );
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
