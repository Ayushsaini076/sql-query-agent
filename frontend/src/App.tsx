import { ArrowUp, Database, Menu, Moon, PanelRight, Square, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { deleteSession, fetchHealth, fetchSchema, streamChat } from "./api";
import { EmptyState } from "./components/EmptyState";
import { Inspector } from "./components/Inspector";
import { MessageBubble } from "./components/MessageBubble";
import { ResizeHandle } from "./components/ResizeHandle";
import { SchemaDrawer } from "./components/SchemaDrawer";
import { Sidebar } from "./components/Sidebar";
import { loadConversations, newId, readPref, saveConversations, writePref } from "./storage";
import type { ChatMessage, Conversation, Health, SchemaInfo } from "./types";

const MAX_CHARS = 4000;
const MIN_CHAT_WIDTH = 400; // the chat column never gets narrower than this while resizing
const MAX_SIDEBAR_WIDTH = 480;
const MOBILE_QUERY = "(max-width: 760px)"; // keep in sync with styles.css: the sidebar is a drawer here

function readWidth(key: string): number | null {
  return Number(readPref(key, "")) || null;
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string>(() => conversations[0]?.id ?? newId());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [execute, setExecute] = useState(() => readPref("execute", "on") === "on");
  const [dark, setDark] = useState(() => {
    const t = readPref<string>("theme", "");
    return t ? t === "dark" : systemPrefersDark();
  });
  const [health, setHealth] = useState<Health | null>(null);
  const [schema, setSchema] = useState<SchemaInfo | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [showSchema, setShowSchema] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile drawer
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => readPref<string>("sidebar", "open") === "collapsed"); // desktop
  const [detailsOpen, setDetailsOpen] = useState(false); // small screens only
  // Panel widths in px chosen by dragging; null = the responsive default from styles.css.
  const [sidebarWidth, setSidebarWidth] = useState(() => readWidth("sidebarWidth"));
  const [inspectorWidth, setInspectorWidth] = useState(() => readWidth("inspectorWidth"));
  const layoutRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLElement>(null);
  const inspectorRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const active = conversations.find((c) => c.id === activeId);
  const messages = active?.messages ?? [];

  // ---------------------------------------------------------------- effects
  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth({ status: "down", llm_ready: false, provider: "", model: "" }));
    fetchSchema().then(setSchema).catch((e: Error) => setSchemaError(e.message));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => saveConversations(conversations), 300);
    return () => clearTimeout(t);
  }, [conversations]);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.steps?.length, messages[messages.length - 1]?.streamText]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  // --------------------------------------------------------------- selection
  const selected = useMemo(() => {
    const idx = selectedId
      ? messages.findIndex((m) => m.id === selectedId)
      : messages.map((m) => m.response?.type === "sql" || m.response?.type === "error").lastIndexOf(true);
    if (idx < 0) return { response: undefined, question: undefined, id: null as string | null };
    const question = messages.slice(0, idx).reverse().find((m) => m.role === "user")?.content;
    return { response: messages[idx].response, question, id: messages[idx].id };
  }, [messages, selectedId]);

  // ----------------------------------------------------------------- updates
  const patchMessage = useCallback((convId: string, msgId: string, fn: (m: ChatMessage) => ChatMessage) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId ? { ...c, updatedAt: Date.now(), messages: c.messages.map((m) => (m.id === msgId ? fn(m) : m)) } : c,
      ),
    );
  }, []);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      const convId = activeId;
      const userMsg: ChatMessage = { id: newId(), role: "user", content: message };
      const botMsg: ChatMessage = { id: newId(), role: "assistant", content: "", steps: [], pending: true };

      setConversations((prev) => {
        const exists = prev.some((c) => c.id === convId);
        const now = Date.now();
        const next = exists
          ? prev.map((c) => (c.id === convId ? { ...c, updatedAt: now, messages: [...c.messages, userMsg, botMsg] } : c))
          : [{ id: convId, title: message.slice(0, 60), createdAt: now, updatedAt: now, messages: [userMsg, botMsg] }, ...prev];
        return next.sort((a, b) => b.updatedAt - a.updatedAt);
      });
      setInput("");
      setSelectedId(null);
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;
      await streamChat(
        { session_id: convId, message, execute },
        {
          onStep: (step) => patchMessage(convId, botMsg.id, (m) => ({ ...m, steps: [...(m.steps ?? []), step] })),
          onToken: (t) => patchMessage(convId, botMsg.id, (m) => ({ ...m, streamText: (m.streamText ?? "") + t })),
          onResult: (response) =>
            patchMessage(convId, botMsg.id, (m) => ({ ...m, response, pending: false, streamText: undefined })),
          onError: (error) => patchMessage(convId, botMsg.id, (m) => ({ ...m, error, pending: false, streamText: undefined })),
        },
        controller.signal,
      );
      patchMessage(convId, botMsg.id, (m) =>
        m.pending ? { ...m, pending: false, error: m.error ?? "Request cancelled." } : m,
      );
      abortRef.current = null;
      setBusy(false);
      inputRef.current?.focus();
    },
    [activeId, busy, execute, patchMessage],
  );

  const retry = (assistantId: string) => {
    const idx = messages.findIndex((m) => m.id === assistantId);
    const question = messages.slice(0, idx).reverse().find((m) => m.role === "user")?.content;
    if (question) send(question);
  };

  const newChat = () => {
    abortRef.current?.abort();
    setActiveId(newId());
    setSelectedId(null);
    setSidebarOpen(false);
    inputRef.current?.focus();
  };

  const removeConversation = (id: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    deleteSession(id);
    if (id === activeId) newChat();
  };

  const toggleExecute = () => {
    setExecute((v) => {
      writePref("execute", v ? "off" : "on");
      return !v;
    });
  };

  const toggleTheme = () => {
    setDark((d) => {
      writePref("theme", d ? "light" : "dark");
      return !d;
    });
  };

  const sidebarEl = () => layoutRef.current?.querySelector<HTMLElement>(".sidebar") ?? null;
  // A panel may take whatever the chat column can give up without dropping below MIN_CHAT_WIDTH.
  const maxWidth = (panel: HTMLElement | null) =>
    (panel?.offsetWidth ?? 0) + (chatRef.current?.offsetWidth ?? 0) - MIN_CHAT_WIDTH;

  // Percent caps keep saved widths sane when the window is later made smaller.
  const layoutStyle = {
    ...(sidebarWidth && { "--sidebar-w": `min(${sidebarWidth}px, 35%)` }),
    ...(sidebarCollapsed && { "--sidebar-w": "0px" }),
    ...(inspectorWidth && { "--inspector-w": `min(${inspectorWidth}px, 65%)` }),
  } as CSSProperties;

  const toggleSidebar = () => {
    if (window.matchMedia(MOBILE_QUERY).matches) {
      setSidebarOpen((o) => !o);
      return;
    }
    setSidebarCollapsed((c) => {
      writePref("sidebar", c ? "open" : "collapsed");
      return !c;
    });
  };

  const selectMessage = (id: string) => {
    setSelectedId(id);
    setDetailsOpen(true);
  };

  // ------------------------------------------------------------------ render
  return (
    <div className="app">
      <header className="topbar">
        <button
          className="icon-btn only"
          onClick={toggleSidebar}
          aria-label={sidebarCollapsed ? "Show history" : "Hide history"}
          title={sidebarCollapsed ? "Show history" : "Hide history"}
          aria-controls="sidebar"
        >
          <Menu size={20} />
        </button>
        <div className="brand">
          <span className="logo" aria-hidden>
            <Database size={16} />
          </span>
          <span className="brand-name">SQL Query Agent</span>
          <span className="badge">Acme Retail · SQLite</span>
        </div>
        <div className="topbar-actions">
          <label className="switch" title="Execute generated queries against the sample database (read-only)">
            <input type="checkbox" checked={execute} onChange={toggleExecute} />
            <span className="track" />
            <span className="switch-label">Run queries</span>
          </label>
          <button className="icon-btn only" onClick={toggleTheme} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
            {dark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="icon-btn only tablet-only" onClick={() => setDetailsOpen((o) => !o)} aria-label="Toggle query details">
            <PanelRight size={18} />
          </button>
        </div>
      </header>

      <div className={`layout${sidebarCollapsed ? " sidebar-collapsed" : ""}`} ref={layoutRef} style={layoutStyle}>
        <Sidebar
          conversations={conversations}
          activeId={activeId}
          health={health}
          open={sidebarOpen}
          onNew={newChat}
          onSelect={(id) => {
            setActiveId(id);
            setSelectedId(null);
            setSidebarOpen(false);
          }}
          onDelete={removeConversation}
          onShowSchema={() => {
            setShowSchema(true);
            setSidebarOpen(false);
          }}
        />
        <ResizeHandle
          className="sidebar-handle"
          direction="grow"
          label="Resize history panel"
          min={180}
          getPanel={sidebarEl}
          getMax={() => Math.min(MAX_SIDEBAR_WIDTH, maxWidth(sidebarEl()))}
          onResize={setSidebarWidth}
          onCommit={(w) => writePref("sidebarWidth", String(w))}
          onReset={() => {
            setSidebarWidth(null);
            writePref("sidebarWidth", "");
          }}
        />
        {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} />}

        <main className="chat" ref={chatRef}>
          {health && !health.llm_ready && (
            <div className="banner">
              The server has no LLM API key configured, so questions can't be answered yet. See the README.
            </div>
          )}
          <div className="messages" ref={scrollRef}>
            {messages.length === 0 ? (
              <EmptyState onPick={send} />
            ) : (
              <div className="messages-inner">
                {messages.map((m) => (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    selected={m.id === selected.id}
                    onSelect={() => selectMessage(m.id)}
                    onRetry={!busy ? () => retry(m.id) : undefined}
                  />
                ))}
              </div>
            )}
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <div className="composer-box">
              <textarea
                ref={inputRef}
                value={input}
                maxLength={MAX_CHARS}
                rows={1}
                placeholder="Ask about the data, or paste SQL to optimize or debug…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                aria-label="Message"
              />
              {busy ? (
                <button type="button" className="send stop" onClick={() => abortRef.current?.abort()} aria-label="Stop">
                  <Square size={14} />
                </button>
              ) : (
                <button type="submit" className="send" disabled={!input.trim()} aria-label="Send">
                  <ArrowUp size={18} />
                </button>
              )}
            </div>
            <p className="composer-hint">
              Read-only: the assistant never modifies data. Enter to send, Shift+Enter for a new line.
            </p>
          </form>
        </main>

        <ResizeHandle
          className="inspector-handle"
          direction="shrink"
          label="Resize query details panel"
          min={300}
          getPanel={() => inspectorRef.current}
          getMax={() => maxWidth(inspectorRef.current)}
          onResize={setInspectorWidth}
          onCommit={(w) => writePref("inspectorWidth", String(w))}
          onReset={() => {
            setInspectorWidth(null);
            writePref("inspectorWidth", "");
          }}
        />
        <div className={`inspector-wrap${detailsOpen ? " open" : ""}`} ref={inspectorRef}>
          <Inspector response={selected.response} question={selected.question} onClose={() => setDetailsOpen(false)} />
        </div>
      </div>

      {showSchema && <SchemaDrawer schema={schema} error={schemaError} onClose={() => setShowSchema(false)} />}
    </div>
  );
}
