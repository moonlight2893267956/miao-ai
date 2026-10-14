"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertCircle, ArrowLeft, Play, Upload, KeyRound, Copy, Trash2, RefreshCw, Download, Loader2, CheckCircle2, Cpu, StopCircle, Sparkles, MessagesSquare, RotateCcw } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/ui/status-badge";
import { ModelSelector } from "@/components/ui/model-selector";
import { ContractForm } from "@/components/contract/contract-form";
import { ContractOutput, hasDisplayConfig } from "@/components/contract/contract-output";
import {
  type Agent,
  type AgentVersion,
  type ApiKey,
  type ApiKeyWithSecret,
  type LlmModel,
  activateAgent as apiActivateAgent,
  activateVersion,
  createConversation,
  createKey,
  deleteAgent as apiDeleteAgent,
  deleteConversation,
  downloadVersionArtifact,
  getAgent,
  invokeAgent,
  invokeAgentStream,
  listKeys,
  listModels,
  listVersions,
  revokeKey,
  stopAgent as apiStopAgent,
  updateAgentModel,
  uploadVersion,
} from "@/lib/api";

const LANGFUSE_BASE = process.env.NEXT_PUBLIC_LANGFUSE_BASE_URL || "https://cloud.langfuse.com";
const LANGFUSE_PROJECT = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID || "";

type DetailTab = "versions" | "keys" | "tryrun";

/**
 * Try Run 的一轮（仅在「多轮会话」开启时使用）。
 * 非会话模式下输出区仍是单块 Output，行为与本功能上线前完全一致。
 */
type TryTurn = {
  /** 本地 id：流式过程中靠它定位要更新的那一轮 */
  key: string;
  /** 平台回传的会话内轮次号（从 1 开始）；流式下由 conversation 事件给出 */
  turn?: number;
  input: string;
  output: string | null;
  rawOutput: unknown;
  status: "running" | "succeeded" | "failed";
  traceId: string | null;
  /** 本轮平台实际注入给 agent 的轮次号（空 = 没有注入历史） */
  injectedTurns: number[];
  historyTruncated: boolean;
  error?: string | null;
};

function tryTurnStatusText(status: TryTurn["status"]): string {
  if (status === "running") return "进行中";
  return status === "succeeded" ? "成功" : "失败";
}

function shortConversationId(cid: string): string {
  return cid.length > 20 ? `${cid.slice(0, 14)}…${cid.slice(-4)}` : cid;
}

/** 把一条 SSE 数据转成要追加到正文的文本（与改造前的展示逻辑保持一致） */
function streamEventText(event: string, data: string): string {
  try {
    const parsed = JSON.parse(data);
    if (typeof parsed === "string") return parsed;
    if (event === "output") {
      return parsed.answer ?? parsed.output ?? JSON.stringify(parsed, null, 2);
    }
    return parsed.token ?? parsed.content ?? parsed.text ?? JSON.stringify(parsed);
  } catch {
    return data;
  }
}

export default function AgentDetailPage() {
  const params = useParams<{ name: string }>();
  const router = useRouter();
  const name = params.name;

  const [agent, setAgent] = useState<Agent | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [models, setModels] = useState<LlmModel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailTab>("versions");

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    // 保证动画至少展示 400ms，避免快响应时无感知
    const minAnim = new Promise((r) => setTimeout(r, 400));
    try {
      const [a, v, k, m] = await Promise.all([
        getAgent(name),
        listVersions(name),
        listKeys(name),
        listModels(),
        minAnim,
      ]);
      setAgent(a);
      setVersions(v);
      setKeys(k);
      setModels(m);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [name]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ===== 上传版本 =====
  const [showUpload, setShowUpload] = useState(false);
  const [newVer, setNewVer] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadFile || !newVer.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await uploadVersion(name, newVer.trim(), uploadFile);
      setNewVer("");
      setUploadFile(null);
      setShowUpload(false);
      await refresh();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onActivate(version: string) {
    if (!confirm(`Activate version "${version}"? 会构建 venv（首次）和启动子进程。`)) return;
    setBusy(true);
    setActionError(null);
    try {
      await activateVersion(name, version);
      await refresh();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onDownloadVersion(v: AgentVersion) {
    try {
      await downloadVersionArtifact(name, v.version);
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  async function onDeleteAgent() {
    if (!confirm(`Delete agent "${name}"? 会先停止运行中的进程。`)) return;
    try {
      await apiDeleteAgent(name);
      router.push("/agents");
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  async function onStopAgent() {
    if (
      !confirm(
        `停止 agent "${name}"？容器/进程会被杀掉，但 DB 定义（agent/versions/keys）保留。下次 invoke 时会自动唤醒。`
      )
    )
      return;
    setBusy(true);
    setActionError(null);
    try {
      const updated = await apiStopAgent(name);
      setAgent(updated);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onActivateAgent() {
    setBusy(true);
    setActionError(null);
    try {
      const updated = await apiActivateAgent(name);
      setAgent(updated);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onModelChange(modelId: string) {
    setBusy(true);
    setActionError(null);
    try {
      const updated = await updateAgentModel(name, modelId || null);
      setAgent(updated);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // ===== API Key 管理 =====
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [newKeySecret, setNewKeySecret] = useState<string | null>(null);
  const [revokingKeyIds, setRevokingKeyIds] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  async function onCreateKey() {
    setBusy(true);
    setActionError(null);
    try {
      const k: ApiKeyWithSecret = await createKey(name, newKeyLabel.trim() || undefined);
      setNewKeySecret(k.key);
      setNewKeyLabel("");
      await refresh();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onRevokeKey(id: string, label?: string) {
    if (!confirm(`Revoke this key${label ? ` (${label})` : ""}? 调用方将立即无法用这个 key。`)) return;
    setActionError(null);
    setRevokingKeyIds((s) => new Set(s).add(id));
    try {
      await revokeKey(name, id);
      await refresh();
      setToast({ kind: "success", message: `Key ${label || ""} revoked` });
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast({ kind: "error", message: (e as Error).message });
      setTimeout(() => setToast(null), 3500);
    } finally {
      setRevokingKeyIds((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }
  }

  // ===== 试运行 =====
  const [tryInput, setTryInput] = useState('{"question": "一句话介绍你自己"}');
  const [tryKey, setTryKey] = useState<string>("");
  const [tryOutput, setTryOutput] = useState<string | null>(null);
  /** 结构化输出的原料（原始 output 对象）；无契约时用不到 */
  const [tryRawOutput, setTryRawOutput] = useState<unknown>(undefined);
  const [tryError, setTryError] = useState<string | null>(null);
  const [tryTrace, setTryTrace] = useState<string | null>(null);
  const [tryBusy, setTryBusy] = useState(false);
  const [tryStream, setTryStream] = useState(false);
  // 有契约时默认「表单模式」；无契约的 agent 只有 JSON 模式，界面与历史版本一致
  const [tryMode, setTryMode] = useState<"form" | "json">("form");
  const [tryExample, setTryExample] = useState("-1");

  // ===== 多轮会话（Try Run）=====
  const [tryTurns, setTryTurns] = useState<TryTurn[]>([]);
  const [tryConversationOn, setTryConversationOn] = useState(false);
  /** 当前会话 id；为 null 表示下一轮 Run 时惰性新建 */
  const [tryConversationId, setTryConversationId] = useState<string | null>(null);
  const [tryConvNotice, setTryConvNotice] = useState<string | null>(null);

  /** 生效契约：取当前激活版本的声明。没有契约（存量 agent）→ 全部走旧的 JSON 模式 */
  const activeContract = versions.find((v) => v.is_active)?.contract ?? null;
  const examples = activeContract?.input?.examples ?? [];
  const inputSchema = activeContract?.input?.schema;

  /**
   * 该 agent 是否声明了多轮能力。
   * 未声明时平台只记账、不注入历史，因此开关置灰并给出提示，避免「以为 agent 看得见上下文」。
   */
  const conversationCapable = Boolean(activeContract?.conversation?.enabled);
  const conversationActive = tryConversationOn && conversationCapable;

  /** 表单与 JSON 共用一个数据源：都是这段 JSON 文本 */
  const formValue = useMemo<Record<string, unknown>>(() => {
    try {
      const parsed: unknown = JSON.parse(tryInput);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }, [tryInput]);

  // 首次拿到契约时用第一个示例预填入参 —— 用户改几个字就能跑
  const seededExample = useRef(false);
  useEffect(() => {
    if (seededExample.current) return;
    const first = examples[0];
    if (!first) return;
    seededExample.current = true;
    setTryInput(JSON.stringify(first.value, null, 2));
    setTryExample("0");
  }, [examples]);

  function applyExample(index: number) {
    const example = examples[index];
    if (!example) return;
    setTryInput(JSON.stringify(example.value, null, 2));
    setTryExample(String(index));
    setTryError(null);
  }

  function onToggleConversation(next: boolean) {
    setTryConversationOn(next);
    setTryError(null);
    if (next) {
      setTryConvNotice(null);
      return;
    }
    // 关闭 = 停用当前会话（不删除服务端会话，避免误删想留的历史）
    setTryConversationId(null);
    setTryTurns([]);
    setTryConvNotice("已关闭多轮：下一轮不再携带会话");
  }

  function onApiKeyChange(next: string) {
    setTryKey(next);
    if (tryConversationId) {
      // 会话归属创建它的 API Key，换 key 必然 403，直接重置
      setTryConversationId(null);
      setTryTurns([]);
      setTryConvNotice("已切换 API Key，会话已重置");
    }
  }

  async function onResetConversation() {
    const cid = tryConversationId;
    setTryConversationId(null);
    setTryTurns([]);
    setTryError(null);
    setTryConvNotice("已重置，下一轮会新建会话");
    if (cid && tryKey) {
      try {
        await deleteConversation(name, tryKey, cid);
      } catch {
        // 删除失败不影响继续用：会话有 TTL，过期后自然不可用
      }
    }
  }

  /** 会话类失败的友好提示 + 本地善后（坏掉的 conversation_id 不能留到下一轮） */
  function conversationFailureHint(message: string): string | null {
    if (message.startsWith("410")) {
      setTryConversationId(null);
      setTryConvNotice("会话已过期，已自动重置：下一轮会新建会话");
      return "会话已过期（410）：已自动重置，下一轮会新建会话";
    }
    if (message.startsWith("409")) {
      return "上一轮还在跑（409）：同一会话同时只能有一轮，稍等再试";
    }
    if (message.startsWith("403")) {
      setTryConversationId(null);
      setTryTurns([]);
      setTryConvNotice("会话不属于当前 API Key，已自动重置");
      return "会话归属不符（403）：已重置，下一轮会新建会话";
    }
    if (message.startsWith("404")) {
      setTryConversationId(null);
      setTryConvNotice("会话不存在，已自动重置");
      return "会话不存在（404）：已重置，下一轮会新建会话";
    }
    if (message.startsWith("429") && message.includes("max turns")) {
      return "会话轮次已达上限（429）：点「重置」新建一个会话";
    }
    return null;
  }

  async function onTryRun() {
    if (!tryKey) {
      setTryError("需要 API Key（颁发一个或粘贴现有的）");
      return;
    }
    let input: unknown;
    try {
      input = JSON.parse(tryInput);
    } catch {
      setTryError("Input 不是合法 JSON");
      return;
    }
    const useConversation = conversationActive;
    setTryBusy(true);
    setTryError(null);
    setTryConvNotice(null);
    if (!useConversation) {
      // 非会话模式：行为与改造前一致（单块 Output，每次覆盖）
      setTryOutput(null);
      setTryRawOutput(undefined);
      setTryTrace(null);
    }

    // 会话模式：惰性新建会话（第一次 Run 才建，避免点开开关就产生空会话）
    let conversationId = useConversation ? tryConversationId : null;
    let turnKey: string | null = null;
    if (useConversation) {
      if (!conversationId) {
        try {
          const created = await createConversation(name, tryKey);
          conversationId = created.conversation_id;
          setTryConversationId(conversationId);
        } catch (e) {
          setTryError(`新建会话失败：${(e as Error).message}`);
          setTryBusy(false);
          return;
        }
      }
      turnKey = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
      const key = turnKey;
      setTryTurns((prev) => [
        ...prev,
        {
          key,
          input: tryInput,
          output: null,
          rawOutput: undefined,
          status: "running",
          traceId: null,
          injectedTurns: [],
          historyTruncated: false,
        },
      ]);
    }
    const currentTurnKey = turnKey;
    const patchTurn = (patch: Partial<TryTurn>) => {
      if (!currentTurnKey) return;
      setTryTurns((prev) =>
        prev.map((t) => (t.key === currentTurnKey ? { ...t, ...patch } : t))
      );
    };

    try {
      if (tryStream) {
        const chunks: string[] = [];
        let traceId: string | null = null;
        let raw: unknown = undefined;
        let meta: { turn?: number; injected_turns?: number[]; history_truncated?: boolean } = {};
        for await (const evt of invokeAgentStream(
          name,
          tryKey,
          input,
          conversationId ? { conversation_id: conversationId } : undefined
        )) {
          if (evt.event === "conversation") {
            // 平台补发的首条事件：轮次号 + 注入明细
            try {
              const parsed = JSON.parse(evt.data);
              meta = parsed;
              if (parsed.conversation_id) setTryConversationId(parsed.conversation_id);
            } catch { /* ignore */ }
            patchTurn({
              turn: meta.turn,
              injectedTurns: meta.injected_turns ?? [],
              historyTruncated: Boolean(meta.history_truncated),
            });
            continue;
          }
          if (evt.event === "error") {
            let msg = evt.data;
            try { msg = JSON.parse(evt.data).message; } catch { /* use raw */ }
            patchTurn({ status: "failed", error: String(msg) });
            setTryError(String(msg));
            break;
          }
          if (evt.event === "done") {
            let doneData: Record<string, unknown> | null = null;
            try {
              doneData = JSON.parse(evt.data);
            } catch { /* ignore */ }
            if (doneData?.trace_id) traceId = String(doneData.trace_id);
            if (doneData && "output" in doneData) {
              // 逐 token 流的 agent 把结构化结果随 done 下发（见 miao_runner 的流式约定）
              raw = doneData.output;
            } else {
              // 兜底：agent 把整个 output JSON 逐段吐出来时，拼完能解析就用于结构化展示
              try {
                raw = JSON.parse(chunks.join(""));
              } catch { /* 拼起来不是完整 JSON 就保持文本展示 */ }
            }
            break;
          }
          chunks.push(streamEventText(evt.event, evt.data));
          if (currentTurnKey) {
            patchTurn({ output: chunks.join("") });
          } else {
            setTryOutput(chunks.join(""));
          }
        }
        if (currentTurnKey) {
          patchTurn({
            status: "succeeded",
            output: chunks.join("") || null,
            rawOutput: raw,
            traceId,
            turn: meta.turn,
            injectedTurns: meta.injected_turns ?? [],
            historyTruncated: Boolean(meta.history_truncated),
          });
        } else {
          setTryTrace(traceId);
          setTryRawOutput(raw);
          setTryOutput(chunks.join("") || null);
        }
      } else {
        const r = await invokeAgent(
          name,
          tryKey,
          input,
          conversationId ? { conversation_id: conversationId } : undefined
        );
        const output = r.output as Record<string, unknown>;
        const text = output?.answer ?? output?.output ?? JSON.stringify(r.output, null, 2);
        if (currentTurnKey) {
          patchTurn({
            status: "succeeded",
            output: String(text),
            rawOutput: r.output,
            traceId: r.trace_id,
            turn: r.turn,
            injectedTurns: r.injected_turns ?? [],
            historyTruncated: Boolean(r.history_truncated),
          });
        } else {
          setTryOutput(String(text));
          setTryRawOutput(r.output);
          setTryTrace(r.trace_id);
        }
      }
    } catch (e) {
      const message = (e as Error).message;
      const hint = useConversation ? conversationFailureHint(message) : null;
      if (currentTurnKey) {
        patchTurn({ status: "failed", error: hint ?? message });
      }
      setTryError(hint ?? message);
    } finally {
      setTryBusy(false);
    }
  }

  if (error) {
    return <div className="alert alert-error">{error}</div>;
  }
  if (!agent) {
    return <p className="text-[var(--color-text-secondary)]">Loading...</p>;
  }

  const tabs: { id: DetailTab; label: string; count?: number }[] = [
    { id: "versions", label: "Versions", count: versions.length },
    { id: "keys", label: "API Keys", count: keys.length },
    { id: "tryrun", label: "Try Run" },
  ];
  const defaultModel = models.find((model) => model.is_default) ?? null;
  const systemDefaultLabel = defaultModel
    ? `系统默认 (${defaultModel.provider_name ?? "Provider"} / ${defaultModel.name})`
    : "系统默认 (.env DASHSCOPE_*)";

  return (
    <div className="page-stack">
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={`pointer-events-none fixed right-6 top-20 z-50 flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium shadow-lg animate-fade-in-up ${
            toast.kind === "success"
              ? "border-[var(--color-success-icon)]/30 bg-[var(--color-success-bg)] text-[var(--color-success-text)]"
              : "border-[var(--color-error-icon)]/30 bg-[var(--color-error-bg)] text-[var(--color-error-text)]"
          }`}
        >
          {toast.kind === "success" ? (
            <CheckCircle2 className="h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0" />
          )}
          {toast.message}
        </div>
      )}
      <header className="mb-6 space-y-3">
        <nav className="breadcrumb">
          <Link href="/agents" className="inline-flex items-center gap-1 transition-colors hover:text-[var(--color-text-primary)]">
            <ArrowLeft className="h-3.5 w-3.5" />
            Agents
          </Link>
          <span>/</span>
          <span className="text-[var(--color-text-primary)]">{name}</span>
        </nav>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="page-header">
            <h1 className="page-title">{name}</h1>
            {agent.description && (
              <p className="page-subtitle">{agent.description}</p>
            )}
            <p className="font-mono text-[0.65rem] text-[var(--color-text-tertiary)]">
              ID: {agent.id}
            </p>
          </div>
          <div
            className="flex items-center gap-1 self-start rounded-lg bg-[var(--color-surface-secondary)]/50 p-1 sm:self-auto"
            role="toolbar"
            aria-label="Agent runtime controls"
          >
            {/* Segment 1: Model selector (config) */}
            <ModelSelector
              models={models}
              value={agent.model_id ?? null}
              systemDefaultLabel={systemDefaultLabel}
              disabled={busy}
              onChange={onModelChange}
            />

            {/* Divider */}
            <div
              className="mx-1 h-6 w-px self-stretch bg-[var(--color-border)]"
              aria-hidden="true"
            />

            {/* Segment 2: Status + runtime toggle (Activate / Stop) */}
            <div className="flex items-center gap-1.5 px-1">
              <StatusBadge status={agent.status} />
              {agent.status === "stopped" ? (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onActivateAgent}
                  disabled={busy}
                  title="Activate (warm up the agent container/process)"
                >
                  <Play className="h-4 w-4 text-[var(--color-success-icon)]" />
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onStopAgent}
                  disabled={busy}
                  title="Stop (DB definition kept, auto-wake on next invoke)"
                >
                  <StopCircle className="h-4 w-4 text-[var(--color-warning-icon)]" />
                </Button>
              )}
            </div>

            {/* Divider */}
            <div
              className="mx-1 h-6 w-px self-stretch bg-[var(--color-border)]"
              aria-hidden="true"
            />

            {/* Segment 3: Page actions (Refresh + Delete) */}
            <div className="flex items-center gap-0.5">
              <Button
                variant="ghost"
                size="icon"
                onClick={refresh}
                disabled={busy}
                className="group active:scale-90 transition-transform duration-150"
                title="Refresh"
              >
                <RefreshCw
                  className={`h-4 w-4 transition-transform duration-500 ease-out ${
                    busy ? "animate-spin" : "group-hover:rotate-180"
                  }`}
                />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={onDeleteAgent}
                title="Delete agent (cascades to versions & keys)"
              >
                <Trash2 className="h-4 w-4 text-[var(--color-error-icon)]" />
              </Button>
            </div>
          </div>
        </div>

        {actionError && (
          <div className="alert alert-error mt-3">
            <AlertCircle className="h-4 w-4 shrink-0" /> {actionError}
          </div>
        )}
      </header>

      <div className="tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`tab ${activeTab === tab.id ? "active" : ""}`}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="tab-badge">
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {activeTab === "versions" && (
        <div className="animate-fade-in-up">
          <section className="detail-panel">
            <div className="detail-panel-header">
              <div>
                <h2 className="detail-panel-title">
                  <Upload className="h-[18px] w-[18px]" />
                  Version History
                </h2>
                <p className="detail-panel-desc">上传 zip 包部署新版本，激活后自动构建 venv 并启动子进程</p>
              </div>
              <Button size="sm" onClick={() => setShowUpload(!showUpload)}>
                <Upload className="mr-2 h-4 w-4" />
                Upload
              </Button>
            </div>

            <div className="space-y-3">
              {showUpload && (
                <form onSubmit={onUpload} className="space-y-4 rounded-[var(--radius-lg)] border border-[var(--color-border-default)] bg-[var(--color-surface-secondary)] p-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="ver">Version</Label>
                      <Input
                        id="ver"
                        value={newVer}
                        onChange={(e) => setNewVer(e.target.value)}
                        placeholder="v1"
                        required
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="zip">Zip（必须含 agent.py）</Label>
                      <Input
                        id="zip"
                        type="file"
                        accept=".zip"
                        onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                        required
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button type="submit" disabled={busy}>
                      {busy ? "Uploading…" : "Upload"}
                    </Button>
                    <Button type="button" variant="outline" onClick={() => setShowUpload(false)}>
                      Cancel
                    </Button>
                  </div>
                </form>
              )}

              {versions.length === 0 ? (
                <p className="py-4 text-center text-sm text-[var(--color-text-secondary)]">还没有版本</p>
              ) : (
                <div className="space-y-2">
                  {versions.map((v) => (
                    <div
                      key={v.id}
                      className="version-item"
                    >
                      <div className="version-info">
                          {v.is_active && (
                            <StatusBadge status={agent.status} className="scale-75 origin-left" />
                          )}
                          <span className="version-tag">{v.version}</span>
                          {!v.is_active && (
                            <span className="code-pill uppercase tracking-[0.06em]">
                              {v.status}
                            </span>
                          )}
                        <span className="version-meta">
                          entrypoint: {v.entrypoint} · {new Date(v.created_at).toLocaleString()}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onDownloadVersion(v)}
                          title={`下载 ${v.version}`}
                        >
                          <Download className="h-4 w-4" />
                        </Button>
                        {!v.is_active && v.status !== "running" && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onActivate(v.version)}
                            disabled={busy}
                          >
                            Activate
                          </Button>
                        )}
                        {v.status === "running" && (
                          <span className="text-xs text-[var(--color-text-secondary)]">Active</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      )}

      {activeTab === "keys" && (
        <div className="animate-fade-in-up">
          <section className="detail-panel">
            <div className="detail-panel-header">
              <div>
                <h2 className="detail-panel-title">
                  <KeyRound className="h-[18px] w-[18px]" />
                  API Keys
                </h2>
                <p className="detail-panel-desc">调用 /invoke 时使用 Bearer 鉴权，key 原文仅创建时展示一次</p>
              </div>
            </div>

            <div className="space-y-4">
              {newKeySecret && (
                <div className="key-reveal">
                  <p className="text-sm font-semibold text-[var(--color-brand)]">
                    新 Key - 仅显示一次，请复制保存
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <code className="flex-1 break-all rounded-[var(--radius-sm)] bg-[var(--color-surface-primary)] px-3 py-2 font-mono text-xs">
                      {newKeySecret}
                    </code>
                    <Button
                      size="icon"
                      variant="outline"
                      onClick={() => navigator.clipboard.writeText(newKeySecret)}
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="mt-2"
                    onClick={() => setNewKeySecret(null)}
                  >
                    知道了
                  </Button>
                </div>
              )}

              <div className="flex gap-2">
                <Input
                  value={newKeyLabel}
                  onChange={(e) => setNewKeyLabel(e.target.value)}
                  placeholder="label（可选）"
                  className="flex-1"
                />
                <Button onClick={onCreateKey} disabled={busy}>
                  Create Key
                </Button>
              </div>

              {keys.length === 0 ? (
                <p className="py-4 text-center text-sm text-[var(--color-text-secondary)]">还没有 key</p>
              ) : (
                <div className="space-y-2">
                  {keys.map((k) => {
                    const isRevoking = revokingKeyIds.has(k.id);
                    return (
                    <div
                      key={k.id}
                      className={`key-item text-sm transition-all duration-300 ${
                        isRevoking ? "pointer-events-none scale-[0.97] opacity-40" : ""
                      }`}
                    >
                      <div className="version-info">
                        <span className="version-tag !font-sans">{k.label || "(no label)"}</span>
                        <span className="version-meta">
                          {new Date(k.created_at).toLocaleString()}
                        </span>
                        {k.revoked_at && (
                          <span className="ml-2 text-xs text-[var(--color-error-text)]">revoked</span>
                        )}
                      </div>
                      {!k.revoked_at && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onRevokeKey(k.id, k.label ?? undefined)}
                          disabled={revokingKeyIds.has(k.id)}
                          className="group active:scale-90 transition-transform duration-150"
                        >
                          {revokingKeyIds.has(k.id) ? (
                            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-error-icon)]" />
                          ) : (
                            <Trash2 className="h-4 w-4 text-[var(--color-error-icon)] transition-transform group-hover:scale-110" />
                          )}
                        </Button>
                      )}
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        </div>
      )}

      {activeTab === "tryrun" && (
        <div className="animate-fade-in-up">
          <section className="detail-panel">
            <div className="detail-panel-header">
              <div>
                <h2 className="detail-panel-title">
                  <Play className="h-[18px] w-[18px]" />
                  Try Run
                </h2>
                <p className="detail-panel-desc">
                  {activeContract
                    ? `按契约表单填字段，或切到 JSON 手写；两种模式内容互通${
                        conversationCapable
                          ? "。勾选「多轮会话」可在同一会话内连续追问，平台会把历史注入 agent"
                          : ""
                      }`
                    : "用 API Key 直接调用 /invoke，支持普通和 SSE 流式两种模式"}
                </p>
              </div>
            </div>

            <div className="space-y-4">
              {activeContract && (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div
                    role="tablist"
                    aria-label="入参编辑模式"
                    className="inline-flex rounded-[var(--radius-md)] border border-[var(--color-border-default)] bg-[var(--color-surface-tertiary)] p-0.5"
                  >
                    {(["form", "json"] as const).map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        role="tab"
                        aria-selected={tryMode === mode}
                        onClick={() => setTryMode(mode)}
                        className={`cursor-pointer rounded-[var(--radius-sm)] px-3 py-1 text-xs font-medium transition-colors ${
                          tryMode === mode
                            ? "bg-[var(--color-surface-primary)] text-[var(--color-brand)] shadow-[var(--shadow-xs)]"
                            : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                        }`}
                      >
                        {mode === "form" ? "表单" : "JSON"}
                      </button>
                    ))}
                  </div>
                  {examples.length > 0 &&
                    (examples.length === 1 ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => applyExample(0)}
                      >
                        <Sparkles className="h-3.5 w-3.5" />
                        填充示例
                      </Button>
                    ) : (
                      <select
                        className="select-control w-auto"
                        value={tryExample}
                        aria-label="填充示例"
                        onChange={(e) => applyExample(Number(e.target.value))}
                      >
                        <option value="-1">填充示例…</option>
                        {examples.map((example, index) => (
                          <option key={`${example.name ?? "example"}-${index}`} value={index}>
                            {example.name ?? `示例 ${index + 1}`}
                          </option>
                        ))}
                      </select>
                    ))}
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="trykey">API Key（Bearer）</Label>
                <Input
                  id="trykey"
                  type="password"
                  value={tryKey}
                  onChange={(e) => onApiKeyChange(e.target.value)}
                  placeholder="miao_sk_..."
                />
              </div>

              {/* 多轮会话：开关 + 会话状态。未声明能力的 agent 置灰，避免误以为能带上下文 */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <label
                  className={`toggle-label ${conversationCapable ? "" : "cursor-not-allowed opacity-60"}`}
                  title={
                    conversationCapable
                      ? "同一次会话内连续调用，平台会把历史注入 agent"
                      : "该 agent 未声明多轮能力：平台只记账，不会把历史注入 agent"
                  }
                >
                  <input
                    type="checkbox"
                    checked={conversationActive}
                    disabled={!conversationCapable}
                    onChange={(e) => onToggleConversation(e.target.checked)}
                  />
                  <MessagesSquare className="h-4 w-4" />
                  多轮会话
                </label>
                {conversationActive ? (
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
                    <span className="font-mono">
                      {tryConversationId
                        ? shortConversationId(tryConversationId)
                        : "尚未创建（Run 时自动新建）"}
                    </span>
                    {tryTurns.length > 0 && <span>已 {tryTurns.length} 轮</span>}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={onResetConversation}
                      disabled={tryBusy}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      重置
                    </Button>
                  </span>
                ) : (
                  !conversationCapable && (
                    <span className="text-xs text-[var(--color-text-secondary)]">
                      该 agent 未声明多轮能力（agent.yaml 的 conversation.enabled: true）
                    </span>
                  )
                )}
              </div>

              {tryConvNotice && (
                <div className="rounded-[var(--radius-md)] bg-[var(--color-info-bg)] px-3 py-2 text-xs text-[var(--color-info-text)]">
                  {tryConvNotice}
                </div>
              )}
              {activeContract && tryMode === "form" && inputSchema ? (
                <div className="space-y-1.5">
                  <Label>Input（按契约填写）</Label>
                  <ContractForm
                    schema={inputSchema}
                    value={formValue}
                    onChange={(next) => setTryInput(JSON.stringify(next, null, 2))}
                    disabled={tryBusy}
                  />
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="tryinput">Input（JSON）</Label>
                  <Textarea
                    id="tryinput"
                    value={tryInput}
                    onChange={(e) => setTryInput(e.target.value)}
                    rows={4}
                    className="font-mono text-xs"
                  />
                </div>
              )}
              <div className="flex items-center gap-4">
                <Button onClick={onTryRun} disabled={tryBusy}>
                  <Play className="mr-2 h-4 w-4" />
                  {tryBusy ? "Running…" : "Run"}
                </Button>
                <label className="toggle-label">
                  <input
                    type="checkbox"
                    checked={tryStream}
                    onChange={(e) => setTryStream(e.target.checked)}
                  />
                  流式输出 (SSE)
                </label>
              </div>

              {tryError && (
                <div className="alert alert-error">
                  {tryError}
                </div>
              )}

              {/* 非会话模式：单块 Output（与本功能上线前完全一致） */}
              {!conversationActive && tryOutput && (
                <div className="space-y-2">
                  <Label>Output</Label>
                  {activeContract && tryRawOutput !== undefined && hasDisplayConfig(activeContract) ? (
                    <ContractOutput output={tryRawOutput} contract={activeContract} />
                  ) : (
                    <div
                      className={`code-block ${
                        tryBusy ? "streaming" : ""
                      }`}
                    >
                      {tryOutput}
                    </div>
                  )}
                  {tryTrace && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
                      <span>
                        trace_id:{" "}
                        <a
                          href={`${LANGFUSE_BASE}/project/${LANGFUSE_PROJECT}/traces/${tryTrace}`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-[var(--color-brand)] underline"
                        >
                          {tryTrace}
                        </a>
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* 会话模式：按轮累积，最新在上 */}
              {conversationActive && tryTurns.length > 0 && (
                <div className="space-y-2">
                  <Label>轮次（最新在上）</Label>
                  {[...tryTurns].reverse().map((turn) => (
                    <div
                      key={turn.key}
                      className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-border-default)] bg-[var(--color-surface-primary)] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
                        <span className="font-medium text-[var(--color-text-primary)]">
                          {turn.turn ? `第 ${turn.turn} 轮` : "本轮"}
                        </span>
                        <span
                          className={
                            turn.status === "failed"
                              ? "text-[var(--color-error-text)]"
                              : turn.status === "running"
                                ? "text-[var(--color-warning-text)]"
                                : "text-[var(--color-success-text)]"
                          }
                        >
                          {tryTurnStatusText(turn.status)}
                        </span>
                        <span
                          title={
                            turn.injectedTurns.length > 0
                              ? `本轮 agent 实际看到的历史轮次：${turn.injectedTurns.join(", ")}`
                              : "本轮没有注入历史（首轮，或该 agent 未声明多轮能力）"
                          }
                        >
                          注入历史：
                          {turn.injectedTurns.length > 0
                            ? turn.injectedTurns.map((i) => `#${i}`).join(" ")
                            : "无"}
                        </span>
                        {turn.historyTruncated && (
                          <span className="text-[var(--color-warning-text)]">已裁剪</span>
                        )}
                        {turn.traceId && (
                          <a
                            href={`${LANGFUSE_BASE}/project/${LANGFUSE_PROJECT}/traces/${turn.traceId}`}
                            target="_blank"
                            rel="noreferrer"
                            className="font-mono text-[var(--color-brand)] underline"
                          >
                            trace
                          </a>
                        )}
                      </div>
                      <details>
                        <summary className="cursor-pointer text-xs text-[var(--color-text-secondary)]">
                          本轮入参
                        </summary>
                        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-[var(--color-text-secondary)]">
                          {turn.input}
                        </pre>
                      </details>
                      {turn.error ? (
                        <div className="alert alert-error text-xs">{turn.error}</div>
                      ) : activeContract && turn.rawOutput !== undefined && hasDisplayConfig(activeContract) ? (
                        <ContractOutput output={turn.rawOutput} contract={activeContract} />
                      ) : (
                        <div
                          className={`code-block ${
                            turn.status === "running" ? "streaming" : ""
                          }`}
                        >
                          {turn.output ?? ""}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
