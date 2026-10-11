import { Database, MessageSquare, Plus, Trash2 } from "lucide-react";
import type { Conversation, Health } from "../types";

interface Props {
  conversations: Conversation[];
  activeId: string;
  health: Health | null;
  open: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onShowSchema: () => void;
}

export function Sidebar({ conversations, activeId, health, open, onNew, onSelect, onDelete, onShowSchema }: Props) {
  return (
    <nav id="sidebar" className={`sidebar${open ? " open" : ""}`} aria-label="Conversations">
      <button className="btn primary block" onClick={onNew}>
        <Plus size={16} /> New chat
      </button>
      <div className="sidebar-section">History</div>
      <ul className="conv-list">
        {conversations.length === 0 && <li className="muted small-pad">No conversations yet.</li>}
        {conversations.map((c) => (
          <li key={c.id} className={c.id === activeId ? "active" : ""}>
            <button className="conv-item" onClick={() => onSelect(c.id)} title={c.title}>
              <MessageSquare size={15} />
              <span>{c.title}</span>
            </button>
            <button className="icon-btn only conv-delete" onClick={() => onDelete(c.id)} aria-label={`Delete ${c.title}`}>
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
      <div className="sidebar-foot">
        <button className="btn ghost block" onClick={onShowSchema}>
          <Database size={16} /> Browse schema
        </button>
        <div className="model-status">
          <span className={`dot ${health ? (health.llm_ready ? "ok" : "bad") : ""}`} />
          {health ? (health.llm_ready ? `${health.provider} · ${health.model}` : "LLM not configured") : "Connecting…"}
        </div>
      </div>
    </nav>
  );
}
