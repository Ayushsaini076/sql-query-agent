import { AlertTriangle, HelpCircle, PanelRight, RotateCcw, ShieldAlert, Sparkles } from "lucide-react";
import Markdown from "react-markdown";
import type { ChatMessage, SqlResponse } from "../types";
import { PipelineTrace } from "./PipelineTrace";
import { SqlCode } from "./SqlCode";

interface Props {
  message: ChatMessage;
  selected: boolean;
  onSelect: () => void;
  onRetry?: () => void;
}

export function MessageBubble({ message, selected, onSelect, onRetry }: Props) {
  if (message.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{message.content}</div>
      </div>
    );
  }

  const r = message.response;
  const steps = message.steps ?? [];

  return (
    <div className={`msg assistant${selected ? " selected" : ""}`}>
      <div className="avatar" aria-hidden>
        <Sparkles size={16} />
      </div>
      <div className="bubble">
        <PipelineTrace steps={steps} pending={!!message.pending} />

        {message.pending && message.streamText && (
          <div className="markdown streaming">
            <Markdown>{message.streamText}</Markdown>
          </div>
        )}

        {message.error && (
          <div className="notice danger">
            <AlertTriangle size={16} />
            <div>
              <p>{message.error}</p>
              {onRetry && (
                <button className="btn ghost small" onClick={onRetry}>
                  <RotateCcw size={14} /> Retry
                </button>
              )}
            </div>
          </div>
        )}

        {r?.type === "sql" && <SqlSummary r={r} selected={selected} onSelect={onSelect} />}

        {r?.type === "refusal" && (
          <div className="notice warning">
            <ShieldAlert size={16} />
            <p>{r.message}</p>
          </div>
        )}

        {r?.type === "clarification" && (
          <div className="notice info">
            <HelpCircle size={16} />
            <p>{r.message}</p>
          </div>
        )}

        {r?.type === "answer" && (
          <div className="markdown">
            <Markdown>{r.message}</Markdown>
          </div>
        )}

        {r?.type === "error" && (
          <div className="notice danger">
            <AlertTriangle size={16} />
            <div>
              <p>{r.message}</p>
              <ul>
                {r.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
              {onRetry && (
                <button className="btn ghost small" onClick={onRetry}>
                  <RotateCcw size={14} /> Retry
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SqlSummary({ r, selected, onSelect }: { r: SqlResponse; selected: boolean; onSelect: () => void }) {
  const cost = r.analysis?.cost?.level;
  return (
    <>
      <p className="headline">{r.message}</p>
      <SqlCode code={r.sql} compact />
      {r.explanation && (
        <div className="markdown">
          <Markdown>{r.explanation}</Markdown>
        </div>
      )}
      <div className="chips">
        <span className={`chip ${r.validation.warnings.length ? "warn" : "ok"}`}>
          {r.validation.warnings.length ? `Validated · ${r.validation.warnings.length} warning(s)` : "Validated"}
        </span>
        {cost && <span className={`chip cost-${cost}`}>Cost: {cost}</span>}
        {r.results && (
          <span className="chip">
            {r.results.row_count}
            {r.results.truncated ? "+" : ""} rows
          </span>
        )}
        {r.attempts > 1 && <span className="chip">Self-corrected ({r.attempts} attempts)</span>}
        {r.equivalence?.checked && (
          <span className={`chip ${r.equivalence.equivalent ? "ok" : "warn"}`}>
            {r.equivalence.equivalent ? "Same results as original" : "Results differ"}
          </span>
        )}
        <button className={`chip action${selected ? " active" : ""}`} onClick={onSelect}>
          <PanelRight size={13} /> Details
        </button>
      </div>
    </>
  );
}
