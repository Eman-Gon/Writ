import type { Metadata } from "next";

import { OverviewClient } from "./_components/overview-client";

export const metadata: Metadata = {
  title: { absolute: "Control Center · Agent Change Control" },
  description: "Review source-aware policy decisions for protected agent changes.",
};

export default function ControlOverviewPage() {
  return <OverviewClient />;
}
