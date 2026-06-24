"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import {
  LayoutDashboard,
  Activity,
  Settings,
  Settings2,
  Users,
} from "lucide-react";

const NAV_ITEMS = [
  {
    label: "概览",
    items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard, exact: true }],
  },
  {
    label: "Agent 管理",
    items: [
      { href: "/agents", label: "Agents", icon: Users, badge: "1", exact: true },
      { href: "/agents/qwen-chat", label: "Agent Detail", icon: Settings2, detailOnly: true },
    ],
  },
  {
    label: "可观测",
    items: [{ href: "/traces", label: "Traces", icon: Activity }],
  },
  {
    label: "设置",
    items: [{ href: "#settings", label: "Settings", icon: Settings, disabled: true }],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  function isActive(item: (typeof NAV_ITEMS)[number]["items"][number]) {
    if ("disabled" in item && item.disabled) return false;
    if ("detailOnly" in item && item.detailOnly) return pathname.startsWith("/agents/");
    const href = item.href;
    if (href === "/") return pathname === "/";
    if ("exact" in item && item.exact) return pathname === href;
    return pathname.startsWith(href);
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-logo">
          <svg
            viewBox="0 0 32 32"
            fill="none"
            className="h-full w-full"
            xmlns="http://www.w3.org/2000/svg"
          >
            <defs>
              <linearGradient id="sg" x1="0" y1="0" x2="32" y2="32">
                <stop offset="0%" stopColor="var(--color-brand)" />
                <stop offset="100%" stopColor="var(--color-accent)" />
              </linearGradient>
              <filter id="logoShadow">
                <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.15" />
              </filter>
            </defs>
            <path d="M6 6L10 2L14 6" fill="url(#sg)" filter="url(#logoShadow)" />
            <path d="M18 6L22 2L26 6" fill="url(#sg)" filter="url(#logoShadow)" />
            <circle cx="16" cy="18" r="11" fill="url(#sg)" filter="url(#logoShadow)" />
            <circle cx="13" cy="15" r="2.5" fill="white" opacity="0.8" />
            <circle cx="19" cy="15" r="2.5" fill="white" opacity="0.8" />
            <circle cx="13.5" cy="15.5" r="1.2" fill="var(--neutral-800)" />
            <circle cx="19.5" cy="15.5" r="1.2" fill="var(--neutral-800)" />
            <ellipse cx="16" cy="20" rx="1.5" ry="1.2" fill="white" opacity="0.7" />
          </svg>
        </div>
        <Link href="/" className="sidebar-brand-text">
          Miao <span className="brand-gradient-text">AI</span>
        </Link>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map((section) => (
          <div key={section.label}>
            <div className="sidebar-section-label">
              {section.label}
            </div>
            {section.items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "nav-item",
                  "disabled" in item && item.disabled && "pointer-events-none opacity-80",
                  isActive(item) && "active"
                )}
              >
                <item.icon className="nav-icon" />
                <span>{item.label}</span>
                {"badge" in item && item.badge && <span className="nav-badge">{item.badge}</span>}
              </Link>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <ThemeToggle />
        <p className="text-center text-[0.65rem] text-muted-foreground/50">
          Miao AI v0.2.0
        </p>
      </div>
    </aside>
  );
}
