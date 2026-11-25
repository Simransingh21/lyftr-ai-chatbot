import React, { useState } from 'react';

const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

function generateSessionId() {
  return 's-' + Math.random().toString(36).substring(2, 10);
}

export default function App() {
  const [sessionId] = useState(() => generateSessionId());
  const [systemPrompt, setSystemPrompt] = useState('');
  const [simHeader, setSimHeader] = useState('');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  const sendMessage = async (e) => {
    if (e) e.preventDefault();
    const text = input;
    if (loading) return;

    setInput('');
    setLoading(true);

    if (text.trim()) {
      setMessages((prev) => [...prev, { role: 'user', content: text }]);
    }

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (simHeader) {
        headers['X-Simulate-Failure'] = simHeader;
      }

      const payload = {
        session_id: sessionId,
        message: text,
      };
      if (systemPrompt.trim()) {
        payload.system_prompt = systemPrompt.trim();
      }

      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });

      const data = await res.json();

      if (!res.ok) {
        const detail =
          typeof data.detail === 'string'
            ? data.detail
            : JSON.stringify(data.detail || 'Request failed');
        setMessages((prev) => [
          ...prev,
          { role: 'error', content: `Error ${res.status}: ${detail}` },
        ]);
        return;
      }

      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.reply,
          metadata: data.metadata,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'error', content: `Network error: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <div className="header">
        <h1>Lyftr AI Gateway Chat</h1>
        <span className="session-badge">Session: {sessionId}</span>
      </div>

      <div className="controls-bar">
        <input
          type="text"
          data-testid="system-prompt-input"
          placeholder="Optional system prompt for this session..."
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
        />
        <select
          value={simHeader}
          onChange={(e) => setSimHeader(e.target.value)}
          title="Simulate Key Failure Header"
        >
          <option value="">Normal (No Failure Sim)</option>
          <option value="key_1=429">Simulate key_1=429</option>
          <option value="key_1=500">Simulate key_1=500</option>
          <option value="*=429">Simulate *=429 (All Fail 503)</option>
        </select>
      </div>

      <div className="chat-window">
        {messages.map((msg, idx) => {
          if (msg.role === 'user') {
            return (
              <div key={idx} className="message user">
                {msg.content}
              </div>
            );
          }
          if (msg.role === 'error') {
            return (
              <div key={idx} className="message error">
                {msg.content}
              </div>
            );
          }
          const m = msg.metadata || {};
          const isCache = m.resolution_source === 'LLM_CACHE';
          return (
            <div key={idx} className="message assistant" data-testid="assistant-message">
              <div>{msg.content}</div>
              <div className="metadata-bar">
                <span
                  className={`meta-pill ${isCache ? 'cache' : 'live'}`}
                  data-testid="meta-source"
                >
                  {m.resolution_source}
                </span>
                <span className="meta-pill" data-testid="meta-model">
                  {m.model}
                </span>
                <span className="meta-pill" data-testid="meta-latency">
                  {m.latency_ms} ms
                </span>
                <span className="meta-pill">
                  in: <span data-testid="meta-input-tokens">{m.input_tokens}</span>
                </span>
                <span className="meta-pill">
                  out: <span data-testid="meta-output-tokens">{m.output_tokens}</span>
                </span>
                {m.key_id && <span className="meta-pill">key: {m.key_id}</span>}
                {m.fallback_attempts > 0 && (
                  <span className="meta-pill">fallbacks: {m.fallback_attempts}</span>
                )}
              </div>
            </div>
          );
        })}
        {loading && <div className="message assistant">Thinking...</div>}
      </div>

      <form className="input-area" onSubmit={sendMessage}>
        <input
          type="text"
          data-testid="chat-input"
          placeholder="Type a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit" data-testid="send-button" disabled={loading}>
          Send
        </button>
      </form>
    </div>
  );
}