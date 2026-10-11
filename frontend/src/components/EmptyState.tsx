import { Bug, Gauge, MessageSquareText, ShieldCheck, Wand2 } from "lucide-react";

const GROUPS = [
  {
    title: "Ask in plain English",
    icon: MessageSquareText,
    prompts: [
      "Show all employees hired after January 2024.",
      "Top 5 customers by total spend, with their state",
      "Monthly revenue for 2025 by product category",
    ],
  },
  {
    title: "Follow up",
    icon: Wand2,
    prompts: ["Show all customers.", "Only those from California."],
  },
  {
    title: "Optimize",
    icon: Gauge,
    prompts: [
      "Optimize: SELECT * FROM Orders o JOIN Customers c ON o.CustomerID = c.CustomerID WHERE strftime('%Y', o.OrderDate) = '2025' AND o.Status = 'Delivered'",
    ],
  },
  {
    title: "Debug",
    icon: Bug,
    prompts: ["Fix this: SELECT FirstName, COUNT(*) FROM Employee WHERE HireDate > 2024 GROUP BY"],
  },
  {
    title: "Guardrails",
    icon: ShieldCheck,
    prompts: ["Delete all cancelled orders", "Who won the FIFA World Cup?"],
  },
];

export function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="empty">
      <h1>Ask your database anything</h1>
      <p className="muted">
        I turn questions into validated, read-only SQL for the Acme Retail sample database, and I can also explain,
        optimize and debug your own queries.
      </p>
      <div className="examples">
        {GROUPS.map(({ title, icon: Icon, prompts }) => (
          <div key={title} className="example-group">
            <h4>
              <Icon size={15} /> {title}
            </h4>
            {prompts.map((p) => (
              <button key={p} className="example" onClick={() => onPick(p)} title={p}>
                <span>{p}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
