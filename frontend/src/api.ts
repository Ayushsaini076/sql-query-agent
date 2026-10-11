import type { AgentResponse, Health, SchemaInfo, Step } from "./types";

export interface StreamHandlers {
  onStep: (step: Step) => void;
  onToken: (text: string) => void;
  onResult: (response: AgentResponse) => void;
  onError: (message: string) => void;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return `Request failed (${res.status} ${res.statusText}).`;
}

export async function fetchHealth(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

export async function fetchSchema(): Promise<SchemaInfo> {
  const res = await fetch("/api/schema");
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }).catch(() => undefined);
}

/** POST a message and consume the Server-Sent Events stream the backend returns. */
export async function streamChat(
  body: { session_id: string; message: string; execute: boolean },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    handlers.onError("Could not reach the server. Check your connection and try again.");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError(await errorMessage(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let gotResult = false;

  const dispatch = (raw: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (event === "step") handlers.onStep(payload);
    else if (event === "token") handlers.onToken(payload.text);
    else if (event === "result") {
      gotResult = true;
      handlers.onResult(payload);
    } else if (event === "error") {
      gotResult = true;
      handlers.onError(payload.message);
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // Normalise the accumulated buffer, not each chunk: a CRLF can be split across chunks.
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        dispatch(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    buffer = buffer.replace(/\r/g, "");
    if (buffer.trim()) dispatch(buffer);
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    handlers.onError("The connection was interrupted. Please try again.");
    return;
  }
  if (!gotResult) handlers.onError("The server closed the connection before finishing. Please try again.");
}
