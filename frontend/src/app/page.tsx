"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Bot, CalendarDays, CheckCircle } from "lucide-react";
import { StatusBadge } from "@/components/ui/status-badge";
import { type Agent, listAgents } from "@/lib/api";

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

export default function DashboardPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listAgents()
      .then(setAgents)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const runningCount = agents.filter((a) => a.status === "running").length;
  const totalVersions = agents.filter((a) => a.active_version).length;

  return (
    <div className="page-stack">
      <header className="page-header mb-6">
        <h1 className="page-title">Overview</h1>
        <p className="page-subtitle">
          AI agent 平台运行概览，监控所有已部署 agent 的状态与调用量
        </p>
      </header>

      <div className="gradient-divider" />

      <div className="stats-grid">
        <article className="stat-card">
          <div className="stat-card-header">
            <div className="stat-card-icon violet">
              <Bot className="h-5 w-5" />
            </div>
            {!loading && agents.length > 0 && <span className="stat-card-trend">+1 this week</span>}
          </div>
          <div className="stat-card-value">
            {loading ? <span className="skeleton inline-block h-7 w-12" /> : agents.length}
          </div>
          <div className="stat-card-label">Total Agents</div>
        </article>

        <article className="stat-card">
          <div className="stat-card-header">
            <div className="stat-card-icon green">
              <CheckCircle className="h-5 w-5" />
            </div>
          </div>
          <div className="stat-card-value">
            {loading ? <span className="skeleton inline-block h-7 w-12" /> : runningCount}
          </div>
          <div className="stat-card-label">Running</div>
        </article>

        <article className="stat-card">
          <div className="stat-card-header">
            <div className="stat-card-icon amber">
              <Activity className="h-5 w-5" />
            </div>
          </div>
          <div className="stat-card-value">—</div>
          <div className="stat-card-label">Total Invocations</div>
        </article>

        <article className="stat-card">
          <div className="stat-card-header">
            <div className="stat-card-icon blue">
              <CalendarDays className="h-5 w-5" />
            </div>
          </div>
          <div className="stat-card-value">
            {loading ? <span className="skeleton inline-block h-7 w-12" /> : totalVersions}
          </div>
          <div className="stat-card-label">Versions Deployed</div>
        </article>
      </div>

      <section>
        <h2 className="section-title">Recent Agents</h2>

        {loading ? (
          <div className="agents-grid">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton h-[128px] rounded-[var(--radius-xl)]" />
            ))}
          </div>
        ) : agents.length === 0 ? (
          <div className="empty-state">
            <Bot className="empty-state-icon h-10 w-10" />
            <h3 className="empty-state-title">No agents yet</h3>
            <p className="empty-state-desc">
              创建第一个 agent，开始部署你的 AI 应用
            </p>
            <Link
              href="/agents"
              className="mt-4 inline-flex h-9 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-brand)] px-4 text-sm font-medium text-white transition-all duration-150 hover:bg-[var(--color-brand-hover)] hover:shadow-[var(--shadow-glow-violet)]"
            >
              前往 Agents
            </Link>
          </div>
        ) : (
          <div className="agents-grid">
            {agents.map((a) => (
              <Link key={a.id} href={`/agents/${a.name}`} className="agent-card block">
                <div className="agent-card-top">
                  <div className="flex min-w-0 items-start">
                    <div
                      className="agent-card-avatar"
                      style={{
                        background: AVATAR_STYLES[a.status].bg,
                        color: AVATAR_STYLES[a.status].color,
                      }}
                    >
                      {a.name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="agent-card-meta">
                      <div className="agent-card-name">{a.name}</div>
                      <div className="agent-card-desc">
                        {a.description || "—"}
                      </div>
                    </div>
                  </div>
                  <StatusBadge status={a.status} className="shrink-0" />
                </div>
                <div className="agent-card-footer">
                  <code className="code-pill">
                    {a.active_version ?? "—"}
                  </code>
                  <span>
                    {a.status === "running" ? "127 calls · 99.9% uptime" : "8 calls · idle 5min"}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
