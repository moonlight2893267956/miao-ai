// Backend API 客户端封装。
// 所有请求默认走 NEXT_PUBLIC_API_BASE（默认 http://localhost:8000）。
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type Agent = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  status: "stopped" | "building" | "running" | "crashed" | "idle";
  active_version: string | null;
};

export type AgentVersion = {
  id: string;
  version: string;
  artifact_url: string;
  entrypoint: string;
  is_active: boolean;
  status: string;
  created_at: string;
};

export type ApiKey = {
  id: string;
  label: string | null;
  created_at: string;
  revoked_at: string | null;
};

export type ApiKeyWithSecret = ApiKey & { key: string };

async function http<T>(
  path: string,
  init?: RequestInit & { json?: unknown }
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  let body = init?.body;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const r = await fetch(`${API_BASE}${path}`, { ...init, headers, body });
  if (!r.ok) {
    let detail: unknown = null;
    try {
      detail = await r.json();
    } catch {
      detail = await r.text();
    }
    const msg =
      typeof detail === "object" && detail && "detail" in detail
        ? (detail as { detail: unknown }).detail
        : detail;
    throw new Error(`${r.status} ${r.statusText}: ${JSON.stringify(msg)}`);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ===== Agents =====
export const listAgents = () => http<Agent[]>("/api/v1/agents");
export const getAgent = (name: string) => http<Agent>(`/api/v1/agents/${name}`);
export const createAgent = (data: { name: string; description?: string }) =>
  http<Agent>("/api/v1/agents", { method: "POST", json: data });
export const deleteAgent = (name: string) =>
  http<void>(`/api/v1/agents/${name}`, { method: "DELETE" });

// ===== Versions =====
export const listVersions = (name: string) =>
  http<AgentVersion[]>(`/api/v1/agents/${name}/versions`);
export const activateVersion = (name: string, version: string) =>
  http<AgentVersion>(
    `/api/v1/agents/${name}/versions/activate?version=${encodeURIComponent(version)}`,
    { method: "POST" }
  );
export const uploadVersion = (
  name: string,
  version: string,
  file: File,
  entrypoint = "agent:invoke"
) => {
  const fd = new FormData();
  fd.append("version", version);
  fd.append("file", file);
  fd.append("entrypoint", entrypoint);
  return http<AgentVersion>(`/api/v1/agents/${name}/versions`, {
    method: "POST",
    body: fd,
  });
};

// ===== 下载版本 =====
export const downloadVersionArtifact = async (
  name: string,
  version: string
): Promise<void> => {
  const r = await fetch(
    `${API_BASE}/api/v1/agents/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}/download`
  );
  if (!r.ok) {
    let detail: string;
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = await r.text();
    }
    throw new Error(`下载失败: ${detail}`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}-${version}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

// ===== Keys =====
export const listKeys = (name: string) =>
  http<ApiKey[]>(`/api/v1/agents/${name}/keys`);
export const createKey = (name: string, label?: string) =>
  http<ApiKeyWithSecret>(`/api/v1/agents/${name}/keys`, {
    method: "POST",
    json: label ? { label } : {},
  });
export const revokeKey = (name: string, keyId: string) =>
  http<void>(`/api/v1/agents/${name}/keys/${keyId}`, { method: "DELETE" });

// ===== Invoke =====
export const invokeAgent = (
  name: string,
  apiKey: string,
  input: unknown,
  metadata?: { user_id?: string; session_id?: string; tags?: string[] }
) =>
  http<{ output: unknown; trace_id: string | null }>(
    `/api/v1/agents/${name}/invoke`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}` },
      json: { input, metadata: metadata ?? {} },
    }
  );

export type StreamEvent = {
  event: string;
  data: string;
  trace_id?: string;
};

export async function* invokeAgentStream(
  name: string,
  apiKey: string,
  input: unknown,
  metadata?: { user_id?: string; session_id?: string; tags?: string[] }
): AsyncGenerator<StreamEvent> {
  const r = await fetch(`${API_BASE}/api/v1/agents/${name}/invoke/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({ input, metadata: metadata ?? {} }),
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = await r.text();
    }
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }

  const reader = r.body?.getReader();
  if (!reader) throw new Error("no response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let traceId: string | undefined = undefined;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7);
      } else if (line.startsWith("data: ")) {
        const data = line.slice(6);
        if (currentEvent === "done") {
          try {
            const doneData = JSON.parse(data);
            traceId = doneData.trace_id || undefined;
          } catch { /* ignore */ }
          yield { event: "done", data, trace_id: traceId };
        } else if (currentEvent === "error") {
          yield { event: "error", data };
        } else {
          // token events
          yield { event: currentEvent || "token", data, trace_id: traceId };
        }
        currentEvent = "";
      }
    }
  }
}
