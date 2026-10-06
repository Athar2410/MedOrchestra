"use client";

import { useEffect, useState } from "react";

import { listRuns } from "@/lib/api";
import type { RunSummary, Urgency } from "@/lib/types";

const URGENCY_DOT: Record<Urgency, string> = {
  LOW: "bg-emerald-500",
  MEDIUM: "bg-amber-500",
  HIGH: "bg-red-600",
};

/** Saved cases from Supabase; `refreshKey` changes when a new run finishes. */
export function RunHistory({
  refreshKey,
  activeRunId,
  onOpen,
}: {
  refreshKey: string;
  activeRunId?: string;
  onOpen: (runId: string) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    listRuns().then(setRuns).catch(() => setRuns([]));
  }, [refreshKey]);

  if (runs.length === 0) return null;
  return (
    <section className="no-print rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-base font-semibold">Recent cases</h2>
      <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
        {runs.map((r) => (
          <li key={r.run_id}>
            <button
              onClick={() => onOpen(r.run_id)}
              className={`flex w-full items-start gap-3 py-2 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/50 ${
                r.run_id === activeRunId ? "font-medium" : ""
              }`}
            >
              <span
                className={`mt-1.5 block h-2 w-2 shrink-0 rounded-full ${r.urgency ? URGENCY_DOT[r.urgency] : "bg-zinc-400"}`}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{r.chief_complaint}</span>
                <span className="block truncate text-xs text-zinc-500">
                  {r.status === "failed" ? "failed" : (r.top_diagnosis ?? "no diagnosis")} ·{" "}
                  {new Date(r.created_at).toLocaleString()}
                  {r.duration_ms != null && ` · ${(r.duration_ms / 1000).toFixed(1)}s`}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
