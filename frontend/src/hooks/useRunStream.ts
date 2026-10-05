"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { createCase, openRunStream } from "@/lib/api";
import type { AgentName, CaseInput, ClinicalReport, RunEvent } from "@/lib/types";

export const AGENT_ORDER: AgentName[] = [
  "triage",
  "diagnostician",
  "drug_safety",
  "critique",
  "report",
];

export type AgentStatus = "waiting" | "running" | "done" | "failed";

export interface AgentAttempt {
  attempt: number;
  thoughts: string[];
  output?: Record<string, unknown>;
  durationMs?: number;
  error?: string;
}

export interface AgentProgress {
  status: AgentStatus;
  attempts: AgentAttempt[];
}

export interface Reroute {
  reason: string;
  questions: string[];
}

export interface RunState {
  status: "idle" | "starting" | "running" | "completed" | "failed";
  runId?: string;
  agents: Record<AgentName, AgentProgress>;
  reroute?: Reroute;
  report?: ClinicalReport;
  error?: string;
}

type Action =
  | { type: "start" }
  | { type: "started"; runId: string }
  | { type: "event"; event: RunEvent }
  | { type: "fail"; error: string };

function initialState(): RunState {
  const agents = Object.fromEntries(
    AGENT_ORDER.map((name) => [name, { status: "waiting", attempts: [] }]),
  ) as unknown as Record<AgentName, AgentProgress>;
  return { status: "idle", agents };
}

function updateAttempt(
  progress: AgentProgress,
  attempt: number,
  patch: (a: AgentAttempt) => AgentAttempt,
): AgentAttempt[] {
  return progress.attempts.map((a) => (a.attempt === attempt ? patch(a) : a));
}

function applyEvent(state: RunState, event: RunEvent): RunState {
  switch (event.type) {
    case "run_started":
      return { ...state, status: "running", runId: event.run_id };
    case "agent_started": {
      const progress = state.agents[event.agent];
      const agents = {
        ...state.agents,
        [event.agent]: {
          status: "running",
          attempts: [...progress.attempts, { attempt: event.attempt, thoughts: [] }],
        },
      };
      return { ...state, agents };
    }
    case "agent_thinking":
    case "agent_completed":
    case "agent_failed": {
      const progress = state.agents[event.agent];
      const attempts = updateAttempt(progress, event.attempt, (a) => {
        if (event.type === "agent_thinking") return { ...a, thoughts: [...a.thoughts, event.message] };
        if (event.type === "agent_completed")
          return { ...a, output: event.output, durationMs: event.duration_ms };
        return { ...a, error: event.error };
      });
      const status: AgentStatus =
        event.type === "agent_thinking" ? "running" : event.type === "agent_completed" ? "done" : "failed";
      return { ...state, agents: { ...state.agents, [event.agent]: { status, attempts } } };
    }
    case "reroute":
      return { ...state, reroute: { reason: event.reason, questions: event.questions } };
    case "report":
      return { ...state, report: event.report };
    case "error":
      return { ...state, error: event.message };
    case "done":
      return { ...state, status: event.status };
  }
}

function reducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case "start":
      return { ...initialState(), status: "starting" };
    case "started":
      return { ...state, status: "running", runId: action.runId };
    case "event":
      return applyEvent(state, action.event);
    case "fail":
      return { ...state, status: "failed", error: action.error };
  }
}

export function useRunStream() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const closeRef = useRef<(() => void) | null>(null);

  useEffect(() => () => closeRef.current?.(), []);

  const analyze = useCallback(async (input: CaseInput) => {
    closeRef.current?.();
    dispatch({ type: "start" });
    try {
      const runId = await createCase(input);
      dispatch({ type: "started", runId });
      closeRef.current = openRunStream(
        runId,
        (event) => dispatch({ type: "event", event }),
        () => dispatch({ type: "fail", error: "Lost connection to the analysis server." }),
      );
    } catch (err) {
      dispatch({ type: "fail", error: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  return { state, analyze };
}
