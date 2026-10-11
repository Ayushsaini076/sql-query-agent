"""FastAPI application: chat API (JSON + Server-Sent Events) and the built frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import NODE_LABELS, build_graph
from app.agent.llm import create_models, message_text
from app.agent.nodes import SQLAgentNodes
from app.agent.state import new_turn
from app.config import Settings, get_settings
from app.db.catalog import SchemaCatalog
from app.db.executor import ReadOnlyDatabase
from app.db.seed import build_database

logger = logging.getLogger("sql_agent")
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
STREAMED_NODES = {"generate_explanation", "answer_question"}


class ChatRequest(BaseModel):
    session_id: str = Field(description="Client-generated conversation id")
    message: str
    execute: bool = True


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        if len(self.hits) > 10_000:  # drop idle clients so the table can't grow without bound
            for k in [k for k, w in self.hits.items() if not w or now - w[-1] > 60]:
                del self.hits[k]
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            raise HTTPException(429, "Too many requests – please wait a minute and try again.")
        window.append(now)


def create_app(settings: Settings | None = None, *, llm=None, fast_llm=None) -> FastAPI:
    """Build the app. `llm`/`fast_llm` can be injected (tests); otherwise they come from settings."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        build_database(settings.database_path)
        catalog = SchemaCatalog.from_sqlite(settings.database_path)
        db = ReadOnlyDatabase(settings.database_path, timeout_s=settings.query_timeout_s)
        main_llm, router_llm = llm, fast_llm
        if main_llm is None and settings.llm_configured:
            main_llm, router_llm = create_models(settings)
        app.state.catalog = catalog
        app.state.llm_ready = main_llm is not None
        settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
            app.state.checkpointer = saver
            app.state.graph = None
            if main_llm is not None:
                nodes = SQLAgentNodes(
                    llm=main_llm, fast_llm=router_llm, provider=settings.llm_provider, catalog=catalog, db=db,
                    max_attempts=settings.max_sql_attempts, history_turns=settings.history_turns,
                    max_input_chars=settings.max_input_chars, max_result_rows=settings.max_result_rows,
                )
                app.state.graph = build_graph(nodes, saver)
            yield

    app = FastAPI(title="SQL Query Agent", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"],
    )
    limiter = RateLimiter(settings.rate_limit_per_minute)
    session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _client_key(request: Request) -> str:
        peer = request.client.host if request.client else "?"
        hops = settings.trusted_proxy_hops
        if hops <= 0:
            return peer
        # Each trusted proxy appends the address it saw; entries further left are client-controlled.
        chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        return chain[-hops] if len(chain) >= hops else peer

    def _prepare(request: Request, body: ChatRequest):
        if not SESSION_ID.match(body.session_id):
            raise HTTPException(422, "Invalid session_id.")
        graph = request.app.state.graph
        if graph is None:
            raise HTTPException(503, "The language model is not configured on the server (missing API key).")
        limiter.check(_client_key(request))
        if session_locks[body.session_id].locked():
            raise HTTPException(409, "This conversation is still answering the previous message.")
        return graph, {"configurable": {"thread_id": body.session_id}}

    # ------------------------------------------------------------------ routes
    @app.get("/api/health")
    async def health(request: Request) -> dict:
        return {
            "status": "ok",
            "llm_ready": request.app.state.llm_ready,
            "provider": settings.llm_provider,
            "model": settings.resolved_model,
            "fallback_provider": settings.llm_fallback_provider if settings.fallback_configured else None,
        }

    @app.get("/api/schema")
    async def schema(request: Request) -> dict:
        return request.app.state.catalog.to_dict()

    @app.post("/api/chat")
    async def chat(request: Request, body: ChatRequest) -> dict:
        graph, config = _prepare(request, body)
        async with session_locks[body.session_id]:
            try:
                state = await graph.ainvoke(new_turn(body.message, execute=body.execute), config)
            except Exception as exc:
                logger.exception("agent run failed")
                raise HTTPException(502, _friendly_error(exc)) from exc
        return state["response"]

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request, body: ChatRequest) -> EventSourceResponse:
        graph, config = _prepare(request, body)

        async def events() -> AsyncIterator[dict[str, str]]:
            async with session_locks[body.session_id]:
                async for event in _run(graph, config, body):
                    yield event

        return EventSourceResponse(events(), ping=15)

    async def _run(graph, config, body: ChatRequest) -> AsyncIterator[dict[str, str]]:
        try:
            async for mode, payload in graph.astream(
                new_turn(body.message, execute=body.execute), config, stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    chunk, meta = payload
                    if meta.get("langgraph_node") in STREAMED_NODES:
                        text = message_text(chunk)
                        if text:
                            yield _sse("token", {"node": meta["langgraph_node"], "text": text})
                    continue
                for node, update in payload.items():
                    if node in NODE_LABELS:
                        yield _sse("step", {"node": node, "label": NODE_LABELS[node], "detail": _step_detail(node, update or {})})
            state = await graph.aget_state(config)
            yield _sse("result", state.values["response"])
        except Exception as exc:
            logger.exception("agent stream failed")
            yield _sse("error", {"message": _friendly_error(exc)})

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str) -> dict:
        if not SESSION_ID.match(session_id):
            raise HTTPException(422, "Invalid session_id.")
        await request.app.state.checkpointer.adelete_thread(session_id)
        return {"deleted": session_id}

    _mount_frontend(app, settings)
    return app


def _sse(event: str, data: Any) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data, default=str)}


def _step_detail(node: str, update: dict) -> str:
    if node == "classify_intent":
        return (update.get("intent") or "").replace("_", " ")
    if node == "check_user_sql":
        if update.get("intent") == "destructive":
            return "write operation detected"
        return "has errors, switching to debugging" if update.get("routed_to_debug") else "looks valid"
    if node == "retrieve_schema":
        return ", ".join(update.get("relevant_tables") or [])
    if node == "validate_sql":
        v = update.get("validation") or {}
        attempt = update.get("attempts", 1)
        if v.get("valid"):
            return f"passed (attempt {attempt})" + (f", {len(v['warnings'])} warning(s)" if v.get("warnings") else "")
        return f"failed (attempt {attempt}): {(v.get('errors') or ['?'])[0][:120]}"
    if node == "analyze_sql":
        cost = (update.get("analysis") or {}).get("cost") or {}
        return f"estimated cost: {cost.get('level', '?')}"
    if node == "execute_sql":
        if update.get("execution_error"):
            return f"error: {update['execution_error']}"
        ex = update.get("execution")
        return f"{ex['row_count']}{'+' if ex['truncated'] else ''} rows in {ex['elapsed_ms']} ms" if ex else "skipped"
    if node == "guard_input" and update.get("refusal_reason"):
        return update["refusal_reason"].replace("_", " ")
    return ""


def _friendly_error(exc: Exception) -> str:
    name = type(exc).__name__
    if "RateLimit" in name:
        return "The language model provider is rate-limiting requests. Please try again shortly."
    if "Authentication" in name or "PermissionDenied" in name:
        return "The server's language model credentials were rejected."
    if "Timeout" in name:
        return "The language model took too long to respond. Please try again."
    return f"Something went wrong while processing your request ({name}). Please try again."


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    static = settings.static_dir
    if not (static / "index.html").exists():
        return
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (static / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static.resolve()):
            return FileResponse(candidate)
        return FileResponse(static / "index.html")


app = create_app()
