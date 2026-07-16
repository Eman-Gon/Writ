"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type ReactNode,
} from "react";

export type ApprovalStatus = "pending" | "approved" | "rejected";

const DEMO_REQUEST_ID = "ACC-2051" as const;
const REQUESTED_AT = "2026-07-14T16:20:00.000Z";
const APPROVED_AT = "2026-07-14T16:24:00.000Z";
const REJECTED_AT = "2026-07-14T16:25:00.000Z";

type DemoState = {
  requestId: typeof DEMO_REQUEST_ID;
  approvalStatus: ApprovalStatus;
  requestedAt: string;
  decisionTimestamp: string | null;
};

type DemoAction =
  | { type: "approve" }
  | { type: "reject" }
  | { type: "reset" };

type DemoContextValue = DemoState & {
  approveExactChange: () => void;
  rejectExactChange: () => void;
  resetApproval: () => void;
};

const initialState: DemoState = {
  requestId: DEMO_REQUEST_ID,
  approvalStatus: "pending",
  requestedAt: REQUESTED_AT,
  decisionTimestamp: null,
};

function demoReducer(state: DemoState, action: DemoAction): DemoState {
  switch (action.type) {
    case "approve":
      return {
        ...state,
        approvalStatus: "approved",
        decisionTimestamp: APPROVED_AT,
      };
    case "reject":
      return {
        ...state,
        approvalStatus: "rejected",
        decisionTimestamp: REJECTED_AT,
      };
    case "reset":
      return initialState;
  }
}

const DemoContext = createContext<DemoContextValue | null>(null);

export function DemoProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(demoReducer, initialState);

  const approveExactChange = useCallback(() => {
    dispatch({ type: "approve" });
  }, []);

  const rejectExactChange = useCallback(() => {
    dispatch({ type: "reject" });
  }, []);

  const resetApproval = useCallback(() => {
    dispatch({ type: "reset" });
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      approveExactChange,
      rejectExactChange,
      resetApproval,
    }),
    [state, approveExactChange, rejectExactChange, resetApproval],
  );

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export function useDemoState() {
  const context = useContext(DemoContext);

  if (!context) {
    throw new Error("useDemoState must be used within a DemoProvider");
  }

  return context;
}
