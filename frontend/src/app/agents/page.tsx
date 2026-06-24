"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Plus, Trash2, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/ui/status-badge";
import { type Agent, createAgent, deleteAgent, listAgents } from "@/lib/api";

const AVATAR_STYLES: Record<
  Agent["status"],
  { bg: string; color: string }
> = {
  running: { bg: "var(--color-success-bg)", color: "var(--color-success-icon)" },
  building: { bg: "var(--color-warning-bg)", color: "var(--color-warning-icon)" },
  crashed: { bg: "var(--color-error-bg)", color: "var(--color-error-icon)" },
  stopped: { bg: "var(--color-surface-tertiary)", color: "var(--color-text-tertiary)" },
  idle: { bg: "var(--color-info-bg)", color: "var(--color-info-icon)" },
};

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [search, setSearch] = useState("");

  async function refresh() {
    setLoading(true);
    setError(null);
    // 保证动画至少展示 400ms，避免快响应时无感知
    const minAnim = new Promise((r) => setTimeout(r, 400));
    try {
      const [agents] = await Promise.all([listAgents(), minAnim]);
      setAgents(agents);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    try {
      await createAgent({ name: newName.trim(), description: newDesc.trim() || undefined });
      setNewName("");
      setNewDesc("");
      setCreating(false);
      await refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  async function onDelete(name: string) {
    if (!confirm(`Delete agent "${name}"? This stops the running process.`)) return;
    try {
      await deleteAgent(name);
      await refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  const filtered = search.trim()
    ? agents.filter(
        (a) =>
          a.name.toLowerCase().includes(search.toLowerCase()) ||
          (a.description ?? "").toLowerCase().includes(search.toLowerCase())
      )
    : agents;

  return (
    <div className="page-stack">
      <header className="page-header mb-6">
          <h1 className="page-title">Agents</h1>
          <p className="page-subtitle">
            管理已部署的 AI agent，支持上传、激活、版本控制
          </p>
      </header>

      <div className="gradient-divider" />

      <div className="action-bar">
        <div className="search-input-shell">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents..."
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            onClick={refresh}
            disabled={loading}
            className="group active:scale-90 transition-transform duration-150"
          >
            <RefreshCw
              className={`h-4 w-4 transition-transform duration-500 ease-out ${
                loading
                  ? "animate-spin"
                  : "group-hover:rotate-180"
              }`}
            />
          </Button>
          <Button onClick={() => setCreating(!creating)}>
            <Plus className="mr-2 h-4 w-4" />
            New Agent
          </Button>
        </div>
      </div>

      {creating && (
        <section className="detail-panel animate-fade-in-up border-[var(--color-brand-muted)]">
          <div className="detail-panel-header">
            <div>
              <h2 className="detail-panel-title">Create agent</h2>
              <p className="detail-panel-desc">name 用小写字母数字加连字符</p>
            </div>
          </div>
          <form onSubmit={onCreate} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="hello-agent"
                  pattern="^[a-z0-9][a-z0-9-]*$"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="desc">Description（可选）</Label>
                <Input
                  id="desc"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="first agent"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <Button type="submit">Create</Button>
              <Button type="button" variant="outline" onClick={() => setCreating(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </section>
      )}

      {error && (
        <div className="alert alert-error">
          {error}
        </div>
      )}

      {loading ? (
        <div className="agents-grid">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-[128px] rounded-[var(--radius-xl)]" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <Bot className="empty-state-icon h-10 w-10" />
          <h3 className="empty-state-title">
            {search ? "No matching agents" : "No agents yet"}
          </h3>
          <p className="empty-state-desc">
            {search ? "Try a different search term" : 'Click "New Agent" to create your first one'}
          </p>
        </div>
      ) : (
        <div className="agents-grid">
          {filtered.map((a) => (
            <div key={a.id} className="agent-card group relative">
              <div className="agent-card-top">
                <Link
                  href={`/agents/${a.name}`}
                  className="flex min-w-0 flex-1 items-start"
                >
                  <div
                    className="agent-card-avatar text-sm"
                    style={{
                      background: AVATAR_STYLES[a.status].bg,
                      color: AVATAR_STYLES[a.status].color,
                    }}
                  >
                    {a.name.slice(0, 2).toUpperCase()}
                  </div>
                  <div className="agent-card-meta">
                    <div className="agent-card-name hover:underline">{a.name}</div>
                    <div className="agent-card-desc">
                      {a.description || "—"}
                    </div>
                  </div>
                </Link>
                <StatusBadge status={a.status} className="shrink-0" />
              </div>
              <div className="agent-card-footer">
                <code className="code-pill">
                  {a.active_version ?? "—"}
                </code>
                <span className="text-[var(--color-text-tertiary)]">active version</span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="absolute bottom-3 right-3 h-7 w-7 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100"
                onClick={(e) => {
                  e.preventDefault();
                  onDelete(a.name);
                }}
              >
                <Trash2 className="h-3.5 w-3.5 text-[var(--color-error-icon)]" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
