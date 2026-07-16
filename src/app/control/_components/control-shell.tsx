"use client";

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  CircleCheckBig,
  FileSearch,
  LayoutDashboard,
  ScrollText,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

import { BrandMark } from "./brand-mark";

type NavigationItem = {
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
  matchPrefix?: string;
};

const navigationItems: NavigationItem[] = [
  {
    href: "/control",
    label: "Overview",
    description: "Protected changes",
    icon: LayoutDashboard,
  },
  {
    href: "/control/decisions/ACC-2048",
    label: "Decision record",
    description: "Review ACC-2048",
    icon: FileSearch,
    matchPrefix: "/control/decisions",
  },
  {
    href: "/control/approvals",
    label: "Approvals",
    description: "Exact-value requests",
    icon: CircleCheckBig,
    matchPrefix: "/control/approvals",
  },
  {
    href: "/control/policies",
    label: "Policies",
    description: "Source authority rules",
    icon: ScrollText,
    matchPrefix: "/control/policies",
  },
];

function isNavigationItemActive(pathname: string, item: NavigationItem) {
  if (item.href === "/control") {
    return pathname === item.href;
  }

  const section = item.matchPrefix ?? item.href;
  return pathname === section || pathname.startsWith(`${section}/`);
}

function DesktopNavigation({ pathname }: { pathname: string }) {
  return (
    <nav aria-label="Control center" className="mt-8 px-3">
      <p className="px-3 text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        Control center
      </p>
      <div className="mt-3 space-y-1">
        {navigationItems.map((item) => {
          const active = isNavigationItemActive(pathname, item);
          const Icon = item.icon;

          return (
            <Link
              aria-current={active ? "page" : undefined}
              className={cn(
                "group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
              )}
              href={item.href}
              key={item.href}
            >
              <Icon
                aria-hidden="true"
                className={cn(
                  "size-[1.125rem] shrink-0 transition-colors",
                  active
                    ? "text-sidebar-primary"
                    : "text-muted-foreground group-hover:text-sidebar-foreground",
                )}
                strokeWidth={1.8}
              />
              <span className="min-w-0">
                <span className="block font-medium leading-5">{item.label}</span>
                <span
                  className={cn(
                    "block truncate text-xs leading-4",
                    active ? "text-sidebar-accent-foreground/70" : "text-muted-foreground",
                  )}
                >
                  {item.description}
                </span>
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

function MobileNavigation({ pathname }: { pathname: string }) {
  return (
    <nav
      aria-label="Control center"
      className="flex overflow-x-auto px-3 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {navigationItems.map((item) => {
        const active = isNavigationItemActive(pathname, item);
        const Icon = item.icon;

        return (
          <Link
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-2 border-b-2 border-transparent px-3 py-3 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              active && "border-primary text-foreground",
            )}
            href={item.href}
            key={item.href}
          >
            <Icon
              aria-hidden="true"
              className={cn("size-4", active && "text-primary")}
              strokeWidth={1.9}
            />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function GatewayStatus({ compact = false }: { compact?: boolean }) {
  return (
    <div className={cn("flex items-center", compact ? "gap-2" : "gap-3")}>
      <span
        aria-hidden="true"
        className="size-2 shrink-0 rounded-full bg-success ring-4 ring-success/10"
      />
      <span className={cn("font-medium", compact ? "sr-only" : "text-sm text-sidebar-foreground")}>
        Gateway healthy
      </span>
    </div>
  );
}

export function ControlShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="acc-theme min-h-dvh bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-72 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex">
        <div className="flex h-20 items-center border-b border-sidebar-border px-6">
          <Link
            aria-label="Writ overview"
            className="flex items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring focus-visible:ring-offset-4 focus-visible:ring-offset-sidebar"
            href="/control"
          >
            <BrandMark className="size-9 text-sidebar-primary" />
            <span className="text-[0.9375rem] font-semibold tracking-[-0.015em]">
              Writ
            </span>
          </Link>
        </div>

        <div className="px-6 pt-6">
          <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Workspace
          </p>
          <div className="mt-2.5 flex items-center gap-3 rounded-xl border border-sidebar-border bg-background/60 p-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-xs font-semibold text-primary">
              PD
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">Procurement demo</span>
              <span className="block truncate text-xs text-muted-foreground">Protected workspace</span>
            </span>
          </div>
        </div>

        <DesktopNavigation pathname={pathname} />

        <div className="mt-auto border-t border-sidebar-border px-6 py-5">
          <GatewayStatus />
          <p className="mt-1.5 pl-5 text-xs leading-5 text-muted-foreground">
            Policy engine connected
          </p>
        </div>
      </aside>

      <div className="min-h-dvh lg:pl-72">
        <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur-sm lg:hidden">
          <div className="flex h-16 items-center justify-between gap-4 px-4">
            <Link
              aria-label="Writ overview"
              className="flex min-w-0 items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              href="/control"
            >
              <BrandMark className="size-8 text-primary" />
              <span className="truncate text-sm font-semibold tracking-[-0.015em]">
                Change Control
              </span>
            </Link>

            <div className="flex min-w-0 items-center gap-3">
              <span className="min-w-0 text-right">
                <span className="block text-[0.625rem] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                  Workspace
                </span>
                <span className="block max-w-28 truncate text-xs font-medium">Procurement demo</span>
              </span>
              <GatewayStatus compact />
            </div>
          </div>
          <div className="border-t border-border/70">
            <MobileNavigation pathname={pathname} />
          </div>
        </header>

        <main className="min-w-0">{children}</main>
      </div>
    </div>
  );
}

export default ControlShell;
