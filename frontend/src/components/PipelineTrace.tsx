import { CheckCircle2, ChevronDown, Loader2, XCircle } from "lucide-react";
import { useState } from "react";
import type { Step } from "../types";

interface Props {
  steps: Step[];
  pending: boolean;
}

export function PipelineTrace({ steps, pending }: Props) {
  const [open, setOpen] = useState(false);
  const expanded = open || pending;
  if (!steps.length && !pending) return null;
  const last = steps[steps.length - 1];

  return (
    <div className="trace">
      <button className="trace-toggle" onClick={() => setOpen((o) => !o)} disabled={pending} aria-expanded={expanded}>
        {pending ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} className="ok" />}
        <span>{pending ? (last ? `${last.label}…` : "Starting…") : `Agent workflow · ${steps.length} steps`}</span>
        {!pending && <ChevronDown size={14} className={expanded ? "rot" : ""} />}
      </button>
      {expanded && (
        <ol className="trace-steps">
          {steps.map((s, i) => {
            const failed = s.node === "validate_sql" && s.detail.startsWith("failed");
            return (
              <li key={i} className={failed ? "failed" : ""}>
                {failed ? <XCircle size={13} /> : <CheckCircle2 size={13} />}
                <span className="trace-label">{s.label}</span>
                {s.detail && <span className="trace-detail">{s.detail}</span>}
              </li>
            );
          })}
          {pending && (
            <li className="running">
              <Loader2 size={13} className="spin" />
              <span className="trace-label">Working…</span>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}
