import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ControlShell } from "./_components/control-shell";
import { DemoProvider } from "./_components/demo-provider";

export const metadata: Metadata = {
  title: {
    default: "Control Center",
    template: "%s · Agent Change Control",
  },
  description:
    "A source-aware authorization gateway for changes proposed by autonomous agents.",
};

export default function ControlLayout({ children }: { children: ReactNode }) {
  return (
    <DemoProvider>
      <ControlShell>{children}</ControlShell>
    </DemoProvider>
  );
}
