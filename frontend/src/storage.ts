import type { Conversation } from "./types";

const KEY = "sqlagent.conversations.v1";
const MAX_CONVERSATIONS = 30;

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as Conversation[]) : [];
    // Anything still pending was interrupted by a reload.
    return parsed.map((c) => ({
      ...c,
      messages: c.messages.map((m) =>
        m.pending ? { ...m, pending: false, error: m.error ?? "This request was interrupted." } : m,
      ),
    }));
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)));
  } catch {
    /* storage full or unavailable – history just won't persist */
  }
}

export function readPref<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(`sqlagent.${key}`) as T) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(`sqlagent.${key}`, value);
  } catch {
    /* ignore */
  }
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
