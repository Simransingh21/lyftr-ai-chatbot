import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import litellm
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from litellm.caching.caching import Cache
from pydantic import BaseModel, Field, field_validator

# Configure LiteLLM built-in in-memory cache
litellm.cache = Cache(type="local")
litellm.drop_params = True

MODEL_NAME = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
ENABLE_FAILURE_SIMULATION = (
    os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
)
DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise AI assistant."


def get_key_pool() -> List[Dict[str, str]]:
    raw_keys = os.getenv("LLM_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    return [{"key_id": f"key_{i + 1}", "api_key": k} for i, k in enumerate(keys)]


def get_db_path() -> str:
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL[len("sqlite:///") :]
    return DATABASE_URL


def get_db() -> sqlite3.Connection:
    db_path = get_db_path()
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
            """
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Lyftr AI Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str
    system_prompt: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        trimmed = v.strip()
        if not (1 <= len(trimmed) <= 128):
            raise ValueError("session_id must be 1-128 characters")
        return trimmed

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        trimmed = v.strip()
        if not (1 <= len(trimmed) <= 4000):
            raise ValueError("message must be 1-4000 characters after trimming")
        return trimmed


def parse_simulated_failures(header_val: Optional[str]) -> Dict[str, int]:
    if not ENABLE_FAILURE_SIMULATION or not header_val:
        return {}
    failures: Dict[str, int] = {}
    tokens = [t.strip() for t in re.split(r"[,\s]+", header_val.strip()) if t.strip()]
    for token in tokens:
        if "=" in token:
            k, code_str = token.split("=", 1)
            if code_str.isdigit() and int(code_str) in {429, 500, 502, 503}:
                failures[k.strip()] = int(code_str)
    return failures


@app.get("/health")
def health():
    keys = get_key_pool()
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {
        "status": "ok",
        "keys_configured": len(keys),
        "cache_enabled": True,
    }


@app.post("/chat")
def chat(
    req: ChatRequest,
    x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure"),
):
    start_time = time.perf_counter()
    keys = get_key_pool()
    if not keys:
        raise HTTPException(status_code=503, detail="all providers unavailable")

    conn = get_db()
    try:
        # Load or initialize session system prompt
        sess_row = conn.execute(
            "SELECT system_prompt FROM sessions WHERE session_id = ?",
            (req.session_id,),
        ).fetchone()

        if sess_row is None:
            active_system_prompt = (
                req.system_prompt.strip()
                if req.system_prompt and req.system_prompt.strip()
                else DEFAULT_SYSTEM_PROMPT
            )
        else:
            active_system_prompt = (
                req.system_prompt.strip()
                if req.system_prompt and req.system_prompt.strip()
                else sess_row["system_prompt"]
            )

        # Fetch existing history
        history_rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
            (req.session_id,),
        ).fetchall()

        llm_messages: List[Dict[str, str]] = [
            {"role": "system", "content": active_system_prompt}
        ]
        for row in history_rows:
            llm_messages.append({"role": row["role"], "content": row["content"]})
        llm_messages.append({"role": "user", "content": req.message})

        simulated = parse_simulated_failures(x_simulate_failure)
        bypass_cache = len(simulated) > 0
        fallback_attempts = 0
        response = None
        winning_key_id: Optional[str] = None

        for entry in keys:
            kid = entry["key_id"]
            if "*" in simulated or kid in simulated:
                fallback_attempts += 1
                continue

            try:
                call_kwargs: Dict[str, Any] = {
                    "model": MODEL_NAME,
                    "messages": llm_messages,
                    "api_key": entry["api_key"],
                }
                if bypass_cache:
                    call_kwargs["cache"] = {"no-cache": True}

                response = litellm.completion(**call_kwargs)
                winning_key_id = kid
                break
            except Exception:
                fallback_attempts += 1
                continue

        if response is None:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        hidden = getattr(response, "_hidden_params", {}) or {}
        is_cache_hit = bool(hidden.get("cache_hit", False)) and not bypass_cache

        reply_text = (response.choices[0].message.content or "").strip()
        if not reply_text:
            reply_text = "No response generated."

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))

        metadata = {
            "resolution_source": "LLM_CACHE" if is_cache_hit else "LIVE_INFERENCE",
            "model": MODEL_NAME,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": None if is_cache_hit else winning_key_id,
            "fallback_attempts": 0 if is_cache_hit else fallback_attempts,
        }

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO sessions (session_id, system_prompt, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt
            """,
            (req.session_id, active_system_prompt, now_iso),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (req.session_id, "user", req.message, None, now_iso),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (req.session_id, "assistant", reply_text, json.dumps(metadata), now_iso),
        )
        conn.commit()

        return {
            "session_id": req.session_id,
            "reply": reply_text,
            "metadata": metadata,
        }
    finally:
        conn.close()


@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="session not found")

        messages = []
        for r in rows:
            item: Dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["role"] == "assistant" and r["metadata"]:
                item["metadata"] = json.loads(r["metadata"])
            messages.append(item)

        return {"session_id": session_id, "messages": messages}
    finally:
        conn.close()