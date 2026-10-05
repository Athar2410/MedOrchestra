import type { CaseInput, RunEvent } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function createCase(input: CaseInput): Promise<string> {
  const res = await fetch(`${API_URL}/api/cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Failed to start analysis (${res.status}): ${detail}`);
  }
  const { run_id } = (await res.json()) as { run_id: string };
  return run_id;
}

/**
 * Subscribes to a run's SSE stream. EventSource reconnects on its own and the
 * server resumes from Last-Event-ID, so we only close it once the run is done.
 * Returns a function that closes the stream.
 */
export function openRunStream(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onConnectionError: () => void,
): () => void {
  const source = new EventSource(`${API_URL}/api/runs/${runId}/stream`);
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data) as RunEvent;
    onEvent(event);
    if (event.type === "done") source.close();
  };
  source.onerror = () => {
    // CLOSED means the browser gave up (e.g. backend gone); CONNECTING is a normal retry.
    if (source.readyState === EventSource.CLOSED) onConnectionError();
  };
  return () => source.close();
}
