import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { demoCases, getDemoCase } from "@/lib/change-control/demo";

import { DecisionWorkbench } from "../../_components/decision-workbench";

type DecisionPageProps = {
  params: Promise<{ decisionId: string }>;
};

export function generateStaticParams() {
  return demoCases.map((demoCase) => ({ decisionId: demoCase.id }));
}

export async function generateMetadata({ params }: DecisionPageProps): Promise<Metadata> {
  const { decisionId } = await params;
  const demoCase = getDemoCase(decisionId);

  return {
    title: {
      absolute: demoCase
        ? `Decision ${demoCase.id} · Writ`
        : "Decision not found · Writ",
    },
  };
}

export default async function DecisionPage({ params }: DecisionPageProps) {
  const { decisionId } = await params;
  const demoCase = getDemoCase(decisionId);

  if (!demoCase) {
    notFound();
  }

  return <DecisionWorkbench demoCase={demoCase} />;
}
