import { useEffect, useState, useRef } from "react";
import { api, ApiError } from "../services/api";
import type { Message } from "../types";

export function AssistantPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Scroll to bottom when messages change
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      type: "user",
      content: input,
      timestamp: new Date().toISOString(),
    };

    setMessages([...messages, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      // TODO: Implement streaming response from backend
      // For now, simulate a response
      const botMessage: Message = {
        id: Date.now().toString() + "1",
        type: "assistant",
        content: "I'm the DEDAN Remote AI assistant. I can help you find opportunities, understand job requirements, and prepare applications. How can I assist you today?",
        timestamp: new Date().toISOString(),
      };

      // Simulate network delay
      setTimeout(() => {
        setMessages([...messages, botMessage]);
        setLoading(false);
      }, 1000);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to get response from assistant"
      );
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(e as React.FormEvent);
    }
  };

  return (
    <div className="assistant-page">
      <div className="assistant-header">
        <h1>DEDAN Remote Assistant</h1>
        <p className="muted">
          Your AI-powered job search companion
        </p>
      </div>

      <div className="assistant-messages" ref={messagesEndRef}>
        {loading && messages.length === 1 && (
          <div className="assistant-message assistant-bot">
            <p>Thinking...</p>
          </div>
        )}
        {messages.map((message) => (
          <div
            key={message.id}
            className={`assistant-message ${
              message.type === "user" ? "assistant-user" : "assistant-bot"
            }`}
          >
            <p>{message.content}</p>
            <small className="muted">{new Date(message.timestamp).toLocaleTimeString()}</small>
          </div>
        ))}
        {error && (
          <div className="assistant-message assistant-error">
            <p>{error}</p>
          </div>
        )}
      </div>

      <form className="assistant-input-form" onSubmit={sendMessage}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask me about jobs, applications, or career advice…"
          rows={2}
          className="assistant-input"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className={`btn btn-primary ${loading ? "btn-loading" : ""}`}
        >
          {loading ? "Sending..." : "Send"}
        </button>
      </form>
    </div>
  );
}