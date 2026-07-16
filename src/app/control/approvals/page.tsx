import type { Metadata } from "next";

import {
  ApprovalReview,
  type ApprovalReviewCase,
} from "@/app/control/_components/approval-review";
import { evaluateCase, getDemoCase } from "@/lib/change-control/demo";

export const metadata: Metadata = {
  title: { absolute: "Approvals · Agent Change Control" },
  description: "Review exact-value approvals for protected agent changes.",
};

export default function ApprovalsPage() {
  const demoCase = getDemoCase("ACC-2051");
  const evaluation = evaluateCase(demoCase);
  const approvalCase: ApprovalReviewCase = {
    requestId: demoCase.id,
    title: demoCase.title,
    summary: demoCase.summary,
    requester: demoCase.actors.user.label,
    source: demoCase.source.label,
    destination: demoCase.mutation.destination,
    record: demoCase.mutation.record,
    field: demoCase.mutation.field,
    currentValue: demoCase.mutation.currentValue,
    proposedValue: demoCase.mutation.proposedValue,
    policyId: evaluation.rule.id,
    policyName: evaluation.rule.name,
    policyReason: evaluation.rule.reason,
    evidence: demoCase.evidence,
  };

  return <ApprovalReview approvalCase={approvalCase} />;
}
