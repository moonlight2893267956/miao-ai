"use client";

/**
 * 结构化输出展示（Try Run）。
 *
 * 契约给出 `output.display.primary`（主字段）与 `secondary` 时，把主结果以大字号
 * 突出展示、其余字段逐行列出，原始 JSON 折叠在下方。契约没给展示配置时本组件不渲染，
 * 由调用方回落到原有的纯文本输出 —— 无契约的 agent 表现与历史版本完全一致。
 */
import type { AgentContract } from "@/lib/api";

export function hasDisplayConfig(contract: AgentContract | null | undefined): boolean {
  const display = contract?.output?.display;
  return Boolean(display?.primary) || (display?.secondary?.length ?? 0) > 0;
}

export function ContractOutput({
  output,
  contract,
}: {
  output: unknown;
  contract: AgentContract;
}) {
  const display = contract.output?.display;
  const primaryKey = display?.primary;
  const secondary = display?.secondary ?? [];
  const record = asRecord(output);
  const primaryValue = primaryKey && record ? record[primaryKey] : undefined;

  return (
    <div className="space-y-3">
      {primaryValue !== undefined && primaryKey && (
        <div className="rounded-[var(--radius-md)] border border-[var(--color-border-default)] bg-[var(--color-brand-subtle)] p-4">
          <p className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[var(--tracking-wider)] text-[var(--color-text-tertiary)]">
            {primaryKey}
          </p>
          <div className="whitespace-pre-wrap break-words text-[15px] leading-[var(--leading-relaxed)] text-[var(--color-text-primary)]">
            {formatValue(primaryValue)}
          </div>
        </div>
      )}

      {secondary.length > 0 && (
        <dl className="space-y-1.5">
          {secondary.map((key) => (
            <div key={key} className="flex gap-3 text-xs">
              <dt className="w-32 shrink-0 font-mono text-[var(--color-text-tertiary)]">
                {key}
              </dt>
              <dd className="min-w-0 flex-1 break-words font-mono text-[var(--color-text-secondary)]">
                {formatValue(record ? record[key] : undefined)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <details>
        <summary className="cursor-pointer text-xs text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-brand)]">
          原始 JSON
        </summary>
        <div className="code-block mt-2">{safeJson(output)}</div>
      </details>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function formatValue(value: unknown): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  return safeJson(value);
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}
