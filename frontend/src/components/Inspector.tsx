import {
  AlertTriangle, CheckCircle2, Code2, Download, FileText, Gauge, ListChecks, Table2, X, XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import type { AgentResponse, Cost, Dialect, SqlResponse } from "../types";
import { downloadFile, formatCell, toCsv } from "../utils";
import { SqlCode } from "./SqlCode";

type Tab = "sql" | "explanation" | "results" | "validation" | "performance";
const TABS: { id: Tab; label: string; icon: typeof Code2 }[] = [
  { id: "sql", label: "SQL", icon: Code2 },
  { id: "explanation", label: "Explanation", icon: FileText },
  { id: "results", label: "Results", icon: Table2 },
  { id: "validation", label: "Validation", icon: ListChecks },
  { id: "performance", label: "Performance", icon: Gauge },
];
const DIALECTS: { id: Dialect; label: string }[] = [
  { id: "sqlite", label: "SQLite" },
  { id: "postgres", label: "PostgreSQL" },
  { id: "mysql", label: "MySQL" },
];

interface Props {
  response: AgentResponse | undefined;
  question: string | undefined;
  onClose?: () => void;
}

export function Inspector({ response, question, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("sql");
  const sql = response?.type === "sql" ? response : undefined;

  useEffect(() => {
    if (response?.type === "error") setTab("validation");
  }, [response]);

  return (
    <aside className="inspector" aria-label="Query details">
      <div className="inspector-head">
        <div className="inspector-title">
          <span>Query details</span>
          {question && <small title={question}>{question}</small>}
        </div>
        {onClose && (
          <button className="icon-btn only" onClick={onClose} aria-label="Close details">
            <X size={18} />
          </button>
        )}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            <Icon size={15} />
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div className="inspector-body">
        {!response && <Placeholder text="Ask a question to see the generated SQL, its explanation, results and checks here." />}
        {response && response.type !== "sql" && response.type !== "error" && (
          <Placeholder text="This reply has no SQL. Select a reply with a query to inspect it." />
        )}
        {response?.type === "error" && tab !== "validation" && (
          <Placeholder text="No valid query was produced. See the Validation tab for details." />
        )}
        {response?.type === "error" && tab === "validation" && response.validation && (
          <ValidationPanel validation={response.validation} />
        )}
        {sql && tab === "sql" && <SqlPanel r={sql} />}
        {sql && tab === "explanation" && <ExplanationPanel r={sql} />}
        {sql && tab === "results" && <ResultsPanel r={sql} />}
        {sql && tab === "validation" && <ValidationPanel validation={sql.validation} attempts={sql.attempts} />}
        {sql && tab === "performance" && <PerformancePanel r={sql} />}
      </div>
    </aside>
  );
}

function Placeholder({ text }: { text: string }) {
  return <p className="placeholder">{text}</p>;
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <section className="panel-section">
      <h4>{title}</h4>
      <ul className="bullets">
        {items.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
    </section>
  );
}

function SqlPanel({ r }: { r: SqlResponse }) {
  const [dialect, setDialect] = useState<Dialect>("sqlite");
  const code = r.analysis?.dialects?.[dialect] ?? r.sql;
  const notes = r.analysis?.dialect_notes?.[dialect] ?? [];
  const ext = dialect === "sqlite" ? "" : `.${dialect}`;

  return (
    <>
      <div className="segmented" role="radiogroup" aria-label="SQL dialect">
        {DIALECTS.map((d) => (
          <button key={d.id} role="radio" aria-checked={dialect === d.id} className={dialect === d.id ? "active" : ""} onClick={() => setDialect(d.id)}>
            {d.label}
          </button>
        ))}
      </div>
      <SqlCode code={code} filename={`query${ext}.sql`} label={DIALECTS.find((d) => d.id === dialect)?.label} />
      {dialect !== "sqlite" && (
        <p className="hint">Executed and validated on SQLite; translated automatically for {DIALECTS.find((d) => d.id === dialect)?.label}.</p>
      )}
      {notes.map((n, i) => (
        <div key={i} className="notice warning small">
          <AlertTriangle size={14} />
          <p>{n}</p>
        </div>
      ))}
      <List title="Assumptions" items={r.assumptions} />

      {r.debug && (
        <section className="panel-section">
          <h4>Issues found</h4>
          {r.debug.issues.length === 0 && <p className="muted">No errors were found in the original query.</p>}
          <ol className="issues">
            {r.debug.issues.map((i, k) => (
              <li key={k}>
                <strong>{i.problem}</strong>
                <span>{i.explanation}</span>
              </li>
            ))}
          </ol>
          {r.debug.summary && <p className="muted">{r.debug.summary}</p>}
          <SqlCode code={r.debug.original_sql} label="Original (broken) SQL" compact />
        </section>
      )}

      {r.optimization && (
        <section className="panel-section">
          {r.equivalence && (
            <div className={`notice ${r.equivalence.equivalent ? "success" : "warning"} small`}>
              {r.equivalence.equivalent ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
              <p>{r.equivalence.detail}</p>
            </div>
          )}
          <List title="Readability" items={r.optimization.readability_changes} />
          <List title="Performance" items={r.optimization.performance_changes} />
          <List title="Removed joins" items={r.optimization.removed_joins} />
          <SqlCode code={r.optimization.original_sql} label="Original SQL" compact />
        </section>
      )}
    </>
  );
}

function ExplanationPanel({ r }: { r: SqlResponse }) {
  return (
    <>
      <div className="markdown">
        <Markdown>{r.explanation || "_No explanation available._"}</Markdown>
      </div>
      {r.validation.tables.length > 0 && (
        <section className="panel-section">
          <h4>Tables used</h4>
          <div className="chips">
            {r.validation.tables.map((t) => (
              <span key={t} className="chip">{t}</span>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function ResultsPanel({ r }: { r: SqlResponse }) {
  if (r.execution_error) {
    return (
      <div className="notice danger">
        <XCircle size={16} />
        <p>Execution failed: {r.execution_error}</p>
      </div>
    );
  }
  if (!r.results) return <Placeholder text="Query execution is turned off. Enable “Run queries” in the header to see results." />;
  const { columns, rows, row_count, truncated, elapsed_ms } = r.results;
  return (
    <>
      <div className="results-bar">
        <span>
          {row_count}
          {truncated ? "+" : ""} row{row_count === 1 ? "" : "s"} · {elapsed_ms} ms
          {truncated && <em> (showing first {row_count})</em>}
        </span>
        <button className="icon-btn" onClick={() => downloadFile("results.csv", toCsv(columns, rows), "text/csv")} disabled={!rows.length}>
          <Download size={15} />
          <span>CSV</span>
        </button>
      </div>
      {rows.length === 0 ? (
        <Placeholder text="The query ran successfully but returned no rows." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((v, j) => (
                    <td key={j} className={v === null ? "null" : typeof v === "number" ? "num" : ""}>
                      {formatCell(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

const CHECK_LABELS: Record<string, string> = {
  syntax: "Syntax",
  read_only: "Read-only",
  tables: "Tables exist",
  columns: "Columns exist",
  joins: "Join relationships",
  compiles: "SQLite compiles query",
};

function ValidationPanel({ validation, attempts }: { validation: SqlResponse["validation"]; attempts?: number }) {
  return (
    <>
      <div className={`notice ${validation.valid ? "success" : "danger"} small`}>
        {validation.valid ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
        <p>
          {validation.valid ? "All guardrail checks passed" : "Validation failed"}
          {attempts && attempts > 1 ? ` after ${attempts} attempts (self-corrected).` : "."}
        </p>
      </div>
      <ul className="checks">
        {validation.checks.map((c, i) => {
          const isWarning = validation.warnings.includes(c.detail);
          return (
            <li key={i} className={!c.passed ? "fail" : isWarning ? "warn" : "pass"}>
              {!c.passed ? <XCircle size={16} /> : isWarning ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
              <div>
                <strong>{CHECK_LABELS[c.name] ?? c.name}</strong>
                <span>{c.detail}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function CostCard({ cost, title }: { cost: Cost; title: string }) {
  return (
    <div className={`cost-card cost-${cost.level}`}>
      <span className="cost-title">{title}</span>
      <strong>{cost.level.toUpperCase()}</strong>
      <span>~{cost.estimated_rows_examined.toLocaleString()} rows examined</span>
    </div>
  );
}

function PerformancePanel({ r }: { r: SqlResponse }) {
  const a = r.analysis;
  const llmIndexes = r.optimization?.index_recommendations ?? [];
  return (
    <>
      <div className="cost-row">
        {r.optimization?.original_cost && <CostCard cost={r.optimization.original_cost} title="Original" />}
        <CostCard cost={a.cost} title={r.optimization ? "Optimized" : "Estimated cost"} />
      </div>
      <p className="hint">Heuristic estimate from SQLite's query plan and table statistics.</p>
      <List title="Full table scans" items={a.cost.full_scans} />
      <List title="Index lookups" items={a.cost.index_lookups} />
      <List title="Sorting / grouping" items={a.cost.temp_structures} />
      <List title="Notes" items={a.cost.notes} />
      {(a.index_suggestions.length > 0 || llmIndexes.length > 0) && (
        <section className="panel-section">
          <h4>Recommended indexes</h4>
          {a.index_suggestions.map((s, i) => (
            <div key={i} className="index-suggestion">
              <SqlCode code={s.statement} compact label={`Index on ${s.table}`} />
              <p className="muted">{s.reason}</p>
            </div>
          ))}
          {llmIndexes.map((s, i) => (
            <p key={`llm-${i}`} className="muted mono">{s}</p>
          ))}
          <p className="hint">Recommendations only. The assistant never changes the database.</p>
        </section>
      )}
      <section className="panel-section">
        <h4>Query plan</h4>
        <ol className="plan">
          {a.plan.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ol>
      </section>
    </>
  );
}
