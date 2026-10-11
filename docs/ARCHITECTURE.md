# Architecture

This document covers how a request moves through the system, the design decisions behind it, and the trade-offs made. The [README](../README.md) has the overview diagrams. [`workflow.mmd`](workflow.mmd) is the graph that LangGraph renders from the compiled `StateGraph`.

## Request lifecycle

1. The UI `POST`s `{session_id, message, execute}` to `/api/chat/stream`.
2. FastAPI checks the session id and rate limit, then calls `graph.astream(new_turn(message), {"thread_id": session_id}, stream_mode=["updates", "messages"])`.
3. The checkpointer loads the conversation (`messages`, `last_sql`). `new_turn()` resets every per-turn field, so state from an earlier turn cannot leak into routing.
4. As each node finishes, an `updates` chunk becomes a `step` SSE event (label plus a short detail such as `failed (attempt 1): Table 'Employee' does not exist`). The UI renders these as a live "agent workflow" trace.
5. Tokens from the `generate_explanation` and `answer_question` nodes are forwarded as `token` events (LangGraph `messages` stream mode, filtered by `langgraph_node`).
6. After the run, the final `response` is read from the checkpointed state and sent as a `result` event. The terminal node has already appended the `HumanMessage`/`AIMessage` pair and the new `last_sql` to the thread.

SSE event types:

| Event | Payload |
|---|---|
| `step` | `{node, label, detail}` |
| `token` | `{node, text}` |
| `result` | the response object (`type`: `sql`, `refusal`, `clarification`, `answer` or `error`) |
| `error` | `{message}` for provider or infrastructure failures |

## State

`AgentState` (`backend/app/agent/state.py`) has three groups of fields:

- **Conversation, persisted:** `messages` (with the `add_messages` reducer) and `last_sql`.
- **Routing:** `intent`, `refusal_reason`, `clarification`, `source_sql` (pasted or previous SQL), `refers_to_previous`, `routed_to_debug`.
- **SQL pipeline:** `relevant_tables`, `candidate_sql`, `validation`, `attempts`, `analysis`, `execution`, `equivalence`, `explanation`, and finally `response`.

## Design decisions

**LLM for language, code for guarantees.** The model routes, writes, diagnoses and explains. Everything that has to *always* hold (read-only, existing names, a valid join graph, compilable SQL, bounded execution) is enforced by deterministic nodes the model cannot skip, because the graph only reaches `respond` through `validate_sql`.

**Structured output everywhere.** Every LLM decision is a Pydantic schema (`IntentDecision`, `SQLDraft`, `DebugResult`, `OptimizationResult`) sent as native JSON-schema structured output. Current Claude models reject forced tool choice, so the tool-calling path is used only for providers without native support. Gemini receives the schema as `response_json_schema`, and Groq's gpt-oss models get it with `strict: true` (constrained decoding). All fields are required, with nullable values where needed, so the same schemas also work under OpenAI's and Groq's strict modes.

**Provider-agnostic models with fallback.** `create_models()` in `backend/app/agent/llm.py` builds the main model (SQL generation, debugging, optimization, schema questions) and the fast model (routing, explanations) through LangChain's `init_chat_model`, with per-provider defaults and effort settings (Claude `effort`, Gemini 3+ `thinking_level`, Groq gpt-oss `reasoning_effort`). When `LLM_FALLBACK_PROVIDER` is set and its API key is present, each model is wrapped in a `FallbackLLM`: any failed call (rate limit, timeout, outage, rejected key) is retried once on the fallback provider's default model, using that provider's structured-output method, and a warning is logged. Nodes are unaware of the wrapper because they only call `structured_call()` and `invoke()`. `/api/health` reports the active fallback provider.

**Self-correction with precise feedback.** The validator's messages are written for the model: they say what is wrong and what exists instead ("Column 'FirstNam' does not exist… Did you mean Employees.FirstName?… Columns available: …"). After a name-resolution error, the retry also sees the full schema instead of only the retrieved subset.

**Validation layers.** sqlglot gives the AST (read-only check, table/column resolution through `qualify`, alias-aware join extraction) and human-friendly messages. SQLite is the final authority: `EXPLAIN QUERY PLAN` compiles the statement without running it. When the two disagree (for example, SQLite allows a `SELECT` alias in `WHERE`), the engine wins. The exception is a double-quoted unknown identifier, which SQLite would silently treat as a string literal.

**Join validation.** Equality conditions between two base-table columns are checked against declared foreign keys. Incompatible types (e.g. `TEXT = INTEGER`) are errors. Non-FK joins between compatible columns are warnings, because they are occasionally intentional. Joins with no condition (or comma joins not linked in `WHERE`) are flagged as Cartesian products.

**Schema retrieval.** Each table is scored with IDF-weighted token overlap against its name (×3), its description with synonyms (×2) and its non-key column names/descriptions (×1). Tables scoring at least 40% of the best score are kept. Tables on the shortest FK path between selected tables are added so joins remain possible, along with tables from the previous query on follow-ups. Remaining tables are listed as one-line summaries, so a retrieval miss costs a little precision rather than the answer.

**Optimization you can verify.** The optimizer must preserve results. After execution, `execute_sql` runs the original and optimized queries (up to 5,000 rows each) and compares the result multisets, so the UI can say "Both queries return the same N rows on the sample database". The original's and the optimized query's cost estimates are shown side by side.

**Cost estimate.** `analyze_sql` walks the SQLite plan with a nested-loop model. A full scan costs the table's row count. An index probe costs the average rows per key from `sqlite_stat1` (written by `ANALYZE` at seed time). Temp B-trees for sorting and grouping raise the level. Index advice comes from two sources: unindexed filter/join columns on fully scanned tables (equality columns before range columns), and `AUTOMATIC` indexes that SQLite had to build at query time.

**Dialects.** Validation and execution use SQLite. sqlglot translates the final SQL to PostgreSQL and MySQL for display and download. Functions that don't translate (`JULIANDAY`, `DATE('now', …)`) are flagged rather than silently passed through.

**Conversation memory.** The `AsyncSqliteSaver` checkpointer stores LangGraph state per `thread_id` (the UI's conversation UUID). Prompts get the last 6 turns, with each assistant turn summarised as its headline plus SQL, which keeps prompts short while giving follow-ups what they need.

## Failure handling

| Situation | Behaviour |
|---|---|
| Model returns SQL that fails validation | Up to 3 attempts with validator feedback, then an `error` response listing the problems (never unvalidated SQL) |
| Model asked for data the schema lacks | `SQLDraft.unanswerable_reason` becomes a clarification message |
| Pasted SQL is broken but the user asked to optimize or explain it | Rerouted to `debug_sql` first, and the UI says so |
| Query times out or fails at runtime | SQL and explanation are still returned, along with the execution error |
| Provider error (rate limit, auth, timeout) | Retried once on `LLM_FALLBACK_PROVIDER` if configured; otherwise, or if that also fails, an SSE `error` event with a friendly message, and the UI offers Retry |
| Page reload during a request | Pending messages are marked interrupted when history is restored |

## Testing strategy

`backend/tests` replaces the LLM with `ScriptedLLM`, which returns queued structured responses and records prompts. This lets the real graph, validator, analyzer, executor, checkpointer and API run end to end without network access:

- **Unit:** validator cases (valid queries, every destructive form including writes in CTEs, unknown names, syntax, join rules), executor read-only enforcement and limits, guard patterns, retrieval, analyzer cost and index advice.
- **Integration:** every graph path (generate, follow-up, self-correction, max attempts, destructive SQL from the model, refusals, guard-before-LLM, clarification, unanswerable, debug, optimize with equivalence, explain previous, execution off, schema questions) and the HTTP/SSE API including rate limiting.
- **Provider configuration:** per-provider defaults, aliases and credentials, effort mapping for Gemini and Groq, and the fallback wrapper (a failing primary falls back for both structured and text calls; a healthy primary never touches the fallback).
