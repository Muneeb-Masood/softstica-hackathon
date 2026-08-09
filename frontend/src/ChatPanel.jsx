import { useState } from "react";
import { sendChatMessage } from "./api";

// Read-only Q&A over the ranking currently on screen. The chat never
// triggers a re-rank -- every answer comes from backend/chat_qa.py, which
// is grounded strictly in `context` (the exact ranked/excluded/
// explanations payload) and never touches combined_ranking.py or
// trust_score.py. See CLAUDE.md Section 7: this stays a simulation
// explainer, never live guidance.
//
// Also forwards `timeline` (the already-fetched /rank/timeline `hours`
// array behind the Time Slider) when App.jsx has it, so questions like
// "why doesn't this AED show closed?" can be answered from the same
// precomputed day-long data the slider itself uses -- see chat_qa.py's
// _build_timeline_block. Nothing new is fetched here for this.
//
// Rendered as a floating widget docked to the right edge of the screen
// (not buried at the bottom of the scrollable results list) so it stays
// reachable regardless of how long the ranked/excluded list is. The
// caller only mounts this once a real ranking exists (see App.jsx), so
// there is nothing to ask about until "Find AEDs" has actually run.
const MAX_MESSAGES = 20;

export default function ChatPanel({ context, timeline, sessionId }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [remaining, setRemaining] = useState(MAX_MESSAGES);
  const [limitReached, setLimitReached] = useState(false);

  const handleSend = async (e) => {
    e.preventDefault();
    const question = input.trim();
    if (!question || sending || limitReached) return;

    const history = messages;
    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setInput("");
    setSending(true);
    setError(null);

    try {
      const data = await sendChatMessage({
        sessionId,
        question,
        context: {
          query: context.query,
          ranked: context.ranked,
          excluded: context.excluded,
          explanations: context.explanations,
          timeline: timeline?.hours ?? null,
        },
        history,
      });
      setMessages((prev) => [...prev, { role: "assistant", text: data.answer }]);
      setRemaining(data.messages_remaining);
      if (data.messages_remaining <= 0) setLimitReached(true);
    } catch (err) {
      if (err.status === 429) {
        setLimitReached(true);
        setRemaining(0);
      }
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        className="chat-widget-tab"
        onClick={() => setOpen(true)}
        aria-label="Ask about these results"
      >
        <span className="chat-widget-tab-icon" aria-hidden="true">💬</span>
        <span className="chat-widget-tab-label">Ask about results</span>
      </button>
    );
  }

  return (
    <div className="chat-widget">
      <div className="chat-panel-head">
        <span className="chat-panel-title">Ask about these results</span>
        <div className="chat-panel-head-right">
          <span className="chat-panel-remaining">{remaining} left</span>
          <button
            type="button"
            className="chat-widget-close"
            onClick={() => setOpen(false)}
            aria-label="Minimize chat"
          >
            ✕
          </button>
        </div>
      </div>
      <p className="chat-panel-note">
        Answers are grounded only in the ranking on screen and the Time Slider's
        precomputed hours for today — not live traffic, live availability, or
        anything else. This does not re-rank or recalculate.
      </p>

      {messages.length > 0 && (
        <div className="chat-messages">
          {messages.map((m, i) => (
            <div key={i} className={`chat-message chat-message-${m.role}`}>
              <span className="chat-message-role">{m.role === "user" ? "You" : "Assistant"}</span>
              <p>{m.text}</p>
            </div>
          ))}
          {sending && (
            <div className="chat-message chat-message-assistant">
              <span className="chat-message-role">Assistant</span>
              <p className="chat-message-pending">Thinking…</p>
            </div>
          )}
        </div>
      )}

      {error && <p className="chat-panel-error">{error}</p>}
      {limitReached && (
        <p className="chat-panel-error">
          Question limit reached for this session ({MAX_MESSAGES}). Reload the page to reset it.
        </p>
      )}

      <form className="chat-panel-form" onSubmit={handleSend}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. why doesn't this one show closed today?"
          disabled={sending || limitReached}
          maxLength={500}
          autoFocus
        />
        <button type="submit" disabled={sending || limitReached || !input.trim()}>
          {sending ? "…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
