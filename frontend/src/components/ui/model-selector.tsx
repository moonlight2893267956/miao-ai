"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Cpu, ChevronDown, Monitor } from "lucide-react";
import type { LlmModel } from "@/lib/api";

type Props = {
  models: LlmModel[];
  value: string | null;
  systemDefaultLabel: string;
  disabled?: boolean;
  onChange: (modelId: string) => void;
};

export function ModelSelector({
  models,
  value,
  systemDefaultLabel,
  disabled,
  onChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const activeIdx = useRef(-1);

  const selectedModel = models.find((m) => m.id === value) ?? null;

  const displayLabel = selectedModel
    ? `${selectedModel.provider_name ?? "Provider"} / ${selectedModel.name}`
    : systemDefaultLabel;

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => {
    if (disabled) return;
    setOpen((p) => !p);
  }, [disabled]);

  const pick = useCallback(
    (id: string) => {
      onChange(id);
      close();
    },
    [onChange, close]
  );

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (
        triggerRef.current?.contains(e.target as Node) ||
        menuRef.current?.contains(e.target as Node)
      )
        return;
      close();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, close]);

  // Close on Escape, keyboard nav
  useEffect(() => {
    if (!open) return;
    activeIdx.current = 0;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        close();
        triggerRef.current?.focus();
        return;
      }
      const items = menuRef.current?.querySelectorAll('[role="option"]') ?? [];
      const len = items.length;
      if (len === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIdx.current = Math.min(activeIdx.current + 1, len - 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIdx.current = Math.max(activeIdx.current - 1, 0);
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const el = items[activeIdx.current] as HTMLElement;
        el?.click();
        return;
      }
      (items[activeIdx.current] as HTMLElement)?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  return (
    <div className="relative" style={{ minWidth: 260 }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={[
          "model-select-trigger",
          open && "model-select-trigger--open",
          disabled && "model-select-trigger--disabled",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <Cpu className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
        <span
          className="flex-1 truncate text-left font-mono text-[0.8125rem]"
          title={displayLabel}
        >
          {displayLabel}
        </span>
        {!value && (
          <span className="default-badge">默认</span>
        )}
        <ChevronDown
          className={[
            "h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform duration-200",
            open && "rotate-180",
          ]
            .filter(Boolean)
            .join(" ")}
        />
      </button>

      {open && (
        <div ref={menuRef} className="model-select-menu" role="listbox">
          {/* System default */}
          <div
            role="option"
            aria-selected={!value}
            tabIndex={0}
            onClick={() => pick("")}
            className={[
              "model-select-option",
              "model-select-option--default",
              !value && "model-select-option--active",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <div className="option-icon option-icon--default">
              <Monitor className="h-4 w-4" />
            </div>
            <div className="option-body">
              <span className="option-label">系统默认</span>
              <span className="option-sub">{systemDefaultLabel}</span>
            </div>
            {!value && <Check className="h-4 w-4 shrink-0 text-[var(--color-brand)]" />}
          </div>

          {/* Divider */}
          <div className="model-select-divider">
            <span>已配置模型</span>
          </div>

          {/* Model options */}
          {models.map((model) => {
            const isActive = model.id === value;
            return (
              <div
                key={model.id}
                role="option"
                aria-selected={isActive}
                tabIndex={0}
                onClick={() => pick(model.id)}
                className={[
                  "model-select-option",
                  isActive && "model-select-option--active",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <div className="option-icon">
                  <Cpu className="h-4 w-4" />
                </div>
                <div className="option-body">
                  <span className="option-label font-mono text-[0.8125rem]">
                    {model.provider_name ?? "Provider"} / {model.name}
                  </span>
                  {model.is_default && (
                    <span className="option-default-tag">默认</span>
                  )}
                </div>
                {isActive && <Check className="h-4 w-4 shrink-0 text-[var(--color-brand)]" />}
              </div>
            );
          })}

          {models.length === 0 && (
            <div className="model-select-empty">暂无已配置模型</div>
          )}
        </div>
      )}
    </div>
  );
}
