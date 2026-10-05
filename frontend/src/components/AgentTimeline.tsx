"use client";

import {
  AGENT_ORDER,
  type AgentAttempt,
  type AgentStatus,
  type Reroute,
  type RunState,
} from "@/hooks/useRunStream";
import type { AgentName, Critique, Diagnosis, DrugInteraction, TriageResult } from "@/lib/types";

const AGENT_META: Record<AgentName, { label: string; role: string }> = {
  triage: { label: "Triage", role: "NEWS2 + red-flag assessment → urgency" },
  diagnostician: { label: "Diagnostician", role: "PubMed-grounded differential diagnosis" },
  drug_safety: { label: "Drug Safety", role: "Drug–drug interaction screening" },
  critique: { label: "Critique", role: "Adversarial review of the differential" },
  report: { label: "Report", role: "Final clinical summary" },
};

function StatusIcon({ status }: { status: AgentStatus }) {
  const base = "block h-3 w-3 rounded-full";
  if (status === "running")
    return <span className={`${base} animate-spin border-2 border-teal-500 border-t-transparent`} />;
  if (status === "done") return <span className={`${base} bg-teal-500`} />;
  if (status === "failed") return <span className={`${base} bg-red-500`} />;
  return <span className={`${base} border-2 border-zinc-300 dark:border-zinc-600`} />;
}

function summarize(agent: AgentName, output: Record<string, unknown>): string {
  switch (agent) {
    case "triage": {
      const t = output.triage as TriageResult;
      const via = t.method === "llm" ? "AI + NEWS2" : "rules";
      return `${t.urgency} urgency · NEWS2 ${t.news2_score} · ${via}`;
    }
    case "diagnostician": {
      const d = output.diagnoses as Diagnosis[];
      const evidence = (output.evidence as unknown[] | undefined)?.length ?? 0;
      if (!d.length) return "No differential produced";
      const list = d.map((x) => `${x.condition} (${Math.round(x.confidence * 100)}%)`).join(" · ");
      return `${list} · ${evidence} abstract(s)`;
    }
    case "drug_safety": {
      const i = output.drug_interactions as DrugInteraction[];
      if (!i.length) return "No interactions found";
      const major = i.filter((x) => x.severity === "major").length;
      return `${i.length} interaction(s)${major ? ` · ${major} major` : ""}`;
    }
    case "critique": {
      const c = output.critique as Critique;
      return `Confidence ${Math.round(c.confidence_score * 100)}% · ${c.flags.length} flag(s)${c.reroute_requested ? " · re-route requested" : ""}`;
    }
    case "report":
      return "Report ready";
  }
}

function AttemptView({ agent, attempt, latest }: { agent: AgentName; attempt: AgentAttempt; latest: boolean }) {
  return (
    <div className={latest ? "" : "opacity-60"}>
      {attempt.attempt > 1 && (
        <span className="mb-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          Attempt {attempt.attempt}
        </span>
      )}
      {!attempt.output && !attempt.error && attempt.thoughts.length > 0 && (
        <p className="text-sm italic text-zinc-500">{attempt.thoughts.at(-1)}…</p>
      )}
      {attempt.output && (
        <details className="group">
          <summary className="cursor-pointer text-sm text-zinc-700 marker:text-zinc-400 dark:text-zinc-300">
            {summarize(agent, attempt.output)}
            {attempt.durationMs !== undefined && (
              <span className="ml-2 text-xs text-zinc-400">{(attempt.durationMs / 1000).toFixed(1)}s</span>
            )}
          </summary>
          <ul className="mt-1 list-inside list-disc text-xs text-zinc-500">
            {attempt.thoughts.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-zinc-100 p-2 text-[11px] dark:bg-zinc-950">
            {JSON.stringify(attempt.output, null, 2)}
          </pre>
        </details>
      )}
      {attempt.error && <p className="text-sm text-red-600">Failed: {attempt.error}</p>}
    </div>
  );
}

function RerouteCallout({ reroute }: { reroute: Reroute }) {
  return (
    <div className="ml-6 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/40">
      <p className="font-medium text-amber-900 dark:text-amber-200">↺ Critique sent the case back to the Diagnostician</p>
      <p className="text-amber-800 dark:text-amber-300">{reroute.reason}</p>
      {reroute.questions.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-amber-800 dark:text-amber-300">
          {reroute.questions.map((q) => (
            <li key={q}>{q}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AgentTimeline({ state }: { state: RunState }) {
  return (
    <section className="no-print rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-base font-semibold">Agent reasoning</h2>
      <ol className="space-y-3">
        {AGENT_ORDER.map((name) => {
          const progress = state.agents[name];
          return (
            <li key={name} className="space-y-2">
              <div className="flex gap-3">
                <div className="pt-1.5">
                  <StatusIcon status={progress.status} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{AGENT_META[name].label}</span>
                    <span className="text-xs text-zinc-400">
                      {progress.status === "waiting" ? "waiting" : AGENT_META[name].role}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {progress.attempts.map((a, i) => (
                      <AttemptView
                        key={a.attempt}
                        agent={name}
                        attempt={a}
                        latest={i === progress.attempts.length - 1}
                      />
                    ))}
                  </div>
                </div>
              </div>
              {name === "critique" && state.reroute && <RerouteCallout reroute={state.reroute} />}
            </li>
          );
        })}
      </ol>
      {state.error && <p className="mt-3 text-sm text-red-600">{state.error}</p>}
    </section>
  );
}
