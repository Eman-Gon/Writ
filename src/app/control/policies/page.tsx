import type { Metadata } from "next";
import { LockKeyhole, ShieldCheck, ShieldX } from "lucide-react";

import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: { absolute: "Policies · Writ" },
  description: "Deterministic source-authority policies for protected business changes.",
};

const authorityRows = [
  {
    source: "invoice",
    description: "Parsed invoice document",
    allowed: ["invoice_number", "amount", "due_date", "line_items"],
    approval: [],
    denied: ["vendor.bank_account", "approval_status", "payment_routing"],
  },
  {
    source: "finance_admin",
    description: "Authenticated finance administrator",
    allowed: [],
    approval: ["vendor.bank_account"],
    denied: [],
  },
] as const;

const policyRules = [
  {
    id: "CC-TXN-101",
    title: "Authoritative transaction fields",
    description:
      "Allows invoice to write invoice_number, amount, due_date, and line_items directly.",
    outcome: "Allow",
    tone: "allow",
  },
  {
    id: "CC-SOURCE-401",
    title: "External routing changes denied",
    description:
      "Hard-denies vendor.bank_account, approval_status, and payment_routing when proposed by an untrusted document.",
    outcome: "Hard deny",
    tone: "deny",
  },
  {
    id: "CC-ROUTING-202",
    title: "Exact approval for bank routing",
    description:
      "Lets finance_admin request vendor.bank_account only when an approver confirms the exact proposed value.",
    outcome: "Exact approval",
    tone: "approval",
  },
] as const;

type Tone = "allow" | "approval" | "deny";

const toneStyles = {
  allow: {
    icon: ShieldCheck,
    text: "text-success",
    surface: "bg-success/10",
  },
  approval: {
    icon: LockKeyhole,
    text: "text-warning",
    surface: "bg-warning/10",
  },
  deny: {
    icon: ShieldX,
    text: "text-destructive",
    surface: "bg-destructive/10",
  },
} as const;

function FieldList({ fields }: { fields: readonly string[] }) {
  if (fields.length === 0) {
    return (
      <span className="text-muted-foreground">
        <span aria-hidden="true">—</span>
        <span className="sr-only">No fields</span>
      </span>
    );
  }

  return (
    <ul className="space-y-1.5">
      {fields.map((field) => (
        <li className="font-mono text-xs leading-5 text-foreground" key={field}>
          {field}
        </li>
      ))}
    </ul>
  );
}

function AuthorityValue({
  fields,
  tone,
}: {
  fields: readonly string[];
  tone: Tone;
}) {
  if (fields.length === 0) {
    return <FieldList fields={fields} />;
  }

  const style = toneStyles[tone];
  const Icon = style.icon;

  return (
    <div className="flex items-start gap-2.5">
      <span className={cn("mt-0.5 grid size-6 shrink-0 place-items-center rounded-md", style.surface)}>
        <Icon aria-hidden="true" className={cn("size-3.5", style.text)} strokeWidth={2} />
      </span>
      <FieldList fields={fields} />
    </div>
  );
}

function MobileAuthorityRow({
  label,
  fields,
  tone,
}: {
  label: string;
  fields: readonly string[];
  tone: Tone;
}) {
  const style = toneStyles[tone];
  const Icon = style.icon;

  return (
    <div className="grid gap-2 py-4 first:pt-0 last:pb-0 sm:grid-cols-[8.5rem_1fr] sm:gap-4">
      <dt className={cn("flex items-center gap-2 text-xs font-semibold", style.text)}>
        <Icon aria-hidden="true" className="size-4" strokeWidth={2} />
        {label}
      </dt>
      <dd>
        <FieldList fields={fields} />
      </dd>
    </div>
  );
}

function RuleOutcome({ outcome, tone }: { outcome: string; tone: Tone }) {
  const style = toneStyles[tone];
  const Icon = style.icon;

  return (
    <span className={cn("flex shrink-0 items-center gap-2 text-xs font-semibold", style.text)}>
      <Icon aria-hidden="true" className="size-4" strokeWidth={2} />
      {outcome}
    </span>
  );
}

export default function PoliciesPage() {
  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 sm:py-10 lg:px-10 lg:py-12 xl:px-12">
      <header className="max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-[-0.035em] text-foreground sm:text-4xl">
          Source authority is explicit.
        </h1>
        <p className="mt-3 text-base leading-7 text-muted-foreground">
          Policies bind source authority to destination fields before a mutation can execute.
        </p>
      </header>

      <section aria-labelledby="authority-heading" className="mt-10">
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <h2 id="authority-heading" className="text-lg font-semibold tracking-[-0.02em]">
            Source authority matrix
          </h2>
          <p className="hidden text-xs text-muted-foreground sm:block">Closed unless listed</p>
        </div>

        <div className="hidden overflow-hidden rounded-xl border border-border bg-card xl:block">
          <table className="w-full table-fixed border-collapse text-left">
            <caption className="sr-only">
              Fields each source may write directly, request with exact approval, or never write.
            </caption>
            <thead className="border-b border-border bg-muted/60">
              <tr>
                <th className="w-[22%] px-5 py-3.5 text-xs font-semibold text-muted-foreground" scope="col">
                  Source
                </th>
                <th className="w-[26%] px-5 py-3.5 text-xs font-semibold text-muted-foreground" scope="col">
                  Direct write
                </th>
                <th className="w-[23%] px-5 py-3.5 text-xs font-semibold text-muted-foreground" scope="col">
                  Exact approval
                </th>
                <th className="w-[29%] px-5 py-3.5 text-xs font-semibold text-muted-foreground" scope="col">
                  Never
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {authorityRows.map((row) => (
                <tr key={row.source}>
                  <th className="px-5 py-5 align-top font-normal" scope="row">
                    <code className="font-mono text-sm font-semibold text-foreground">{row.source}</code>
                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                      {row.description}
                    </span>
                  </th>
                  <td className="px-5 py-5 align-top">
                    <AuthorityValue fields={row.allowed} tone="allow" />
                  </td>
                  <td className="px-5 py-5 align-top">
                    <AuthorityValue fields={row.approval} tone="approval" />
                  </td>
                  <td className="px-5 py-5 align-top">
                    <AuthorityValue fields={row.denied} tone="deny" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="space-y-3 xl:hidden">
          {authorityRows.map((row) => (
            <article className="rounded-xl border border-border bg-card p-5" key={row.source}>
              <header className="border-b border-border pb-4">
                <code className="font-mono text-sm font-semibold text-foreground">{row.source}</code>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{row.description}</p>
              </header>
              <dl className="mt-4 divide-y divide-border">
                <MobileAuthorityRow fields={row.allowed} label="Direct write" tone="allow" />
                <MobileAuthorityRow fields={row.approval} label="Exact approval" tone="approval" />
                <MobileAuthorityRow fields={row.denied} label="Never" tone="deny" />
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="rules-heading" className="mt-12 pb-8">
        <h2 id="rules-heading" className="text-lg font-semibold tracking-[-0.02em]">
          Deterministic rules
        </h2>

        <ol className="mt-4 overflow-hidden rounded-xl border border-border bg-card">
          {policyRules.map((rule) => (
            <li
              className="grid gap-3 border-b border-border px-5 py-5 last:border-b-0 sm:grid-cols-[7.5rem_minmax(0,1fr)_auto] sm:items-start sm:gap-5"
              key={rule.id}
            >
              <code className="font-mono text-xs font-semibold leading-6 text-primary">{rule.id}</code>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold leading-6 text-foreground">{rule.title}</h3>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{rule.description}</p>
              </div>
              <RuleOutcome outcome={rule.outcome} tone={rule.tone} />
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
