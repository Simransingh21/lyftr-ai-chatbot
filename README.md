# Lyftr AI Full-Stack Assignment

## 1. Live URLS
- Frontend: https://lyftr-ai-chatbot-pied-theta.vercel.app
- Backend: https://lyftr-ai-chatbot-yp5a.onrender.com

## 2. Run locally: with Docker and without
### With Docker
1. Copy `.env.example` to `.env` and add at least two valid API keys in `LLM_API_KEYS`.
2. Run `make up` to build and start both services (`http://localhost:3000` for frontend, `http://localhost:8000` for backend).
3. Run `make down` to stop all containers.

### Without Docker
- **Backend:** `cd backend && pip install -r requirements.txt && DATABASE_URL=sqlite:///./app.db LLM_API_KEYS=k1,k2 ENABLE_FAILURE_SIMULATION=true uvicorn main:app --port 8000`
- **Frontend:** `cd frontend && npm install && VITE_API_URL=http://localhost:8000 npm run dev`

## 3. Design decisions: key fallback, caching, SQLite session storage
- **Key fallback:** Keys from `LLM_API_KEYS` are mapped sequentially to `key_1`, `key_2`, etc. Each request iterates through available keys; any `429`, quota error, or `5xx` (or simulated failure via `X-Simulate-Failure`) increments `fallback_attempts` and tries the next key before returning a clean `503` if all fail.
- **Caching:** LiteLLM's built-in in-memory cache (`litellm.cache = Cache(type="local")`) keys on the model and full message list (system prompt + conversation history + new user message). Cache hits inspect `_hidden_params["cache_hit"]`, set `resolution_source` to `LLM_CACHE`, and clear `key_id` to `null`.
- **SQLite session storage:** Uses Python's standard `sqlite3` against `DATABASE_URL` stored on a persistent `/data` volume. Only successful turns are committed, ensuring failed `422`/`503` requests never pollute session history.

## 4. Verification: how to see failover, cache hits and per-message token usage
- **Cache hit:** Send `"What is the capital of France?"` in a fresh session (`LIVE_INFERENCE`), reload the page to start a new session, and send the exact same prompt (`LLM_CACHE` with lower `latency_ms` and `key_id: null`).
- **Failover:** Select `Simulate key_1=429` in the UI dropdown (or pass `-H "X-Simulate-Failure: key_1=429"` to `POST /chat`) and verify `key_id` becomes `key_2` with `fallback_attempts: 1`.
- **Per-message token usage:** Send multiple turns in one session and observe that `input_tokens` and `output_tokens` reflect each individual request rather than a cumulative sum.

## 5. Setup Used: your editor and AI tools
- Editor: Visual Studio Code connected to GitHub Codespaces.
- AI Tools: Gemini for architecture scaffolding and code generation.

## 6. Bonus features (if any): what you added beyond the requirements and why
- Added an interactive failure-simulation selector directly in the UI header so reviewers can test `key_1=429`, `key_1=500`, and `*=429` without needing cURL.