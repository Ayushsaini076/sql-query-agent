"""Node implementations for the SQL agent graph.

Each public method is one LangGraph node: it reads the state and returns a partial update.
LLM steps (router, generation, debugging, optimization, explanation) are wrapped by
deterministic steps (guards, retrieval, validation, analysis, execution) that the LLM
cannot bypass.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any

import sqlglot
from langchain_core.language_models import BaseChatModel
from sqlglot import exp
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.agent import prompts
from app.agent.guards import check_input, contains_destructive_sql
from app.agent.llm import message_text, structured_call
from app.agent.models import DebugResult, IntentDecision, OptimizationResult, SQLDraft
from app.agent.retrieval import SchemaRetriever
from app.agent.state import AgentState
from app.db.catalog import SchemaCatalog
from app.db.executor import QueryTimeout, ReadOnlyDatabase
from app.sql.analyzer import QueryAnalyzer
from app.sql.validator import SQLValidator, clean_sql

EQUIVALENCE_ROW_LIMIT = 5000


class SQLAgentNodes:
    def __init__(
        self,
        *,
        llm: BaseChatModel,
        fast_llm: BaseChatModel | None = None,
        provider: str,
        catalog: SchemaCatalog,
        db: ReadOnlyDatabase,
        max_attempts: int = 3,
        history_turns: int = 6,
        max_input_chars: int = 4000,
        max_result_rows: int = 200,
    ):
        self.llm = llm
        self.fast_llm = fast_llm or llm
        self.provider = provider
        self.catalog = catalog
        self.db = db
        self.validator = SQLValidator(catalog, db)
        self.analyzer = QueryAnalyzer(catalog, db)
        self.retriever = SchemaRetriever(catalog)
        self.max_attempts = max_attempts
        self.history_turns = history_turns
        self.max_input_chars = max_input_chars
        self.max_result_rows = max_result_rows

    # =================================================================== guards
    def guard_input(self, state: AgentState) -> dict:
        """Deterministic pre-LLM checks: empty/oversized input, prompt injection, raw write SQL."""
        result = check_input(state["question"], max_chars=self.max_input_chars)
        if result.blocked:
            return {"refusal_reason": result.reason, "intent": "destructive" if result.reason == "destructive" else "blocked"}
        return {}

    # =================================================================== router
    def classify_intent(self, state: AgentState) -> dict:
        """LLM intent detection + scope validation in one structured call."""
        decision = structured_call(
            self.fast_llm,
            IntentDecision,
            [
                SystemMessage(prompts.ROUTER_SYSTEM),
                HumanMessage(prompts.ROUTER_USER.format(
                    history=self._history(state),
                    previous_sql=state.get("last_sql") or "(none)",
                    question=state["question"],
                )),
            ],
            self.provider,
        )
        intent = decision.intent
        update: dict[str, Any] = {
            "intent": intent,
            "router_rationale": decision.rationale,
            "refers_to_previous": decision.refers_to_previous_sql,
        }
        user_sql = clean_sql(decision.user_sql) if decision.user_sql else None

        if intent in ("out_of_scope", "destructive"):
            update["refusal_reason"] = intent
        elif intent == "clarify":
            update["clarification"] = decision.clarification_question
        elif intent == "generate_sql" and user_sql:
            # "Run this: SELECT ..." or "like this, but only CA": the pasted query is the starting point.
            update["source_sql"] = user_sql
        elif intent in ("optimize_sql", "debug_sql", "explain_sql"):
            source = user_sql or state.get("last_sql")
            if source is None:
                verb = intent.split("_")[0]
                update.update(intent="clarify", clarification=f"Please paste the SQL query you'd like me to {verb}.")
            else:
                update["source_sql"] = source
        return update

    def check_user_sql(self, state: AgentState) -> dict:
        """Validate SQL the user supplied before working on it."""
        sql = state["source_sql"]
        report = self.validator.validate(sql)
        update: dict[str, Any] = {"source_validation": report.to_dict()}
        if report.destructive or contains_destructive_sql(sql):
            update.update(intent="destructive", refusal_reason="destructive")
        elif not report.valid and state["intent"] in ("optimize_sql", "explain_sql"):
            update.update(intent="debug_sql", routed_to_debug=True)  # fix it first
        return update

    # ================================================================ retrieval
    def retrieve_schema(self, state: AgentState) -> dict:
        previous = state.get("last_sql") if state.get("refers_to_previous") else None
        tables = self.retriever.retrieve(state["question"], previous_sql=previous, extra_sql=state.get("source_sql"))
        return {"relevant_tables": tables}

    # =============================================================== producers
    def generate_sql(self, state: AgentState) -> dict:
        tables = self._tables_for_attempt(state)
        draft = structured_call(
            self.llm,
            SQLDraft,
            [
                SystemMessage(prompts.GENERATE_SYSTEM.format(
                    today=date.today().isoformat(),
                    schema=self.catalog.render(tables),
                    relationships=self.catalog.render_relationships(),
                    other_tables=self.catalog.render_summary(exclude=tables) or "(none)",
                )),
                HumanMessage(prompts.GENERATE_USER.format(
                    history=self._history(state),
                    previous_sql=state.get("source_sql") or state.get("last_sql") or "(none)",
                    question=state["question"],
                    feedback=self._retry_feedback(state),
                )),
            ],
            self.provider,
        )
        if not draft.sql:
            reason = draft.unanswerable_reason or "The database doesn't contain the data needed for that request."
            return {
                "candidate_sql": None,
                "intent": "clarify",
                "clarification": f"{reason} Could you rephrase the request using the available data?",
            }
        return {"candidate_sql": clean_sql(draft.sql), "assumptions": draft.assumptions, "relevant_tables": tables}

    def debug_sql(self, state: AgentState) -> dict:
        tables = self._tables_for_attempt(state)
        source_report = state.get("source_validation") or self.validator.validate(state["source_sql"]).to_dict()
        diagnostics = "\n".join(
            [f"- ERROR: {e}" for e in source_report["errors"]] + [f"- WARNING: {w}" for w in source_report["warnings"]]
        )
        result = structured_call(
            self.llm,
            DebugResult,
            [
                SystemMessage(prompts.DEBUG_SYSTEM.format(
                    schema=self.catalog.render(tables),
                    relationships=self.catalog.render_relationships(),
                    other_tables=self.catalog.render_summary(exclude=tables) or "(none)",
                )),
                HumanMessage(prompts.DEBUG_USER.format(
                    question=state["question"],
                    sql=state["source_sql"],
                    diagnostics=diagnostics or "- No errors detected automatically; look for logical problems.",
                    feedback=self._retry_feedback(state),
                )),
            ],
            self.provider,
        )
        return {
            "candidate_sql": clean_sql(result.corrected_sql),
            "debug": {
                "original_sql": state["source_sql"],
                "issues": [i.model_dump() for i in result.issues],
                "summary": result.summary,
                "detected_errors": source_report["errors"],
            },
        }

    def optimize_sql(self, state: AgentState) -> dict:
        tables = self._tables_for_attempt(state)
        try:
            plan = "\n".join(r["detail"] for r in self.db.explain_plan(state["source_sql"]))
        except sqlite3.Error as exc:
            plan = f"(unavailable: {exc})"
        result = structured_call(
            self.llm,
            OptimizationResult,
            [
                SystemMessage(prompts.OPTIMIZE_SYSTEM.format(
                    schema=self.catalog.render(tables),
                    relationships=self.catalog.render_relationships(tables),
                )),
                HumanMessage(prompts.OPTIMIZE_USER.format(
                    question=state["question"], sql=state["source_sql"], plan=plan,
                    feedback=self._retry_feedback(state),
                )),
            ],
            self.provider,
        )
        original_cost = None
        try:
            original_cost = self.analyzer.analyze(state["source_sql"]).cost.__dict__
        except Exception:
            pass
        return {
            "candidate_sql": clean_sql(result.optimized_sql),
            "optimization": {
                "original_sql": state["source_sql"],
                "original_cost": original_cost,
                **result.model_dump(exclude={"optimized_sql", "index_recommendations"}),
                "index_recommendations": self._valid_index_statements(result.index_recommendations),
            },
        }

    def load_user_sql(self, state: AgentState) -> dict:
        """Explain path: the user's own SQL is the candidate."""
        return {"candidate_sql": state["source_sql"]}

    # =============================================================== validation
    def validate_sql(self, state: AgentState) -> dict:
        report = self.validator.validate(state["candidate_sql"] or "")
        return {
            "validation": report.to_dict(),
            "plan": report.plan,
            "candidate_sql": report.sql,
            "attempts": state.get("attempts", 0) + 1,
        }

    def route_after_validation(self, state: AgentState) -> str:
        if state["validation"]["valid"]:
            return "analyze_sql"
        if state["intent"] in ("generate_sql", "debug_sql", "optimize_sql") and state["attempts"] < self.max_attempts:
            return state["intent"]  # self-correction loop back to the producer
        return "fail"

    # ======================================================= analysis / execution
    def analyze_sql(self, state: AgentState) -> dict:
        analysis = self.analyzer.analyze(state["candidate_sql"], state.get("plan") or None)
        return {"analysis": analysis.to_dict(), "final_sql": analysis.formatted_sql}

    def execute_sql(self, state: AgentState) -> dict:
        if not state.get("execute", True):
            return {}
        update: dict[str, Any] = {}
        try:
            update["execution"] = self.db.execute(state["final_sql"], max_rows=self.max_result_rows).to_dict()
        except (sqlite3.Error, QueryTimeout) as exc:
            update["execution_error"] = str(exc)
        if state["intent"] == "optimize_sql" and "execution" in update:
            update["equivalence"] = self._compare_results(state["source_sql"], state["final_sql"])
        return update

    def _compare_results(self, original: str, optimized: str) -> dict:
        """Run both versions on the sample database and compare result multisets."""
        try:
            a = self.db.execute(original, max_rows=EQUIVALENCE_ROW_LIMIT)
            b = self.db.execute(optimized, max_rows=EQUIVALENCE_ROW_LIMIT)
        except (sqlite3.Error, QueryTimeout) as exc:
            return {"checked": False, "equivalent": None, "detail": f"Could not compare: {exc}"}
        if a.truncated or b.truncated:
            return {"checked": False, "equivalent": None, "detail": "Result too large to compare fully."}
        if len(a.columns) != len(b.columns):
            return {"checked": True, "equivalent": False,
                    "detail": f"Column count differs ({len(a.columns)} vs {len(b.columns)})."}
        same = sorted(map(repr, a.rows)) == sorted(map(repr, b.rows))
        detail = (f"Both queries return the same {a.row_count} rows on the sample database." if same
                  else f"Results differ on the sample database ({a.row_count} vs {b.row_count} rows).")
        return {"checked": True, "equivalent": same, "detail": detail}

    # ============================================================== explanation
    def generate_explanation(self, state: AgentState) -> dict:
        context = []
        if state.get("assumptions"):
            context.append("Assumptions: " + "; ".join(state["assumptions"]))
        if state.get("execution"):
            ex = state["execution"]
            context.append(f"Executed on the sample database: {ex['row_count']}{'+' if ex['truncated'] else ''} rows.")
        if state["intent"] == "debug_sql":
            context.append("This is the corrected version of the user's query.")
        if state["intent"] == "optimize_sql":
            context.append("This is the optimized version of the user's query.")
        reply = self.fast_llm.invoke([
            SystemMessage(prompts.EXPLAIN_SYSTEM),
            HumanMessage(prompts.EXPLAIN_USER.format(
                question=state["question"], sql=state["final_sql"], context="\n".join(context) or "(none)",
            )),
        ])
        return {"explanation": message_text(reply).strip()}

    # =========================================================== terminal nodes
    def respond(self, state: AgentState) -> dict:
        intent = state["intent"]
        execution = state.get("execution")
        headline = {
            "generate_sql": "Here's the SQL for your request.",
            "optimize_sql": "Here's an optimized version of your query.",
            "explain_sql": "Here's what this query does.",
            "debug_sql": "I fixed the query.",
        }[intent]
        if intent == "debug_sql":
            n = len((state.get("debug") or {}).get("issues", []))
            headline = (f"I found {n} issue{'s' if n != 1 else ''} and fixed the query." if n
                        else "I couldn't find any errors in this query.")
            if state.get("routed_to_debug"):
                headline = "Your query has errors, so I fixed it first. " + headline
        if execution:
            more = "+" if execution["truncated"] else ""
            headline += f" It returned {execution['row_count']}{more} row{'s' if execution['row_count'] != 1 else ''}."
        response = {
            "type": "sql",
            "intent": intent,
            "message": headline,
            "sql": state["final_sql"],
            "explanation": state.get("explanation"),
            "assumptions": state.get("assumptions") or [],
            "tables": state.get("relevant_tables") or [],
            "validation": state.get("validation"),
            "analysis": state.get("analysis"),
            "optimization": state.get("optimization"),
            "debug": state.get("debug"),
            "results": execution,
            "execution_error": state.get("execution_error"),
            "equivalence": state.get("equivalence"),
            "attempts": state.get("attempts", 1),
        }
        return self._finish(state, response, f"{headline}\nSQL:\n{state['final_sql']}", last_sql=state["final_sql"])

    def refuse(self, state: AgentState) -> dict:
        reason = state.get("refusal_reason") or "out_of_scope"
        message = {
            "destructive": prompts.DESTRUCTIVE_REFUSAL,
            "prompt_injection": prompts.INJECTION_REFUSAL,
            "too_long": f"That message is too long. Please keep requests under {self.max_input_chars} characters.",
            "empty": "Please type a question about the database or paste an SQL query.",
        }.get(reason, prompts.SCOPE_STATEMENT)
        return self._finish(state, {"type": "refusal", "reason": reason, "message": message}, message)

    def clarify(self, state: AgentState) -> dict:
        message = state.get("clarification") or "Could you clarify what data you'd like to see?"
        return self._finish(state, {"type": "clarification", "message": message}, message)

    def answer_question(self, state: AgentState) -> dict:
        reply = self.llm.invoke([
            SystemMessage(prompts.DB_QUESTION_SYSTEM.format(
                schema=self.catalog.render(), relationships=self.catalog.render_relationships(),
            )),
            HumanMessage(prompts.DB_QUESTION_USER.format(history=self._history(state), question=state["question"])),
        ])
        text = self._validate_sql_blocks(message_text(reply).strip())
        return self._finish(state, {"type": "answer", "message": text}, text)

    def fail(self, state: AgentState) -> dict:
        validation = state.get("validation") or {}
        errors = validation.get("errors") or ["The query could not be validated."]
        message = (
            f"I couldn't produce a valid query after {state.get('attempts', 0)} attempt(s). "
            "Try rephrasing the request or naming the tables and columns you're interested in."
        )
        # Never echo the rejected SQL: it may be destructive or reference names that don't exist.
        response = {
            "type": "error", "message": message, "errors": errors, "sql": None, "intent": state.get("intent"),
            "validation": {k: v for k, v in validation.items() if k != "sql"} if validation else None,
        }
        return self._finish(state, response, message)

    # ================================================================== helpers
    def _finish(self, state: AgentState, response: dict, summary: str, *, last_sql: str | None = None) -> dict:
        update: dict[str, Any] = {
            "response": response,
            "messages": [HumanMessage(state["question"]), AIMessage(summary)],
        }
        if last_sql:
            update["last_sql"] = last_sql
        return update

    def _validate_sql_blocks(self, text: str) -> str:
        """Example queries inside free-text answers obey the same guardrails as generated SQL."""
        def check(match: re.Match) -> str:
            report = self.validator.validate(match.group(1))
            if report.valid:
                return f"```sql\n{report.sql}\n```"
            return "_(An example query was removed because it did not pass validation.)_"

        return re.sub(r"```sql\s*\n(.*?)```", check, text, flags=re.DOTALL | re.IGNORECASE)

    def _valid_index_statements(self, items: list[str]) -> list[str]:
        """Keep only well-formed CREATE INDEX advice on existing tables and columns."""
        kept = []
        for item in items:
            try:
                node = next((n for n in sqlglot.parse(item, read="sqlite") if n is not None), None)
            except Exception:
                continue
            index = node.this if isinstance(node, exp.Create) else None
            if not isinstance(index, exp.Index) or not index.args.get("table"):
                continue
            table = self.catalog.table(index.args["table"].name)
            params = index.args.get("params")
            columns = [c.find(exp.Column) for c in (params.args.get("columns") or [])] if params else []
            if table and columns and all(c is not None and table.column(c.name) for c in columns):
                kept.append(node.sql("sqlite") + ";")
        return kept

    def _history(self, state: AgentState) -> str:
        messages: list[BaseMessage] = state.get("messages") or []
        recent = messages[-2 * self.history_turns:]
        lines = []
        for m in recent:
            role = "User" if isinstance(m, HumanMessage) else "Assistant"
            text = message_text(m)
            lines.append(f"{role}: {text[:800]}{'…' if len(text) > 800 else ''}")
        return "\n".join(lines) or "(no earlier messages)"

    def _retry_feedback(self, state: AgentState) -> str:
        validation = state.get("validation")
        if not validation or validation.get("valid"):
            return ""
        return prompts.RETRY_FEEDBACK.format(
            sql=validation.get("sql", ""), errors="\n".join(f"- {e}" for e in validation["errors"]),
        )

    def _tables_for_attempt(self, state: AgentState) -> list[str]:
        """Retrieved tables first; widen to the full schema after a name-resolution failure."""
        validation = state.get("validation")
        if validation and not validation.get("valid") and any(
            "does not exist" in e or "no such" in e for e in validation["errors"]
        ):
            return self.catalog.table_names
        return state.get("relevant_tables") or self.catalog.table_names
