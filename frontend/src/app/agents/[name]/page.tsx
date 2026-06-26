"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertCircle, ArrowLeft, Play, Upload, KeyRound, Copy, Trash2, RefreshCw, Download, Loader2, CheckCircle2, Cpu } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/ui/status-badge";
import { ModelSelector } from "@/components/ui/model-selector";
import {
  type Agent,
  type AgentVersion,
  type ApiKey,
  type ApiKeyWithSecret,
  type LlmModel,
  activateVersion,
  createKey,
  deleteAgent as apiDeleteAgent,
  downloadVersionArtifact,
  getAgent,
  invokeAgent,
  invokeAgentStream,
  listKeys,
  listModels,
  listVersions,
  revokeKey,
  updateAgentModel,
  uploadVersion,
} from "@/lib/api";

const LANGFUSE_BASE = process.env.NEXT_PUBLIC_LANGFUSE_BASE_URL || "https://cloud.langfuse.com";
const LANGFUSE_PROJECT = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID || "";

type DetailTab = "versions" | "keys" | "tryrun";

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
  const [tryError, setTryError] = useState<string | null>(null);
  const [tryTrace, setTryTrace] = useState<string | null>(null);
  const [tryBusy, setTryBusy] = useState(false);
  const [tryStream, setTryStream] = useState(false);

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
    setTryBusy(true);
    setTryOutput(null);
    setTryError(null);
    setTryTrace(null);

    try {
      if (tryStream) {
        const chunks: string[] = [];
        for await (const evt of invokeAgentStream(name, tryKey, input)) {
          if (evt.event === "error") {
            let msg = evt.data;
            try { msg = JSON.parse(evt.data).message; } catch { /* use raw */ }
            setTryError(String(msg));
            break;
          }
          if (evt.event === "done") {
            try {
              const doneData = JSON.parse(evt.data);
              if (doneData.trace_id) setTryTrace(doneData.trace_id);
            } catch { /* ignore */ }
            break;
          }
          if (evt.event === "output") {
            try {
              const parsed = JSON.parse(evt.data);
              const text = typeof parsed === "string" ? parsed
                : parsed.answer ?? parsed.output ?? JSON.stringify(parsed, null, 2);
              chunks.push(text);
              setTryOutput(chunks.join(""));
            } catch {
              chunks.push(evt.data);
              setTryOutput(chunks.join(""));
            }
          } else {
            try {
              const parsed = JSON.parse(evt.data);
              const text = typeof parsed === "string" ? parsed
                : parsed.token ?? parsed.content ?? parsed.text ?? JSON.stringify(parsed);
              chunks.push(text);
              setTryOutput(chunks.join(""));
            } catch {
              chunks.push(evt.data);
              setTryOutput(chunks.join(""));
            }
          }
        }
      } else {
        const r = await invokeAgent(name, tryKey, input);
        const output = r.output as Record<string, unknown>;
        const text = output?.answer ?? output?.output ?? JSON.stringify(r.output, null, 2);
        setTryOutput(String(text));
        setTryTrace(r.trace_id);
      }
    } catch (e) {
      setTryError((e as Error).message);
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
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              models={models}
              value={agent.model_id ?? null}
              systemDefaultLabel={systemDefaultLabel}
              disabled={busy}
              onChange={onModelChange}
            />
            <StatusBadge status={agent.status} />
            <Button
              variant="outline"
              size="icon"
              onClick={refresh}
              disabled={busy}
              className="group active:scale-90 transition-transform duration-150"
            >
              <RefreshCw
                className={`h-4 w-4 transition-transform duration-500 ease-out ${
                  busy ? "animate-spin" : "group-hover:rotate-180"
                }`}
              />
            </Button>
            <Button variant="ghost" size="icon" onClick={onDeleteAgent}>
              <Trash2 className="h-4 w-4 text-[var(--color-error-icon)]" />
            </Button>
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
                  用 API Key 直接调用 /invoke，支持普通和 SSE 流式两种模式
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="trykey">API Key（Bearer）</Label>
                <Input
                  id="trykey"
                  type="password"
                  value={tryKey}
                  onChange={(e) => setTryKey(e.target.value)}
                  placeholder="miao_sk_..."
                />
              </div>
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

              {tryOutput && (
                <div className="space-y-2">
                  <Label>Output</Label>
                  <div
                    className={`code-block ${
                      tryBusy ? "streaming" : ""
                    }`}
                  >
                    {tryOutput}
                  </div>
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
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
