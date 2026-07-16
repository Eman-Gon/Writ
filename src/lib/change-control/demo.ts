export type DemoCaseId = "ACC-2046" | "ACC-2048" | "ACC-2051";

export type SourceAuthority =
  | "system_of_record"
  | "external_document"
  | "authenticated_finance_admin";

export type FieldSensitivity = "transactional_amount" | "bank_routing";
export type PolicyDecision = "allow" | "hard_deny" | "require_exact_approval";
export type ExecutionStatus = "executed" | "blocked" | "awaiting_exact_approval";
export type CheckKey = "user" | "agent" | "tool" | "source";
export type CheckStatus = "passed" | "failed";

export interface ControlSubject {
  readonly label: string;
  readonly authorized: boolean;
}

export interface ChangeSource {
  readonly label: string;
  readonly channel: string;
  readonly authority: SourceAuthority;
}

export interface ChangeMutation {
  readonly destination: string;
  readonly record: string;
  readonly field: string;
  readonly sensitivity: FieldSensitivity;
  readonly currentValue: string;
  readonly proposedValue: string;
}

export interface EvidenceItem {
  readonly label: string;
  readonly value: string;
}

export interface ChangeControlCase {
  readonly id: DemoCaseId;
  readonly title: string;
  readonly summary: string;
  readonly actors: {
    readonly user: ControlSubject;
    readonly agent: ControlSubject;
    readonly tool: ControlSubject;
  };
  readonly source: ChangeSource;
  readonly mutation: ChangeMutation;
  readonly evidence: readonly EvidenceItem[];
}

export interface EvaluationCheck {
  readonly key: CheckKey;
  readonly label: string;
  readonly status: CheckStatus;
  readonly detail: string;
}

export interface PolicyRule {
  readonly id: string;
  readonly name: string;
  readonly reason: string;
  readonly decision: PolicyDecision;
}

export interface ApprovalState {
  readonly required: boolean;
  readonly granted: boolean;
  readonly scope: string | null;
}

export interface EvaluationResult {
  readonly caseId: DemoCaseId;
  readonly decision: PolicyDecision;
  readonly executionStatus: ExecutionStatus;
  readonly canExecute: boolean;
  readonly rule: PolicyRule;
  readonly checks: readonly EvaluationCheck[];
  readonly approval: ApprovalState;
}

export type AuditEventKind =
  | "request_received"
  | "identity_verified"
  | "provenance_captured"
  | "policy_evaluated"
  | "approval_requested"
  | "approval_granted"
  | "execution_completed"
  | "execution_blocked";

export type AuditEventStatus = "complete" | "pending" | "blocked";

export interface AuditEvent {
  readonly id: string;
  readonly sequence: number;
  readonly at: string;
  readonly kind: AuditEventKind;
  readonly title: string;
  readonly detail: string;
  readonly status: AuditEventStatus;
}

export type DestinationStateStatus =
  | "updated"
  | "blocked_unchanged"
  | "awaiting_approval";

export interface ProjectedDestinationState {
  readonly caseId: DemoCaseId;
  readonly destination: string;
  readonly record: string;
  readonly field: string;
  readonly before: string;
  readonly after: string;
  readonly changed: boolean;
  readonly status: DestinationStateStatus;
  readonly reason: string;
}

export const demoCases: readonly ChangeControlCase[] = [
  {
    id: "ACC-2046",
    title: "Invoice amount synchronized",
    summary:
      "An authorized AP agent updates a transactional amount from the finance system of record.",
    actors: {
      user: { label: "AP operator •••4", authorized: true },
      agent: { label: "Invoice agent •••2", authorized: true },
      tool: { label: "ERP write gateway •••1", authorized: true },
    },
    source: {
      label: "ERP invoice •••2046",
      channel: "Finance ERP connector •••1",
      authority: "system_of_record",
    },
    mutation: {
      destination: "Finance ERP",
      record: "Invoice •••2046",
      field: "invoice_total",
      sensitivity: "transactional_amount",
      currentValue: "$••,480.00",
      proposedValue: "$••,640.00",
    },
    evidence: [
      { label: "Source record", value: "Invoice •••2046" },
      { label: "Captured total", value: "$••,640.00" },
      { label: "Connector", value: "Finance ERP •••1" },
    ],
  },
  {
    id: "ACC-2048",
    title: "Bank detail change blocked",
    summary:
      "An external invoice proposes new bank details that its source is not allowed to authorize.",
    actors: {
      user: { label: "AP operator •••4", authorized: true },
      agent: { label: "Invoice agent •••2", authorized: true },
      tool: { label: "ERP write gateway •••1", authorized: true },
    },
    source: {
      label: "External invoice •••2048",
      channel: "External attachment •••8",
      authority: "external_document",
    },
    mutation: {
      destination: "Vendor master",
      record: "Vendor •••71",
      field: "bank_account",
      sensitivity: "bank_routing",
      currentValue: "•••• 1842",
      proposedValue: "•••• 7729",
    },
    evidence: [
      { label: "Source document", value: "Invoice •••2048" },
      { label: "Captured account", value: "•••• 7729" },
      { label: "Vendor", value: "Vendor •••71" },
    ],
  },
  {
    id: "ACC-2051",
    title: "Bank change requires approval",
    summary:
      "An authenticated finance admin requests a bank update that must be approved exactly before execution.",
    actors: {
      user: { label: "Finance admin •••7", authorized: true },
      agent: { label: "Vendor agent •••3", authorized: true },
      tool: { label: "ERP write gateway •••1", authorized: true },
    },
    source: {
      label: "Finance admin session •••7",
      channel: "Protected admin console •••2",
      authority: "authenticated_finance_admin",
    },
    mutation: {
      destination: "Vendor master",
      record: "Vendor •••92",
      field: "bank_account",
      sensitivity: "bank_routing",
      currentValue: "•••• 4061",
      proposedValue: "•••• 1188",
    },
    evidence: [
      { label: "Admin session", value: "Session •••7" },
      { label: "Requested account", value: "•••• 1188" },
      { label: "Authentication", value: "MFA verified" },
    ],
  },
];

const RULES = {
  identityDenied: {
    id: "CC-AUTH-001",
    name: "Verified actors only",
    reason: "Every user, agent, and tool in the mutation chain must be authorized.",
    decision: "hard_deny",
  },
  transactionalAllow: {
    id: "CC-TXN-101",
    name: "Authoritative transaction fields",
    reason: "The system-of-record invoice is authoritative for transactional amounts.",
    decision: "allow",
  },
  externalRoutingDenied: {
    id: "CC-SOURCE-401",
    name: "External routing changes denied",
    reason: "External documents cannot authorize bank-routing changes.",
    decision: "hard_deny",
  },
  adminRoutingApproval: {
    id: "CC-ROUTING-202",
    name: "Exact approval for bank routing",
    reason:
      "Authenticated finance-admin bank changes require approval of the exact before-and-after values.",
    decision: "require_exact_approval",
  },
  defaultDenied: {
    id: "CC-DEFAULT-000",
    name: "Closed by default",
    reason: "No source-authority rule permits this field mutation.",
    decision: "hard_deny",
  },
} as const satisfies Record<string, PolicyRule>;

const AUTHORITY_LABELS: Record<SourceAuthority, string> = {
  system_of_record: "System-of-record data",
  external_document: "External document",
  authenticated_finance_admin: "Authenticated finance admin",
};

function isSourceAuthorized(demoCase: ChangeControlCase): boolean {
  const { authority } = demoCase.source;
  const { sensitivity } = demoCase.mutation;

  return (
    (sensitivity === "transactional_amount" && authority === "system_of_record") ||
    (sensitivity === "bank_routing" && authority === "authenticated_finance_admin")
  );
}

function checksForCase(demoCase: ChangeControlCase): readonly EvaluationCheck[] {
  const sourceAuthorized = isSourceAuthorized(demoCase);

  return [
    {
      key: "user",
      label: "User",
      status: demoCase.actors.user.authorized ? "passed" : "failed",
      detail: demoCase.actors.user.label,
    },
    {
      key: "agent",
      label: "Agent",
      status: demoCase.actors.agent.authorized ? "passed" : "failed",
      detail: demoCase.actors.agent.label,
    },
    {
      key: "tool",
      label: "Tool",
      status: demoCase.actors.tool.authorized ? "passed" : "failed",
      detail: demoCase.actors.tool.label,
    },
    {
      key: "source",
      label: "Source",
      status: sourceAuthorized ? "passed" : "failed",
      detail: `${AUTHORITY_LABELS[demoCase.source.authority]} for ${demoCase.mutation.field}`,
    },
  ];
}

function ruleForCase(
  demoCase: ChangeControlCase,
  checks: readonly EvaluationCheck[],
): PolicyRule {
  if (checks.slice(0, 3).some((check) => check.status === "failed")) {
    return RULES.identityDenied;
  }

  const { authority } = demoCase.source;
  const { sensitivity } = demoCase.mutation;

  if (sensitivity === "transactional_amount" && authority === "system_of_record") {
    return RULES.transactionalAllow;
  }

  if (sensitivity === "bank_routing" && authority === "external_document") {
    return RULES.externalRoutingDenied;
  }

  if (sensitivity === "bank_routing" && authority === "authenticated_finance_admin") {
    return RULES.adminRoutingApproval;
  }

  return RULES.defaultDenied;
}

function approvalScope(demoCase: ChangeControlCase): string {
  const { record, field, currentValue, proposedValue } = demoCase.mutation;
  return `${record} · ${field} · ${currentValue} → ${proposedValue}`;
}

export function getDemoCase(id: DemoCaseId): ChangeControlCase;
export function getDemoCase(id: string): ChangeControlCase | undefined;
export function getDemoCase(id: string): ChangeControlCase | undefined {
  return demoCases.find((demoCase) => demoCase.id === id);
}

export function evaluateCase(
  demoCase: ChangeControlCase,
  approvalGranted = false,
): EvaluationResult {
  const checks = checksForCase(demoCase);
  const rule = ruleForCase(demoCase, checks);
  const approvalRequired = rule.decision === "require_exact_approval";
  const exactApprovalGranted = approvalRequired && approvalGranted;

  const executionStatus: ExecutionStatus =
    rule.decision === "allow"
      ? "executed"
      : rule.decision === "hard_deny"
        ? "blocked"
        : exactApprovalGranted
          ? "executed"
          : "awaiting_exact_approval";

  return {
    caseId: demoCase.id,
    decision: rule.decision,
    executionStatus,
    canExecute: executionStatus === "executed",
    rule,
    checks,
    approval: {
      required: approvalRequired,
      granted: exactApprovalGranted,
      scope: approvalRequired ? approvalScope(demoCase) : null,
    },
  };
}

export function eventsForCase(
  demoCase: ChangeControlCase,
  approvalGranted = false,
): readonly AuditEvent[] {
  const evaluation = evaluateCase(demoCase, approvalGranted);
  const identitiesPassed = evaluation.checks
    .filter((check) => check.key !== "source")
    .every((check) => check.status === "passed");

  const events: AuditEvent[] = [
    {
      id: `${demoCase.id}-01`,
      sequence: 1,
      at: "T+000ms",
      kind: "request_received",
      title: "Change intercepted",
      detail: `${demoCase.mutation.field}: ${demoCase.mutation.currentValue} → ${demoCase.mutation.proposedValue}`,
      status: "complete",
    },
    {
      id: `${demoCase.id}-02`,
      sequence: 2,
      at: "T+018ms",
      kind: "identity_verified",
      title: identitiesPassed ? "Identity chain verified" : "Identity check failed",
      detail: `${demoCase.actors.user.label} · ${demoCase.actors.agent.label} · ${demoCase.actors.tool.label}`,
      status: identitiesPassed ? "complete" : "blocked",
    },
    {
      id: `${demoCase.id}-03`,
      sequence: 3,
      at: "T+031ms",
      kind: "provenance_captured",
      title: "Provenance captured",
      detail: `${demoCase.source.label} via ${demoCase.source.channel}`,
      status: "complete",
    },
    {
      id: `${demoCase.id}-04`,
      sequence: 4,
      at: "T+046ms",
      kind: "policy_evaluated",
      title: evaluation.rule.name,
      detail: `${evaluation.rule.id} · ${evaluation.rule.reason}`,
      status: evaluation.decision === "hard_deny" ? "blocked" : "complete",
    },
  ];

  if (evaluation.decision === "allow") {
    events.push({
      id: `${demoCase.id}-05`,
      sequence: 5,
      at: "T+068ms",
      kind: "execution_completed",
      title: "Destination updated",
      detail: `${demoCase.mutation.record} now stores ${demoCase.mutation.proposedValue}`,
      status: "complete",
    });
  } else if (evaluation.decision === "hard_deny") {
    events.push({
      id: `${demoCase.id}-05`,
      sequence: 5,
      at: "T+052ms",
      kind: "execution_blocked",
      title: "Execution blocked",
      detail: `${demoCase.mutation.record} remains ${demoCase.mutation.currentValue}`,
      status: "blocked",
    });
  } else {
    events.push({
      id: `${demoCase.id}-05`,
      sequence: 5,
      at: "T+052ms",
      kind: "approval_requested",
      title: "Exact approval requested",
      detail: evaluation.approval.scope ?? "Exact mutation approval required",
      status: evaluation.approval.granted ? "complete" : "pending",
    });

    if (evaluation.approval.granted) {
      events.push(
        {
          id: `${demoCase.id}-06`,
          sequence: 6,
          at: "T+2.4s",
          kind: "approval_granted",
          title: "Exact change approved",
          detail: evaluation.approval.scope ?? "Exact mutation approved",
          status: "complete",
        },
        {
          id: `${demoCase.id}-07`,
          sequence: 7,
          at: "T+2.5s",
          kind: "execution_completed",
          title: "Destination updated",
          detail: `${demoCase.mutation.record} now stores ${demoCase.mutation.proposedValue}`,
          status: "complete",
        },
      );
    }
  }

  return events;
}

export function projectFinalState(
  demoCase: ChangeControlCase,
  approvalGranted = false,
): ProjectedDestinationState {
  const evaluation = evaluateCase(demoCase, approvalGranted);
  const changed = evaluation.executionStatus === "executed";

  const status: DestinationStateStatus = changed
    ? "updated"
    : evaluation.executionStatus === "blocked"
      ? "blocked_unchanged"
      : "awaiting_approval";

  const reason = changed
    ? "Policy conditions satisfied; the protected destination was updated."
    : status === "blocked_unchanged"
      ? evaluation.rule.reason
      : "The protected destination remains unchanged until this exact mutation is approved.";

  return {
    caseId: demoCase.id,
    destination: demoCase.mutation.destination,
    record: demoCase.mutation.record,
    field: demoCase.mutation.field,
    before: demoCase.mutation.currentValue,
    after: changed ? demoCase.mutation.proposedValue : demoCase.mutation.currentValue,
    changed,
    status,
    reason,
  };
}
