"use client";

import { AgentTimeline } from "@/components/AgentTimeline";
import { CaseForm } from "@/components/CaseForm";
import { Header } from "@/components/Header";
import { ReportPanel } from "@/components/ReportPanel";
import { useRunStream } from "@/hooks/useRunStream";

export default function Home() {
  const { state, analyze } = useRunStream();
  const busy = state.status === "starting" || state.status === "running";

  return (
    <>
      <Header />
      <main className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <CaseForm busy={busy} onSubmit={analyze} />
          {state.status !== "idle" && <AgentTimeline state={state} />}
        </div>
        <div>
          {state.report ? (
            <ReportPanel report={state.report} />
          ) : (
            <div className="no-print grid h-full min-h-48 place-items-center rounded-xl border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">
              {busy
                ? "Agents are working — the report appears when they finish."
                : "Enter a case (or pick a sample) and click Analyze to see the agents reason in real time."}
            </div>
          )}
        </div>
      </main>
    </>
  );
}
