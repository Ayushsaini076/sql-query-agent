export type Dialect = "sqlite" | "postgres" | "mysql";
export type Intent = "generate_sql" | "optimize_sql" | "debug_sql" | "explain_sql";

export interface Step {
  node: string;
  label: string;
  detail: string;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Validation {
  sql: string;
  valid: boolean;
  checks: Check[];
  errors: string[];
  warnings: string[];
  tables: string[];
  destructive: boolean;
}

export interface Cost {
  level: "low" | "medium" | "high";
  estimated_rows_examined: number;
  full_scans: string[];
  index_lookups: string[];
  temp_structures: string[];
  notes: string[];
}

export interface IndexSuggestion {
  table: string;
  columns: string[];
  statement: string;
  reason: string;
}

export interface Analysis {
  formatted_sql: string;
  dialects: Record<Dialect, string>;
  dialect_notes: Record<Dialect, string[]>;
  plan: string[];
  cost: Cost;
  index_suggestions: IndexSuggestion[];
}

export interface Results {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface Optimization {
  original_sql: string;
  original_cost: Cost | null;
  readability_changes: string[];
  performance_changes: string[];
  removed_joins: string[];
  index_recommendations: string[];
}

export interface DebugInfo {
  original_sql: string;
  issues: { problem: string; explanation: string }[];
  summary: string;
  detected_errors: string[];
}

export interface Equivalence {
  checked: boolean;
  equivalent: boolean | null;
  detail: string;
}

export interface SqlResponse {
  type: "sql";
  intent: Intent;
  message: string;
  sql: string;
  explanation: string | null;
  assumptions: string[];
  tables: string[];
  validation: Validation;
  analysis: Analysis;
  optimization: Optimization | null;
  debug: DebugInfo | null;
  results: Results | null;
  execution_error: string | null;
  equivalence: Equivalence | null;
  attempts: number;
}

export interface ErrorResponse {
  type: "error";
  message: string;
  errors: string[];
  sql: string | null;
  validation: Validation | null;
}

export type AgentResponse =
  | SqlResponse
  | ErrorResponse
  | { type: "refusal"; reason: string; message: string }
  | { type: "clarification"; message: string }
  | { type: "answer"; message: string };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps?: Step[];
  streamText?: string;
  response?: AgentResponse;
  error?: string;
  pending?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export interface SchemaColumn {
  name: string;
  type: string;
  primary_key: boolean;
  nullable: boolean;
  description: string;
  references: string | null;
}

export interface SchemaTable {
  name: string;
  description: string;
  row_count: number;
  columns: SchemaColumn[];
  indexes: Record<string, string[]>;
}

export interface SchemaInfo {
  tables: SchemaTable[];
  relationships: { from: string; to: string }[];
}

export interface Health {
  status: string;
  llm_ready: boolean;
  provider: string;
  model: string;
}
