"use client";

import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  LockKeyhole,
  ShieldCheck,
  UserRoundCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { useDemoState, type ApprovalStatus } from "./demo-provider";

export type ApprovalReviewCase = {
  requestId: string;
  title: string;
  summary: string;
  requester: string;
  source: string;
  destination: string;
  record: string;
  field: string;
  currentValue: string;
  proposedValue: string;
  policyId: string;
  policyName: string;
  policyReason: string;
  evidence: readonly {
    label: string;
    value: string;
  }[];
};

function StatusLabel({ status }: { status: ApprovalStatus }) {
  const config = {
    pending: {
      icon: Clock3,
      label: "Pending exact approval",
      className: "text-warning",
    },
    approved: {
      icon: CheckCircle2,
      label: "Approved and executed",
      className: "text-success",
    },
    rejected: {
      icon: XCircle,
      label: "Rejected · not executed",
      className: "text-destructive",
    },
  }[status];
  const Icon = config.icon;

  return (
    <span className={cn("inline-flex items-center gap-2 text-sm font-medium", config.className)}>
      <Icon aria-hidden="true" className="size-4" strokeWidth={2} />
      {config.label}
    </span>
  );
}

function MutationValues({ approvalCase }: { approvalCase: ApprovalReviewCase }) {
  return (
    <div className="grid overflow-hidden rounded-lg border bg-muted/25 sm:grid-cols-[1fr_auto_1fr]">
      <div className="flex min-w-0 flex-col gap-2 p-4 sm:p-5">
        <span className="text-xs font-medium text-muted-foreground">Current protected value</span>
        <span className="font-mono text-lg font-semibold tracking-[-0.02em] text-foreground">
          {approvalCase.currentValue}
        </span>
      </div>
      <div className="grid place-items-center border-y bg-background px-3 py-2 text-muted-foreground sm:border-x sm:border-y-0">
        <ArrowRight aria-hidden="true" className="size-4 rotate-90 sm:rotate-0" strokeWidth={1.8} />
      </div>
      <div className="flex min-w-0 flex-col gap-2 p-4 sm:p-5">
        <span className="text-xs font-medium text-muted-foreground">Proposed exact value</span>
        <span className="font-mono text-lg font-semibold tracking-[-0.02em] text-primary">
          {approvalCase.proposedValue}
        </span>
      </div>
    </div>
  );
}

function ApprovalDialog({ approvalCase }: { approvalCase: ApprovalReviewCase }) {
  const { approveExactChange, rejectExactChange } = useDemoState();

  return (
    <Dialog>
      <DialogTrigger render={<Button size="lg" type="button" />}>
        <ShieldCheck data-icon="inline-start" />
        Review exact change
      </DialogTrigger>
      <DialogContent className="acc-portal max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Approve this exact mutation?</DialogTitle>
          <DialogDescription>
            Confirm the requester, evidence, and both protected values from this separate human
            approval session.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5 py-1">
          <MutationValues approvalCase={approvalCase} />

          <dl className="grid gap-x-6 gap-y-4 rounded-lg border p-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted-foreground">Requester</dt>
              <dd className="flex items-center gap-2 font-medium">
                <UserRoundCheck aria-hidden="true" className="size-4 text-success" strokeWidth={2} />
                {approvalCase.requester}
              </dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted-foreground">Protected record</dt>
              <dd className="font-medium">{approvalCase.record}</dd>
            </div>
            {approvalCase.evidence.map((item) => (
              <div className="flex min-w-0 flex-col gap-1" key={item.label}>
                <dt className="text-xs font-medium text-muted-foreground">{item.label}</dt>
                <dd className="truncate font-medium" title={item.value}>
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>

          <section aria-labelledby="approval-policy" className="border-l-2 border-primary pl-4">
            <h3 id="approval-policy" className="text-sm font-semibold">
              {approvalCase.policyId} · {approvalCase.policyName}
            </h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {approvalCase.policyReason}
            </p>
          </section>

          <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" strokeWidth={1.9} />
            This decision applies only to {approvalCase.requestId}. It does not authorize an invoice
            or any future mutation, and it cannot be reused.
          </p>
        </div>

        <DialogFooter>
          <DialogClose
            render={
              <Button onClick={rejectExactChange} type="button" variant="outline" />
            }
          >
            <XCircle data-icon="inline-start" />
            Reject
          </DialogClose>
          <DialogClose render={<Button onClick={approveExactChange} type="button" />}>
            <CheckCircle2 data-icon="inline-start" />
            Approve exact change
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PendingApproval({ approvalCase }: { approvalCase: ApprovalReviewCase }) {
  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>{approvalCase.title}</CardTitle>
        <CardDescription>
          {approvalCase.requestId} · {approvalCase.record}
        </CardDescription>
        <CardAction className="col-span-full col-start-1 row-span-1 row-start-3 mt-2 justify-self-start sm:col-span-1 sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0 sm:justify-self-end">
          <StatusLabel status="pending" />
        </CardAction>
      </CardHeader>

      <CardContent className="flex flex-col gap-6">
        <div className="flex flex-col gap-1">
          <p className="text-sm leading-6 text-muted-foreground">{approvalCase.summary}</p>
        </div>

        <MutationValues approvalCase={approvalCase} />

        <dl className="grid gap-5 border-t pt-5 sm:grid-cols-2 xl:grid-cols-4">
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Trusted requester</dt>
            <dd className="font-medium">{approvalCase.requester}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Request source</dt>
            <dd className="font-medium">{approvalCase.source}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Protected destination</dt>
            <dd className="font-medium">{approvalCase.destination}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Policy</dt>
            <dd className="font-medium">{approvalCase.policyId}</dd>
          </div>
        </dl>
      </CardContent>

      <CardFooter className="flex-col items-stretch gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="flex max-w-xl items-start gap-2 text-xs leading-5 text-muted-foreground">
          <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" strokeWidth={1.9} />
          The agent cannot approve its own request; the gateway stays paused for this separate human
          decision.
        </p>
        <ApprovalDialog approvalCase={approvalCase} />
      </CardFooter>
    </Card>
  );
}

function DecisionResult({
  approvalCase,
  status,
}: {
  approvalCase: ApprovalReviewCase;
  status: Exclude<ApprovalStatus, "pending">;
}) {
  const { decisionTimestamp, resetApproval } = useDemoState();
  const approved = status === "approved";
  const displayedValue = approved ? approvalCase.proposedValue : approvalCase.currentValue;

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>{approved ? "Exact change approved" : "Exact change rejected"}</CardTitle>
        <CardDescription>{approvalCase.requestId} is now closed.</CardDescription>
        <CardAction className="col-span-full col-start-1 row-span-1 row-start-3 mt-2 justify-self-start sm:col-span-1 sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0 sm:justify-self-end">
          <StatusLabel status={status} />
        </CardAction>
      </CardHeader>

      <CardContent className="flex flex-col gap-6">
        <div
          className={cn(
            "flex items-start gap-3 rounded-lg border p-4",
            approved
              ? "border-success/20 bg-success/5"
              : "border-destructive/20 bg-destructive/5",
          )}
        >
          {approved ? (
            <CheckCircle2 aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-success" />
          ) : (
            <XCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-destructive" />
          )}
          <div className="flex flex-col gap-1">
            <p className="font-medium">
              {approved ? "Execution completed" : "Execution did not run"}
            </p>
            <p className="text-sm leading-6 text-muted-foreground">
              {approved
                ? `${approvalCase.record} now stores the approved value ${approvalCase.proposedValue}.`
                : `${approvalCase.record} remains unchanged at ${approvalCase.currentValue}.`}
            </p>
          </div>
        </div>

        <dl className="grid gap-5 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Destination state</dt>
            <dd className="font-mono font-semibold">{displayedValue}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Requested by</dt>
            <dd className="font-medium">{approvalCase.requester}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-medium text-muted-foreground">Decision recorded</dt>
            <dd className="font-medium">
              <time dateTime={decisionTimestamp ?? undefined}>
                {approved ? "Jul 14, 2026 · 9:24 AM PT" : "Jul 14, 2026 · 9:25 AM PT"}
              </time>
            </dd>
          </div>
        </dl>

        <p className="flex items-start gap-2 border-t pt-5 text-xs leading-5 text-muted-foreground">
          <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" strokeWidth={1.9} />
          This single-use decision covered only {approvalCase.field} on {approvalCase.record}. It
          did not authorize an invoice or any future mutation.
        </p>
      </CardContent>

      <CardFooter className="flex-col-reverse items-stretch gap-2 sm:flex-row sm:items-center sm:justify-end">
        <Button onClick={resetApproval} type="button" variant="outline">
          Reset demo
        </Button>
        <Link
          className={buttonVariants()}
          href={`/control/decisions/${approvalCase.requestId}`}
        >
          View decision record
          <ArrowRight aria-hidden="true" data-icon="inline-end" />
        </Link>
      </CardFooter>
    </Card>
  );
}

export function ApprovalReview({ approvalCase }: { approvalCase: ApprovalReviewCase }) {
  const { approvalStatus, requestId, requestedAt } = useDemoState();
  const isCurrentRequest = approvalCase.requestId === requestId;

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 py-8 sm:px-8 sm:py-12 lg:px-10 lg:py-16">
      <header className="flex max-w-3xl flex-col gap-3">
        <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">Exact approvals</h1>
        <p className="max-w-2xl text-base leading-7 text-muted-foreground">
          Review the precise protected values a trusted human requested before the gateway can
          execute them.
        </p>
        <p className="text-xs text-muted-foreground">
          Submitted <time dateTime={requestedAt}>Jul 14, 2026 · 9:20 AM PT</time>
        </p>
      </header>

      {!isCurrentRequest || approvalStatus === "pending" ? (
        <PendingApproval approvalCase={approvalCase} />
      ) : (
        <DecisionResult approvalCase={approvalCase} status={approvalStatus} />
      )}
    </section>
  );
}
