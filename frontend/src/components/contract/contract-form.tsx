"use client";

/**
 * Schema 驱动的入参表单（Try Run「表单模式」）。
 *
 * 数据由 agent 契约的 `input.schema` 提供；没有契约的 agent 根本不会渲染到这里。
 *
 * 三条设计约束：
 * 1. 表单是**提示**不是门禁：类型不符只在字段下方给黄色提醒，绝不阻断 Run ——
 *    与后端「契约不参与 invoke 校验」的口径保持一致；
 * 2. 复杂字段（对象 / 对象数组）没有输入控件，退化成等宽 JSON 小框，
 *    所以契约里配 `examples` 很重要（一键填充后改几个字即可）；
 * 3. 单一数据源：本组件只把编辑结果回写给调用方，不做第二份状态。
 */
import * as React from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import type { ContractProperty } from "@/lib/api";

type JsonObject = Record<string, unknown>;

export type ContractFormProps = {
  schema: ContractProperty;
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  disabled?: boolean;
};

export function ContractForm({ schema, value, onChange, disabled }: ContractFormProps) {
  const properties = schema.properties ?? {};
  const names = Object.keys(properties);
  const required = new Set(schema.required ?? []);

  if (names.length === 0) {
    return <p className="form-hint">该 agent 的入参声明里没有可填字段，可直接运行。</p>;
  }

  function setField(key: string, next: unknown) {
    const copy: JsonObject = { ...value };
    if (next === undefined) delete copy[key];
    else copy[key] = next;
    onChange(copy);
  }

  return (
    <div className="space-y-4">
      {names.map((fieldName) => (
        <ContractField
          key={fieldName}
          name={fieldName}
          prop={properties[fieldName]}
          required={required.has(fieldName)}
          value={value[fieldName]}
          disabled={disabled}
          onChange={(next) => setField(fieldName, next)}
        />
      ))}
    </div>
  );
}

type ContractFieldProps = {
  name: string;
  prop: ContractProperty;
  required: boolean;
  value: unknown;
  disabled?: boolean;
  onChange: (next: unknown) => void;
};

function ContractField({
  name,
  prop,
  required,
  value,
  disabled,
  onChange,
}: ContractFieldProps) {
  const id = `contract-field-${name}`;
  const label = prop.title?.trim() || name;
  const warning = mismatchWarning(prop, value);

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label
          htmlFor={id}
          className="text-[var(--text-sm)] font-semibold text-[var(--color-text-secondary)]"
        >
          {label}
          {required && (
            <span className="ml-1 text-[var(--color-brand)]" aria-hidden="true">
              *
            </span>
          )}
        </Label>
        <Badge variant="secondary" className="font-mono text-[10px] leading-4">
          {typeLabel(prop)}
        </Badge>
      </div>

      <FieldControl
        id={id}
        prop={prop}
        value={value}
        disabled={disabled}
        onChange={onChange}
      />

      {prop.description && <p className="form-hint">{prop.description}</p>}
      {warning && (
        <p className="text-xs" style={{ color: "var(--color-warning-text)" }}>
          {warning}
        </p>
      )}
    </div>
  );
}

function FieldControl({
  id,
  prop,
  value,
  disabled,
  onChange,
}: {
  id: string;
  prop: ContractProperty;
  value: unknown;
  disabled?: boolean;
  onChange: (next: unknown) => void;
}) {
  const options = prop.enum;

  if (options && options.length > 0) {
    return (
      <select
        id={id}
        className="select-control"
        disabled={disabled}
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => onChange(coerceEnum(options, e.target.value))}
      >
        <option value="">（未选择）</option>
        {options.map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  }

  if (prop.type === "boolean") {
    return (
      <label className="toggle-label" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          disabled={disabled}
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {value === true ? "true" : "false"}
      </label>
    );
  }

  if (prop.type === "integer" || prop.type === "number") {
    return (
      <Input
        id={id}
        type="number"
        disabled={disabled}
        step={prop.type === "integer" ? 1 : "any"}
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) =>
          onChange(e.target.value === "" ? undefined : Number(e.target.value))
        }
      />
    );
  }

  if (isComplex(prop)) {
    return <JsonControl id={id} value={value} disabled={disabled} onChange={onChange} />;
  }

  if (prop.type === "array") {
    return (
      <Input
        id={id}
        disabled={disabled}
        placeholder="用英文逗号分隔，例如 a, b, c"
        value={Array.isArray(value) ? value.join(", ") : value === undefined ? "" : String(value)}
        onChange={(e) => onChange(splitList(e.target.value, prop.items?.type))}
      />
    );
  }

  if (prop.format === "textarea") {
    return (
      <Textarea
        id={id}
        rows={4}
        disabled={disabled}
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      />
    );
  }

  return (
    <Input
      id={id}
      disabled={disabled}
      placeholder={stringPlaceholder(prop)}
      value={value === undefined || value === null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
    />
  );
}

/** 对象 / 对象数组：等宽 JSON 小框，写不完整也不会污染入参。 */
function JsonControl({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: unknown;
  disabled?: boolean;
  onChange: (next: unknown) => void;
}) {
  const [draft, setDraft] = React.useState(() => stringify(value));
  const [error, setError] = React.useState<string | null>(null);
  const synced = React.useRef(stringify(value));

  React.useEffect(() => {
    const external = stringify(value);
    if (external !== synced.current) {
      synced.current = external;
      setDraft(external);
      setError(null);
    }
  }, [value]);

  return (
    <>
      <Textarea
        id={id}
        rows={4}
        disabled={disabled}
        className="font-mono text-xs"
        value={draft}
        onChange={(e) => {
          const text = e.target.value;
          setDraft(text);
          if (text.trim() === "") {
            synced.current = "";
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            const parsed: unknown = JSON.parse(text);
            synced.current = stringify(parsed);
            setError(null);
            onChange(parsed);
          } catch {
            setError("JSON 还没写完整 —— 解析成功前不会同步到入参");
          }
        }}
      />
      {error && (
        <p className="text-xs" style={{ color: "var(--color-warning-text)" }}>
          {error}
        </p>
      )}
    </>
  );
}

function isComplex(prop: ContractProperty): boolean {
  if (prop.type === "object") return true;
  if (prop.type === "array") {
    const item = prop.items;
    return !!item && (item.type === "object" || !!item.properties);
  }
  return false;
}

function typeLabel(prop: ContractProperty): string {
  if (prop.enum && prop.enum.length > 0) return "enum";
  if (!prop.type) return "any";
  if (isComplex(prop)) return prop.type === "array" ? "array<object>" : "object";
  if (prop.format) return `${prop.type} · ${prop.format}`;
  return prop.type;
}

function stringPlaceholder(prop: ContractProperty): string {
  const first = prop.enum?.[0];
  if (first !== undefined) return String(first);
  return "";
}

function stringify(value: unknown): string {
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "";
  }
}

function coerceEnum(options: unknown[], raw: string): unknown {
  const found = options.find((option) => String(option) === raw);
  return found === undefined ? raw || undefined : found;
}

function splitList(text: string, itemType?: string): unknown[] | undefined {
  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return undefined;
  if (itemType === "integer" || itemType === "number") {
    return parts.map((part) => Number(part)).filter((n) => !Number.isNaN(n));
  }
  if (itemType === "boolean") {
    return parts.map((part) => part === "true");
  }
  return parts;
}

function mismatchWarning(prop: ContractProperty, value: unknown): string | null {
  if (value === undefined || value === null) return null;
  switch (prop.type) {
    case "string":
      return typeof value === "string" ? null : "声明为字符串，当前值不是字符串（仍会照常发送）";
    case "integer":
      return typeof value === "number" && Number.isInteger(value)
        ? null
        : "声明为整数，当前值不是整数";
    case "number":
      return typeof value === "number" ? null : "声明为数字，当前值不是数字";
    case "boolean":
      return typeof value === "boolean" ? null : "声明为布尔值，当前值不是布尔值";
    case "array":
      return Array.isArray(value) ? null : "声明为数组，当前值不是数组";
    case "object":
      return typeof value === "object" && !Array.isArray(value)
        ? null
        : "声明为对象，当前值不是对象";
    default:
      return null;
  }
}
