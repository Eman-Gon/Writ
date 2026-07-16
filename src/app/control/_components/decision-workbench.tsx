"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  CircleDashed,
  CircleX,
  Clock3,
  Database,
  FileText,
  LockKeyhole,
  Play,
  ShieldCheck,
  UserRound,
  Wrench,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  evaluateCase,
  eventsForCase,
  projectFinalState,
  type AuditEvent,
  type ChangeControlCase,
  type EvaluationCheck,
} from "@/lib/change-control/demo";

import { useDemoState } from "./demo-provider";

const checkIcons: Record<EvaluationCheck["key"], typeof UserRound> = {
  user: UserRound,
  agent: Bot,
  tool: Wrench,
  source: FileText,
};

type DecisionTone = "success" | "danger" | "warning";

const toneClasses: Record<DecisionTone, { badge: string; panel: string; dot: string }> = {
  success: {
    badge: "border-success/20 bg-success/10 text-success",
    panel: "border-success/20 bg-success/[0.045]",
    dot: "bg-success",
  },
  danger: {
    badge: "border-destructive/20 bg-destructive/10 text-destructive",
    panel: "border-destructive/20 bg-destructive/[0.035]",
    dot: "bg-destructive",
  },
  warning: {
    badge: "border-warning/20 bg-warning/10 text-warning",
    panel: "border-warning/20 bg-warning/[0.045]",
    dot: "bg-warning",
  },
};

function appendRejectionEvent(
  events: readonly AuditEvent[],
  demoCase: ChangeControlCase,
): readonly AuditEvent[] {
  const completedRequest = events.map((event) =>
    event.kind === "approval_requested"
      ? { ...event, status: "complete" as const }
      : event,
  );

  return [
    ...completedRequest,
    {
      id: `${demoCase.id}-06-rejected`,
      sequence: 6,
      at: "T+2.5s",
      kind: "execution_blocked",
      title: "Exact change rejected",
      detail: `${demoCase.mutation.record} remains ${demoCase.mutation.currentValue}`,
      status: "blocked",
    },
  ];
}

function eventIcon(event: AuditEvent) {
  if (event.status === "blocked") {
    return CircleX;
  }

  if (event.status === "pending") {
    return Clock3;
  }

  return CheckCircle2;
}

function formatAuthority(authority: ChangeControlCase["source"]["authority"]) {
  return authority.replaceAll("_", " ");
}

export function DecisionWorkbench({ demoCase }: { demoCase: ChangeControlCase }) {
  const { approvalStatus, requestId } = useDemoState();
  const isApprovalCase = demoCase.id === requestId;
  const approvalGranted = isApprovalCase && approvalStatus === "approved";
  const approvalRejected = isApprovalCase && approvalStatus === "rejected";

  const evaluation = useMemo(
    () => evaluateCase(demoCase, approvalGranted),
    [demoCase, approvalGranted],
  );
  const projectedState = useMemo(
    () => projectFinalState(demoCase, approvalGranted),
    [demoCase, approvalGranted],
  );
  const events = useMemo(() => {
    const baseEvents = eventsForCase(demoCase, approvalGranted);
    return approvalRejected
      ? appendRejectionEvent(baseEvents, demoCase)
      : baseEvents;
  }, [approvalGranted, approvalRejected, demoCase]);

  const [visibleEventCount, setVisibleEventCount] = useState(events.length);

  useEffect(() => {
    if (visibleEventCount >= events.length) {
      return;
    }

    const timeout = window.setTimeout(() => {
      setVisibleEventCount((count) => Math.min(count + 1, events.length));
    }, 480);

    return () => window.clearTimeout(timeout);
  }, [events.length, visibleEventCount]);

  const decision = (() => {
    if (approvalRejected) {
      return {
        label: "Rejected",
        heading: "Rejected; destination unchanged",
        description:
          "The reviewer rejected this exact mutation, so no execution credential was used.",
        tone: "danger" as const,
      };
    }

    if (evaluation.decision === "hard_deny") {
      return {
        label: "Hard blocked",
        heading: "Blocked before execution",
        description: evaluation.rule.reason,
        tone: "danger" as const,
      };
    }

    if (evaluation.executionStatus === "awaiting_exact_approval") {
      return {
        label: "Approval required",
        heading: "Waiting for exact approval",
        description:
          "The authority chain is valid, but this sensitive field can change only after the exact values are approved.",
        tone: "warning" as const,
      };
    }

    return {
      label: approvalGranted ? "Approved" : "Allowed",
      heading: approvalGranted ? "Approved and executed" : "Allowed and executed",
      description: evaluation.rule.reason,
      tone: "success" as const,
    };
  })();

  const tone = toneClasses[decision.tone];
  const isAwaitingApproval =
    evaluation.executionStatus === "awaiting_exact_approval" && !approvalRejected;
  const finalValue = approvalRejected
    ? demoCase.mutation.currentValue
    : projectedState.after;
  const destinationChanged = approvalRejected ? false : projectedState.changed;
  const visibleEvents = events.slice(0, Math.max(1, visibleEventCount));
  const isReplaying = visibleEventCount < events.length;

  function replayDecision() {
    setVisibleEventCount(1);
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-5 py-7 sm:px-8 lg:px-10 lg:py-10">
      <Link
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        href="/control"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All decisions
      </Link>

      <header className="mt-6 flex flex-col gap-6 border-b border-border pb-8 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="font-mono text-xs font-medium tracking-wide text-muted-foreground">
              {demoCase.id}
            </span>
            <Badge className={cn("border", tone.badge)} variant="outline">
              <span aria-hidden="true" className={cn("size-1.5 rounded-full", tone.dot)} />
              {decision.label}
            </Badge>
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
            {demoCase.title}
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-muted-foreground">
            {demoCase.summary}
          </p>
        </div>

        {isAwaitingApproval ? (
          <Link
            className={cn(buttonVariants({ size: "lg" }), "w-fit")}
            href="/control/approvals"
          >
            Review exact change
            <ArrowRight aria-hidden="true" data-icon="inline-end" />
          </Link>
        ) : (
          <Button className="w-fit" onClick={replayDecision} size="lg">
            <Play aria-hidden="true" data-icon="inline-start" />
            Replay decision
          </Button>
        )}
      </header>

      <section aria-labelledby="authority-heading" className="mt-8">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-[-0.02em]" id="authority-heading">
              Authority chain
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Every actor can be valid while the source still lacks authority.
            </p>
          </div>
          <span className="mt-2 text-xs font-medium text-muted-foreground sm:mt-0">
            Evaluated in 46ms
          </span>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {evaluation.checks.map((check) => {
            const Icon = checkIcons[check.key];
            const passed = check.status === "passed";

            return (
              <article
                className={cn(
                  "relative overflow-hidden rounded-xl border bg-card p-4 shadow-[0_1px_2px_rgb(15_23_42_/_0.03)]",
                  passed ? "border-border" : "border-destructive/25",
                )}
                key={check.key}
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground">
                    <Icon aria-hidden="true" className="size-4" />
                  </span>
                  <span
                    className={cn(
                      "grid size-5 place-items-center rounded-full",
                      passed
                        ? "bg-success/10 text-success"
                        : "bg-destructive/10 text-destructive",
                    )}
                  >
                    {passed ? (
                      <Check aria-hidden="true" className="size-3.5" strokeWidth={2.5} />
                    ) : (
                      <CircleX aria-hidden="true" className="size-3.5" />
                    )}
                  </span>
                </div>
                <h3 className="mt-4 text-sm font-semibold">{check.label}</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{check.detail}</p>
              </article>
            );
          })}
        </div>
      </section>

      <div className="mt-8 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section
          aria-labelledby="proposal-heading"
          className="rounded-xl border border-border bg-card p-5 shadow-[0_1px_2px_rgb(15_23_42_/_0.03)] sm:p-6"
        >
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
              <FileText aria-hidden="true" className="size-4" />
            </span>
            <div>
              <h2 className="text-lg font-semibold tracking-[-0.02em]" id="proposal-heading">
                Source and proposed change
              </h2>
              <p className="mt-0.5 text-xs capitalize text-muted-foreground">
                {formatAuthority(demoCase.source.authority)}
              </p>
            </div>
          </div>

          <dl className="mt-6 grid gap-4 border-b border-border pb-6 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
                Source
              </dt>
              <dd className="mt-2 text-sm font-medium">{demoCase.source.label}</dd>
              <dd className="mt-1 text-xs text-muted-foreground">{demoCase.source.channel}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
                Destination
              </dt>
              <dd className="mt-2 text-sm font-medium">{demoCase.mutation.destination}</dd>
              <dd className="mt-1 font-mono text-xs text-muted-foreground">
                {demoCase.mutation.record}.{demoCase.mutation.field}
              </dd>
            </div>
          </dl>

          <div className="mt-6 rounded-lg border border-border bg-muted/60 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
              Exact mutation
            </p>
            <div className="mt-4 grid items-center gap-3 sm:grid-cols-[1fr_auto_1fr]">
              <div className="rounded-md bg-card px-4 py-3 ring-1 ring-border">
                <p className="text-[0.6875rem] text-muted-foreground">Before</p>
                <p className="mt-1 font-mono text-sm font-semibold">
                  {demoCase.mutation.currentValue}
                </p>
              </div>
              <ArrowRight aria-hidden="true" className="mx-auto size-4 rotate-90 text-muted-foreground sm:rotate-0" />
              <div className="rounded-md bg-card px-4 py-3 ring-1 ring-border">
                <p className="text-[0.6875rem] text-muted-foreground">Proposed</p>
                <p className="mt-1 font-mono text-sm font-semibold">
                  {demoCase.mutation.proposedValue}
                </p>
              </div>
            </div>
          </div>

          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            {demoCase.evidence.map((item) => (
              <div className="rounded-lg border border-border px-3.5 py-3" key={item.label}>
                <p className="text-[0.6875rem] text-muted-foreground">{item.label}</p>
                <p className="mt-1.5 text-xs font-medium">{item.value}</p>
              </div>
            ))}
          </div>
        </section>

        <section
          aria-labelledby="decision-heading"
          className="rounded-xl border border-border bg-card p-5 shadow-[0_1px_2px_rgb(15_23_42_/_0.03)] sm:p-6"
        >
          <div className={cn("rounded-lg border p-5", tone.panel)}>
            <div className="flex items-start gap-3">
              <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg", tone.badge)}>
                {decision.tone === "success" ? (
                  <ShieldCheck aria-hidden="true" className="size-4" />
                ) : decision.tone === "warning" ? (
                  <Clock3 aria-hidden="true" className="size-4" />
                ) : (
                  <LockKeyhole aria-hidden="true" className="size-4" />
                )}
              </span>
              <div>
                <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
                  Policy decision
                </p>
                <h2 className="mt-2 text-xl font-semibold tracking-[-0.025em]" id="decision-heading">
                  {decision.heading}
                </h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {decision.description}
                </p>
              </div>
            </div>
          </div>

          <div className="mt-6 flex items-start justify-between gap-5 border-b border-border pb-6">
            <div>
              <p className="font-mono text-xs font-semibold text-primary">{evaluation.rule.id}</p>
              <p className="mt-1.5 text-sm font-semibold">{evaluation.rule.name}</p>
            </div>
            <Badge variant="outline">Deterministic</Badge>
          </div>

          <div className="mt-6">
            <div className="flex items-center gap-2">
              <Database aria-hidden="true" className="size-4 text-muted-foreground" />
              <h3 className="text-sm font-semibold">Protected destination state</h3>
            </div>
            <div className="mt-4 rounded-lg border border-border bg-muted/45 p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <p className="text-[0.6875rem] text-muted-foreground">Final stored value</p>
                  <p className="mt-1.5 font-mono text-lg font-semibold">{finalValue}</p>
                </div>
                <Badge
                  className={cn(
                    "border",
                    destinationChanged
                      ? "border-success/20 bg-success/10 text-success"
                      : "border-border bg-card text-muted-foreground",
                  )}
                  variant="outline"
                >
                  {destinationChanged ? "Destination updated" : "Unchanged"}
                </Badge>
              </div>
              <p className="mt-4 border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
                {approvalRejected
                  ? "Reviewer rejected the exact mutation; execution was never attempted."
                  : projectedState.reason}
              </p>
            </div>
          </div>

          {isAwaitingApproval ? (
            <div className="mt-5 flex items-center justify-between gap-4 rounded-lg border border-warning/20 bg-warning/[0.045] p-4">
              <p className="text-xs leading-5 text-muted-foreground">
                Approval is bound to this record, field, and exact value pair.
              </p>
              <Link className="shrink-0 text-sm font-semibold text-primary hover:underline" href="/control/approvals">
                Review
              </Link>
            </div>
          ) : null}
        </section>
      </div>

      <section aria-labelledby="audit-heading" className="mt-8 rounded-xl border border-border bg-card p-5 shadow-[0_1px_2px_rgb(15_23_42_/_0.03)] sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-[-0.02em]" id="audit-heading">
              Decision trace
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              A replayable audit record from interception to final state.
            </p>
          </div>
          {isAwaitingApproval ? (
            <Button disabled={isReplaying} onClick={replayDecision} variant="outline">
              {isReplaying ? <CircleDashed aria-hidden="true" className="animate-spin" /> : <Play aria-hidden="true" />}
              {isReplaying ? "Replaying…" : "Replay trace"}
            </Button>
          ) : null}
        </div>

        <ol className="mt-7 grid gap-0 lg:grid-cols-[repeat(auto-fit,minmax(9rem,1fr))]">
          {visibleEvents.map((event, index) => {
            const Icon = eventIcon(event);
            const isLast = index === visibleEvents.length - 1;

            return (
              <li className="relative flex gap-4 pb-6 last:pb-0 lg:block lg:pb-0 lg:pr-5" key={event.id}>
                {!isLast ? (
                  <span
                    aria-hidden="true"
                    className="absolute bottom-0 left-[0.6875rem] top-6 w-px bg-border lg:left-6 lg:right-0 lg:top-3 lg:h-px lg:w-auto"
                  />
                ) : null}
                <span
                  className={cn(
                    "relative z-10 grid size-6 shrink-0 place-items-center rounded-full ring-4 ring-card lg:ml-3",
                    event.status === "complete" && "bg-success/10 text-success",
                    event.status === "pending" && "bg-warning/10 text-warning",
                    event.status === "blocked" && "bg-destructive/10 text-destructive",
                  )}
                >
                  <Icon aria-hidden="true" className="size-3.5" />
                </span>
                <div className="min-w-0 lg:mt-4">
                  <p className="font-mono text-[0.625rem] text-muted-foreground">{event.at}</p>
                  <h3 className="mt-1.5 text-xs font-semibold">{event.title}</h3>
                  <p className="mt-1 text-[0.6875rem] leading-5 text-muted-foreground">{event.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <p className="mt-6 text-center text-xs leading-5 text-muted-foreground">
        The agent proposes the change. The gateway owns policy enforcement and execution credentials.
      </p>
    </div>
  );
}
