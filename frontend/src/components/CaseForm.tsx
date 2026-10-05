"use client";

import { type FormEvent, type KeyboardEvent, useState } from "react";

import type { CaseInput, Consciousness } from "@/lib/types";

type VitalKey = "heart_rate" | "systolic_bp" | "diastolic_bp" | "respiratory_rate" | "spo2" | "temperature_c";

interface FormState {
  complaint: string;
  vitals: Record<VitalKey, string>;
  consciousness: Consciousness;
  onO2: boolean;
  age: string;
  sex: "" | "male" | "female" | "other";
  medications: string[];
}

const EMPTY_VITALS: Record<VitalKey, string> = {
  heart_rate: "",
  systolic_bp: "",
  diastolic_bp: "",
  respiratory_rate: "",
  spo2: "",
  temperature_c: "",
};

const EMPTY: FormState = {
  complaint: "",
  vitals: EMPTY_VITALS,
  consciousness: "A",
  onO2: false,
  age: "",
  sex: "",
  medications: [],
};

const VITAL_FIELDS: { key: VitalKey; label: string; unit: string; step?: string }[] = [
  { key: "heart_rate", label: "HR", unit: "bpm" },
  { key: "systolic_bp", label: "SBP", unit: "mmHg" },
  { key: "diastolic_bp", label: "DBP", unit: "mmHg" },
  { key: "respiratory_rate", label: "RR", unit: "/min" },
  { key: "spo2", label: "SpO₂", unit: "%" },
  { key: "temperature_c", label: "Temp", unit: "°C", step: "0.1" },
];

const SAMPLES: { label: string; form: FormState }[] = [
  {
    label: "Chest pain on warfarin",
    form: {
      ...EMPTY,
      complaint: "Crushing central chest pain radiating to the left arm for 1 hour, sweating and nausea.",
      vitals: { heart_rate: "118", systolic_bp: "95", diastolic_bp: "60", respiratory_rate: "24", spo2: "93", temperature_c: "37.1" },
      age: "64",
      sex: "male",
      medications: ["Coumadin 5mg", "aspirin 75mg", "simvastatin 40mg", "clarithromycin"],
    },
  },
  {
    label: "Fever and cough",
    form: {
      ...EMPTY,
      complaint: "Fever and productive cough for 3 days with right-sided pleuritic pain.",
      vitals: { heart_rate: "104", systolic_bp: "118", diastolic_bp: "74", respiratory_rate: "22", spo2: "94", temperature_c: "38.9" },
      age: "47",
      sex: "female",
      medications: ["lisinopril", "spironolactone"],
    },
  },
  {
    label: "Vague (triggers critique re-route)",
    form: {
      ...EMPTY,
      complaint: "Feeling generally unwell and tired for two weeks.",
      vitals: { ...EMPTY_VITALS, heart_rate: "88", temperature_c: "37.4" },
      age: "35",
      sex: "other",
    },
  },
];

function toNumber(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toCaseInput(form: FormState): CaseInput {
  return {
    chief_complaint: form.complaint.trim(),
    vitals: {
      heart_rate: toNumber(form.vitals.heart_rate),
      systolic_bp: toNumber(form.vitals.systolic_bp),
      diastolic_bp: toNumber(form.vitals.diastolic_bp),
      respiratory_rate: toNumber(form.vitals.respiratory_rate),
      spo2: toNumber(form.vitals.spo2),
      temperature_c: toNumber(form.vitals.temperature_c),
      consciousness: form.consciousness,
      on_supplemental_o2: form.onO2,
    },
    medications: form.medications,
    age: toNumber(form.age),
    sex: form.sex || null,
  };
}

const inputClass =
  "w-full rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 dark:border-zinc-700 dark:bg-zinc-900";
const labelClass = "mb-1 block text-xs font-medium text-zinc-600 dark:text-zinc-400";

export function CaseForm({ busy, onSubmit }: { busy: boolean; onSubmit: (input: CaseInput) => void }) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [medDraft, setMedDraft] = useState("");

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const addMedication = () => {
    const names = medDraft
      .split(",")
      .map((m) => m.trim())
      .filter(Boolean);
    if (names.length) {
      setForm((f) => ({
        ...f,
        medications: [...f.medications, ...names.filter((n) => !f.medications.includes(n))],
      }));
    }
    setMedDraft("");
  };

  const onMedKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addMedication();
    } else if (e.key === "Backspace" && medDraft === "" && form.medications.length) {
      set("medications", form.medications.slice(0, -1));
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (form.complaint.trim().length < 3) return;
    onSubmit(toCaseInput(form));
  };

  return (
    <form
      onSubmit={submit}
      className="no-print space-y-4 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Patient case</h2>
        <div className="flex flex-wrap gap-1.5">
          {SAMPLES.map((s) => (
            <button
              key={s.label}
              type="button"
              onClick={() => setForm(s.form)}
              className="rounded-full border border-zinc-300 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label htmlFor="complaint" className={labelClass}>
          Chief complaint
        </label>
        <textarea
          id="complaint"
          rows={3}
          required
          minLength={3}
          value={form.complaint}
          onChange={(e) => set("complaint", e.target.value)}
          placeholder="Presenting symptoms, onset, relevant history…"
          className={inputClass}
        />
      </div>

      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {VITAL_FIELDS.map((v) => (
          <div key={v.key}>
            <label htmlFor={v.key} className={labelClass}>
              {v.label} <span className="font-normal text-zinc-400">{v.unit}</span>
            </label>
            <input
              id={v.key}
              type="number"
              inputMode="decimal"
              step={v.step ?? "1"}
              value={form.vitals[v.key]}
              onChange={(e) => set("vitals", { ...form.vitals, [v.key]: e.target.value })}
              className={inputClass}
            />
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <label htmlFor="consciousness" className={labelClass}>
            Consciousness (ACVPU)
          </label>
          <select
            id="consciousness"
            value={form.consciousness}
            onChange={(e) => set("consciousness", e.target.value as Consciousness)}
            className={inputClass}
          >
            <option value="A">Alert</option>
            <option value="C">New confusion</option>
            <option value="V">Responds to voice</option>
            <option value="P">Responds to pain</option>
            <option value="U">Unresponsive</option>
          </select>
        </div>
        <div>
          <label htmlFor="age" className={labelClass}>
            Age
          </label>
          <input
            id="age"
            type="number"
            min={0}
            max={120}
            value={form.age}
            onChange={(e) => set("age", e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="sex" className={labelClass}>
            Sex
          </label>
          <select
            id="sex"
            value={form.sex}
            onChange={(e) => set("sex", e.target.value as FormState["sex"])}
            className={inputClass}
          >
            <option value="">—</option>
            <option value="female">Female</option>
            <option value="male">Male</option>
            <option value="other">Other</option>
          </select>
        </div>
        <label className="flex items-end gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            checked={form.onO2}
            onChange={(e) => set("onO2", e.target.checked)}
            className="h-4 w-4 accent-teal-600"
          />
          On supplemental O₂
        </label>
      </div>

      <div>
        <label htmlFor="meds" className={labelClass}>
          Current medications <span className="font-normal text-zinc-400">(Enter or comma to add)</span>
        </label>
        <div className={`${inputClass} flex flex-wrap items-center gap-1.5`}>
          {form.medications.map((m) => (
            <span
              key={m}
              className="flex items-center gap-1 rounded bg-teal-50 px-2 py-0.5 text-xs text-teal-800 dark:bg-teal-950 dark:text-teal-200"
            >
              {m}
              <button
                type="button"
                aria-label={`Remove ${m}`}
                onClick={() => set("medications", form.medications.filter((x) => x !== m))}
                className="text-teal-600 hover:text-teal-900 dark:hover:text-teal-100"
              >
                ×
              </button>
            </span>
          ))}
          <input
            id="meds"
            value={medDraft}
            onChange={(e) => setMedDraft(e.target.value)}
            onKeyDown={onMedKey}
            onBlur={addMedication}
            placeholder={form.medications.length ? "" : "e.g. warfarin"}
            className="min-w-24 flex-1 bg-transparent outline-none"
          />
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={busy || form.complaint.trim().length < 3}
          className="rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Analyzing…" : "Analyze case →"}
        </button>
      </div>
    </form>
  );
}
