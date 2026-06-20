import { create } from "zustand";

export interface ApprovalRequest {
  id: string;
  message: string;
  risk: "low" | "medium" | "high";
  tool?: string;
  detail?: string;
  items?: string[];
}

interface ApprovalState {
  pending: ApprovalRequest[];
  addPending: (req: ApprovalRequest) => void;
  resolve: (id: string, approved: boolean) => void;
  resolveAll: (approved: boolean) => ApprovalRequest[];
}

export const useApprovalStore = create<ApprovalState>((set, get) => ({
  pending: [],

  addPending: (req) =>
    set((s) => ({ pending: [...s.pending, req] })),

  resolve: (id, _approved) =>
    set((s) => ({ pending: s.pending.filter((r) => r.id !== id) })),

  resolveAll: (approved) => {
    const all = get().pending;
    set({ pending: [] });
    return all;
  },
}));
