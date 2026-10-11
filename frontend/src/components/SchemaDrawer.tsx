import { KeyRound, Link2, X } from "lucide-react";
import { useEffect } from "react";
import type { SchemaInfo } from "../types";

interface Props {
  schema: SchemaInfo | null;
  error: string | null;
  onClose: () => void;
}

export function SchemaDrawer({ schema, error, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Database schema" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <h3>Acme Retail database</h3>
            <p className="muted">SQLite sample schema the assistant is restricted to.</p>
          </div>
          <button className="icon-btn only" onClick={onClose} aria-label="Close schema">
            <X size={18} />
          </button>
        </div>
        {error && <p className="notice danger">{error}</p>}
        {!schema && !error && <p className="muted">Loading schema…</p>}
        {schema && (
          <div className="schema-grid">
            {schema.tables.map((t) => (
              <section key={t.name} className="schema-table">
                <header>
                  <strong>{t.name}</strong>
                  <span className="muted">{t.row_count.toLocaleString()} rows</span>
                </header>
                <p className="muted small">{t.description.split(". Synonyms")[0]}</p>
                <ul>
                  {t.columns.map((c) => (
                    <li key={c.name} title={c.description}>
                      <span className="col-name">
                        {c.primary_key && <KeyRound size={12} className="pk" aria-label="primary key" />}
                        {c.references && <Link2 size={12} className="fk" aria-label="foreign key" />}
                        {c.name}
                      </span>
                      <span className="col-type">
                        {c.type}
                        {c.references && <em> → {c.references}</em>}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
