import type {
  ClinicalReport,
  DrugInteraction,
  MedicationMatch,
  Severity,
  Urgency,
} from "@/lib/types";

const URGENCY_STYLE: Record<Urgency, string> = {
  LOW: "bg-emerald-600",
  MEDIUM: "bg-amber-500",
  HIGH: "bg-red-600",
};

const SEVERITY_STYLE: Record<Severity, string> = {
  minor: "bg-zinc-200 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-100",
  moderate: "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100",
  major: "bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100",
  unknown: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
};

const MATCH_LABEL: Record<NonNullable<MedicationMatch["method"]>, string> = {
  exact: "exact",
  synonym: "synonym",
  rxnorm: "RxNorm",
  fuzzy: "spelling match",
};

const pct = (x: number) => `${Math.round(x * 100)}%`;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">{title}</h3>
      {children}
    </section>
  );
}

function pairLabel(it: DrugInteraction, side: "a" | "b") {
  const input = side === "a" ? it.input_a : it.input_b;
  const drug = side === "a" ? it.drug_a : it.drug_b;
  // Show the DDInter name only when it differs from what was typed (e.g. aspirin -> acetylsalicylic acid).
  return input.toLowerCase().startsWith(drug) ? input : `${input} (${drug})`;
}

function InteractionItem({ it }: { it: DrugInteraction }) {
  return (
    <li className="flex items-start gap-3">
      <span
        className={`mt-0.5 shrink-0 rounded px-2 py-0.5 text-xs font-semibold capitalize ${SEVERITY_STYLE[it.severity]}`}
      >
        {it.severity}
      </span>
      <div className="min-w-0 text-sm">
        <span className="font-medium">
          {pairLabel(it, "a")} + {pairLabel(it, "b")}
        </span>
        {it.explanation && <p className="text-zinc-600 dark:text-zinc-400">{it.explanation}</p>}
        {it.clinical_action && (
          <p className="text-zinc-700 dark:text-zinc-300">
            <span className="font-medium">Action:</span> {it.clinical_action}
          </p>
        )}
        <p className="text-xs text-zinc-400">
          Severity: {it.source} ({it.source_ids.join(", ")})
          {it.explanation_source === "llm" && " · explanation AI-generated"}
        </p>
      </div>
    </li>
  );
}

function MedicationMatches({ matches }: { matches: MedicationMatch[] }) {
  if (matches.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5 text-xs">
      {matches.map((m) => (
        <li
          key={m.input}
          className={`rounded border px-2 py-0.5 ${
            m.resolved.length
              ? "border-zinc-200 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"
              : "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300"
          }`}
          title={m.method ? `Matched by ${MATCH_LABEL[m.method]}` : "Not found in DDInter"}
        >
          {m.resolved.length
            ? m.resolved.length === 1 && m.input.toLowerCase().startsWith(m.resolved[0])
              ? m.input // only dose/form was stripped
              : `${m.input} → ${m.resolved.join(" + ")}`
            : `${m.input} — not recognised`}
        </li>
      ))}
    </ul>
  );
}

export function ReportPanel({ report }: { report: ClinicalReport }) {
  const { triage, critique } = report;
  const graded = report.drug_interactions.filter((i) => i.severity !== "unknown");
  const ungraded = report.drug_interactions.filter((i) => i.severity === "unknown");
  const unresolved = report.medication_matches.some((m) => m.resolved.length === 0);

  return (
    <article className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className={`${URGENCY_STYLE[report.urgency]} px-5 py-3 text-white`}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-lg font-semibold">{report.urgency} urgency</span>
          <span className="text-sm opacity-90">
            NEWS2 {triage.news2_score} · {triage.method === "llm" ? "AI + NEWS2" : "rules"}
          </span>
        </div>
        <p className="text-sm opacity-90">{triage.reasoning}</p>
        {triage.red_flags.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {triage.red_flags.map((f) => (
              <li key={f} className="rounded bg-white/20 px-2 py-0.5 text-xs">
                ⚑ {f}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-6 p-5">
        <Section title="Differential diagnoses">
          <ol className="space-y-3">
            {report.diagnoses.map((d, i) => (
              <li key={d.condition}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">
                    {i + 1}. {d.condition}
                    {d.icd11_code && (
                      <span className="ml-2 font-mono text-xs text-zinc-500">ICD-11 {d.icd11_code}</span>
                    )}
                  </span>
                  <span className="text-sm tabular-nums text-zinc-500">{pct(d.confidence)}</span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-zinc-100 dark:bg-zinc-800">
                  <div className="h-2 rounded-full bg-teal-500" style={{ width: pct(d.confidence) }} />
                </div>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{d.rationale}</p>
                {d.citations.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-xs">
                    {d.citations.map((c) => (
                      <li key={c.id}>
                        {c.url ? (
                          <a href={c.url} target="_blank" rel="noreferrer" className="text-teal-700 underline dark:text-teal-400">
                            {c.title}
                          </a>
                        ) : (
                          c.title
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        </Section>

        <Section title="Drug interaction alerts">
          <MedicationMatches matches={report.medication_matches} />
          {graded.length === 0 && ungraded.length === 0 ? (
            <p className="text-sm text-zinc-500">No interactions found among the listed medications.</p>
          ) : (
            <ul className="space-y-3">
              {graded.map((it) => (
                <InteractionItem key={`${it.drug_a}-${it.drug_b}`} it={it} />
              ))}
            </ul>
          )}
          {ungraded.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-zinc-500">
                {ungraded.length} interaction(s) listed in DDInter without a severity grade
              </summary>
              <ul className="mt-2 space-y-2">
                {ungraded.map((it) => (
                  <InteractionItem key={`${it.drug_a}-${it.drug_b}`} it={it} />
                ))}
              </ul>
            </details>
          )}
          {unresolved && (
            <p className="text-xs text-red-700 dark:text-red-300">
              Unrecognised medications were not checked for interactions.
            </p>
          )}
        </Section>

        <Section title="Critique">
          <div className="flex items-center gap-3">
            <span className="text-2xl font-semibold tabular-nums">{pct(critique.confidence_score)}</span>
            <span className="text-sm text-zinc-500">
              overall confidence{report.reroutes > 0 && ` · after ${report.reroutes} re-route`}
            </span>
          </div>
          <p className="text-sm">{critique.summary}</p>
          {critique.flags.length > 0 && (
            <ul className="list-inside list-disc text-sm text-amber-800 dark:text-amber-300">
              {critique.flags.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </Section>

        <footer className="flex items-center justify-between gap-4 border-t border-zinc-200 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
          <span>
            {report.disclaimer} Generated {new Date(report.generated_at).toLocaleString()}.
          </span>
          <button
            onClick={() => window.print()}
            className="no-print shrink-0 rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            Print / Save PDF
          </button>
        </footer>
      </div>
    </article>
  );
}
