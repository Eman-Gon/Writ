"use client";

import Link from "next/link";

import {
  demoCases,
  evaluateCase,
  projectFinalState,
  type ChangeControlCase,
  type EvaluationResult,
  type ProjectedDestinationState,
  type SourceAuthority,
} from "@/lib/change-control/demo";
import { cn } from "@/lib/utils";

import { useDemoState, type ApprovalStatus } from "./demo-provider";

const workflow = [
  { label: "Intercept", detail: "Capture the exact change" },
  { label: "Verify authority", detail: "Check identity and source" },
  { label: "Decide", detail: "Apply a stable policy" },
  { label: "Execute safely", detail: "Update or preserve state" },
] as const;

const authorityLabels: Record<SourceAuthority, string> = {
  system_of_record: "System of record",
  external_document: "External document",
  authenticated_finance_admin: "Authenticated finance admin",
};

type StatusTone = "success" | "warning" | "error";

type DecisionPresentation = {
  label: string;
  outcome: string;
  tone: StatusTone;
};

const statusToneClasses: Record<StatusTone, string> = {
  success: "border-success/20 bg-success/10 text-success",
  warning: "border-warning/20 bg-warning/10 text-warning",
  error: "border-error/20 bg-error/10 text-error",
};

function ArrowIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 20 20"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M4.75 10h10.5m-4-4.25L15.5 10l-4.25 4.25"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}

function presentationForCase(
  demoCase: ChangeControlCase,
  evaluation: EvaluationResult,
  finalState: ProjectedDestinationState,
  approvalStatus: ApprovalStatus,
): DecisionPresentation {
  if (demoCase.id === "ACC-2051") {
    if (approvalStatus === "approved") {
      return {
        label: "Approved · updated",
        outcome: "Exact approval granted · Destination updated",
        tone: "success",
      };
    }

    if (approvalStatus === "rejected") {
      return {
        label: "Rejected · unchanged",
        outcome: "Exact approval rejected · Destination unchanged",
        tone: "error",
      };
    }

    return {
      label: "Pending exact approval",
      outcome: "Exact approval required · Destination unchanged",
      tone: "warning",
    };
  }

  if (evaluation.decision === "hard_deny") {
    return {
      label: "Hard blocked · unchanged",
      outcome: "Hard deny · Destination unchanged",
      tone: "error",
    };
  }

  return {
    label: finalState.changed ? "Allowed · updated" : "Allowed",
    outcome: finalState.changed ? "Allow · Destination updated" : "Allow",
    tone: "success",
  };
}

function DecisionRow({
  demoCase,
  approvalStatus,
}: {
  demoCase: ChangeControlCase;
  approvalStatus: ApprovalStatus;
}) {
  const approvalGranted = demoCase.id === "ACC-2051" && approvalStatus === "approved";
  const evaluation = evaluateCase(demoCase, approvalGranted);
  const finalState = projectFinalState(demoCase, approvalGranted);
  const presentation = presentationForCase(
    demoCase,
    evaluation,
    finalState,
    approvalStatus,
  );

  return (
    <li>
      <Link
        aria-label={`Open decision ${demoCase.id}: ${presentation.label}`}
        className="group block rounded-xl border border-border bg-card p-5 shadow-[0_1px_2px_rgba(11,13,18,0.03)] transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-px hover:border-primary/30 hover:shadow-[0_10px_30px_rgba(11,13,18,0.06)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:p-6"
        href={`/control/decisions/${demoCase.id}`}
      >
        <div className="grid min-w-0 gap-5 sm:grid-cols-2 xl:grid-cols-[minmax(10rem,1.2fr)_minmax(10rem,1.1fr)_minmax(10rem,1.15fr)_minmax(10rem,1.25fr)] xl:items-center xl:gap-5">
          <div className="min-w-0 sm:col-span-2 xl:col-span-1">
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="data text-[0.6875rem] font-semibold tracking-[0.08em] text-muted-foreground">
                {demoCase.id}
              </span>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.6875rem] font-semibold leading-none",
                  statusToneClasses[presentation.tone],
                )}
              >
                <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
                {presentation.label}
              </span>
            </div>
            <h2 className="mt-2.5 text-base font-semibold leading-6 tracking-[-0.015em] text-card-foreground">
              {demoCase.title}
            </h2>
          </div>

          <div className="min-w-0">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.11em] text-muted-foreground">
              Authority path
            </p>
            <p className="mt-1.5 truncate text-sm font-medium text-card-foreground">
              {demoCase.source.label}
            </p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {authorityLabels[demoCase.source.authority]}
            </p>
            <p className="mt-2 flex min-w-0 items-start gap-1.5 text-xs font-medium text-card-foreground">
              <ArrowIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block truncate">{demoCase.mutation.destination}</span>
                <span className="block font-mono text-[0.6875rem] text-muted-foreground">
                  .{demoCase.mutation.field}
                </span>
              </span>
            </p>
          </div>

          <div className="min-w-0">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.11em] text-muted-foreground">
              Exact mutation
            </p>
            <div className="data mt-1.5 flex items-center gap-2 text-xs font-medium text-card-foreground">
              <span className="truncate">{demoCase.mutation.currentValue}</span>
              <ArrowIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{demoCase.mutation.proposedValue}</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Final: <span className="data">{finalState.after}</span>
            </p>
          </div>

          <div className="min-w-0 sm:col-span-2 xl:col-span-1">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.11em] text-muted-foreground">
              Policy outcome
            </p>
            <p className="data mt-1.5 text-xs font-semibold text-card-foreground">
              {evaluation.rule.id}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {presentation.outcome}
            </p>
          </div>
        </div>
      </Link>
    </li>
  );
}

export function OverviewClient() {
  const { approvalStatus } = useDemoState();

  return (
    <div className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8 lg:px-10 lg:py-10">
      <header className="flex flex-col gap-6 border-b border-border pb-9 sm:pb-10 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-[-0.035em] text-foreground sm:text-[2.5rem] sm:leading-[1.08]">
            See every protected change.
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground sm:text-base sm:leading-7">
            Follow each request from captured source to a deterministic execution outcome.
          </p>
        </div>

        <Link
          className="inline-flex h-11 w-fit items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-sm transition-[background-color,transform] hover:-translate-y-px hover:bg-accent-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          href="/control/approvals"
        >
          Review exact approval
          <ArrowIcon className="size-4" />
        </Link>
      </header>

      <section aria-labelledby="workflow-heading" className="py-8 sm:py-9">
        <h2 className="sr-only" id="workflow-heading">
          Protected change workflow
        </h2>
        <ol className="grid overflow-hidden rounded-xl border border-border bg-card shadow-[0_1px_2px_rgba(11,13,18,0.03)] sm:grid-cols-2 lg:grid-cols-4">
          {workflow.map((step, index) => (
            <li
              className="relative flex min-h-24 items-start gap-3 border-b border-border p-4 last:border-b-0 sm:[&:nth-child(odd)]:border-r sm:[&:nth-child(3)]:border-b-0 lg:min-h-0 lg:border-b-0 lg:border-r lg:last:border-r-0"
              key={step.label}
            >
              <span className="data grid size-7 shrink-0 place-items-center rounded-full border border-primary/20 bg-accent text-[0.6875rem] font-semibold text-accent-foreground">
                {index + 1}
              </span>
              <span className="min-w-0 pt-0.5">
                <span className="block text-sm font-semibold text-card-foreground">
                  {step.label}
                </span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                  {step.detail}
                </span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="decisions-heading">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h2
              className="text-lg font-semibold tracking-[-0.02em] text-foreground"
              id="decisions-heading"
            >
              Recent decisions
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Open a record to inspect its evidence and immutable audit trail.
            </p>
          </div>
        </div>

        <ul aria-live="polite" className="space-y-3">
          {demoCases.map((demoCase) => (
            <DecisionRow
              approvalStatus={approvalStatus}
              demoCase={demoCase}
              key={demoCase.id}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}
