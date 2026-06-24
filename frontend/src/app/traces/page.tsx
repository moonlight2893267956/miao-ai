"use client";

import { useState } from "react";
import { Activity, ExternalLink } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const LANGFUSE_BASE = process.env.NEXT_PUBLIC_LANGFUSE_BASE_URL || "https://cloud.langfuse.com";
const LANGFUSE_PROJECT = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID || "";

function buildUrl(agentName: string): string {
  const filter = agentName
    ? `tags%3A%20agent%3A${encodeURIComponent(agentName)}`
    : "";
  const base = `${LANGFUSE_BASE}/project/${LANGFUSE_PROJECT}/traces`;
  return filter ? `${base}?filter=${filter}` : base;
}

export default function TracesPage() {
  const [agentName, setAgentName] = useState("");
  const url = buildUrl(agentName);

  return (
    <div className="page-stack">
      <header className="page-header mb-6">
        <h1 className="page-title">Traces</h1>
        <p className="page-subtitle">
          通过 Langfuse 查看每次调用的完整追踪链路
        </p>
      </header>

      <div className="gradient-divider" />

      <section className="detail-panel">
        <div className="detail-panel-header">
          <div>
            <h2 className="detail-panel-title">
              <Activity className="h-5 w-5" />
              Filter & Jump to Langfuse
            </h2>
            <p className="detail-panel-desc">
              按 agent 名称过滤 trace（tag: agent:&lt;name&gt;），在新窗口打开 Langfuse Traces 页
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="agent">Agent Name</Label>
            <Input
              id="agent"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="留空查看所有 agent 的 trace"
            />
          </div>
          <Button onClick={() => window.open(url, "_blank", "noopener,noreferrer")}>
              <ExternalLink className="mr-2 h-4 w-4" />
              Open in Langfuse
          </Button>
        </div>

        <div className="mt-3 text-sm text-[var(--color-text-secondary)]">
          Filter:{" "}
          <code className="code-pill">
            {agentName ? `tag = agent:${agentName}` : "all traces"}
          </code>
          {agentName && <Badge className="ml-2" variant="secondary">active</Badge>}
        </div>
      </section>

      <section className="detail-panel">
        <div className="detail-panel-header">
          <div>
            <h2 className="detail-panel-title">Tips</h2>
          </div>
        </div>
        <ul className="space-y-2 text-sm text-[var(--color-text-secondary)]">
          <li className="flex items-start gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-brand)]" />
            每次 invoke 都上报 trace，tag 包含{" "}
            <code className="code-pill mx-0.5 px-1 py-0.5">
              miao-agent
            </code>{" "}
            +{" "}
            <code className="code-pill mx-0.5 px-1 py-0.5">
              agent:&lt;name&gt;
            </code>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)]" />
            查看每次调用的 input/output/latency/token 用量
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-success-icon)]" />
            单条 trace 的 trace_id 可在 Agent Detail 试运行结果中找到
          </li>
        </ul>
      </section>
    </div>
  );
}
