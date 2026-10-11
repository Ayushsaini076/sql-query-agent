from __future__ import annotations

import itertools

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.agent.models import IntentDecision, SQLDraft
from app.agent.nodes import SQLAgentNodes
from app.db.catalog import SchemaCatalog
from app.db.executor import ReadOnlyDatabase
from app.db.seed import build_database
from app.sql.analyzer import QueryAnalyzer
from app.sql.validator import SQLValidator
from tests.fakes import ScriptedLLM

_threads = itertools.count()


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("db") / "acme.db")


@pytest.fixture(scope="session")
def catalog(db_path):
    return SchemaCatalog.from_sqlite(db_path)


@pytest.fixture(scope="session")
def db(db_path):
    return ReadOnlyDatabase(db_path, timeout_s=2)


@pytest.fixture(scope="session")
def validator(catalog, db):
    return SQLValidator(catalog, db)


@pytest.fixture(scope="session")
def analyzer(catalog, db):
    return QueryAnalyzer(catalog, db)


def decision(intent: str, *, sql: str | None = None, previous: bool = False, question: str | None = None) -> IntentDecision:
    return IntentDecision(intent=intent, user_sql=sql, refers_to_previous_sql=previous,
                          clarification_question=question, rationale="test")


def draft(sql: str | None, *, assumptions: list[str] | None = None, missing: str | None = None) -> SQLDraft:
    return SQLDraft(sql=sql, assumptions=assumptions or [], unanswerable_reason=missing)


class Agent:
    """A compiled graph wired to a scripted LLM, with a fresh conversation thread."""

    def __init__(self, catalog, db, structured, text):
        self.llm = ScriptedLLM(structured=structured, text=text)
        nodes = SQLAgentNodes(llm=self.llm, provider="fake", catalog=catalog, db=db)
        self.graph = build_graph(nodes, InMemorySaver())
        self.config = {"configurable": {"thread_id": f"t{next(_threads)}"}}

    def ask(self, question: str, *, execute: bool = True) -> dict:
        from app.agent.state import new_turn

        return self.graph.invoke(new_turn(question, execute=execute), self.config)["response"]


@pytest.fixture
def make_agent(catalog, db):
    def _make(structured: dict | None = None, text: list[str] | None = None) -> Agent:
        return Agent(catalog, db, structured or {}, text)

    return _make
