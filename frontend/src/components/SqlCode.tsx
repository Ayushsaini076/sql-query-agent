import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import { Check, Copy, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { copyText, downloadFile } from "../utils";

hljs.registerLanguage("sql", sql);

interface Props {
  code: string;
  compact?: boolean;
  filename?: string;
  label?: string;
}

export function SqlCode({ code, compact, filename, label }: Props) {
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => hljs.highlight(code, { language: "sql" }).value, [code]);

  const onCopy = async () => {
    if (await copyText(code)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div className={`sql-code${compact ? " compact" : ""}`}>
      <div className="sql-code-bar">
        <span className="sql-code-label">{label ?? "SQL"}</span>
        <div className="sql-code-actions">
          <button className="icon-btn" onClick={onCopy} title="Copy SQL" aria-label="Copy SQL">
            {copied ? <Check size={15} /> : <Copy size={15} />}
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>
          {filename && (
            <button
              className="icon-btn"
              onClick={() => downloadFile(filename, code + "\n", "application/sql")}
              title="Download .sql"
              aria-label="Download SQL file"
            >
              <Download size={15} />
              <span>.sql</span>
            </button>
          )}
        </div>
      </div>
      <pre>
        <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}
