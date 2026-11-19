/**
 * The analyst assistant, as a slide-over panel.
 *
 * A thin surface over the shared chat state in AssistantChat.tsx — the same
 * conversation continues in the full-page view (/assistant), which the expand button
 * opens. The panel follows the workspace theme like every other neutral surface; the
 * brand navy stays on the sidebar, where the identity belongs.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUp, Maximize2, Sparkles, X } from "lucide-react";
import { AGENT_SUGGESTIONS, ChatMessages, SUGGESTIONS, useAssistant } from "./AssistantChat";

export function Assistant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { messages, streaming, agentMode, model, ask } = useAssistant();
  const [draft, setDraft] = useState("");

  if (!open) return null;

  const suggestions = agentMode ? AGENT_SUGGESTIONS : SUGGESTIONS;

  function send(question: string) {
    ask(question);
    setDraft("");
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="Analyst assistant">
      <button
        className="flex-1 bg-[color-mix(in_oklab,var(--color-canvas)_70%,transparent)] backdrop-blur-[2px]"
        onClick={onClose}
        aria-label="Close assistant"
      />
      <div className="panel elev-3 flex h-full w-full max-w-[440px] flex-col rounded-none border-y-0 border-r-0">
        <header className="flex items-center gap-3 border-b border-[var(--color-line)] px-5 py-4">
          <span className="grid size-9 place-items-center rounded-xl bg-[var(--color-wash-accent)] text-[var(--color-accent)]">
            <Sparkles size={17} />
          </span>
          <div className="flex-1">
            <div className="text-sm font-semibold">Analyst assistant</div>
            <div className="text-[11px] text-[var(--color-ink-faint)]">
              {agentMode ? `Agent · ${model}` : "Answers from this run’s data"}
            </div>
          </div>
          <button
            onClick={() => {
              navigate("/assistant");
              onClose();
            }}
            aria-label="Open full chat"
            title="Open full chat"
            className="grid size-8 place-items-center rounded-control text-[var(--color-ink-dim)] hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]"
          >
            <Maximize2 size={15} />
          </button>
          <button
            onClick={onClose}
            aria-label="Close"
            className="grid size-8 place-items-center rounded-control text-[var(--color-ink-dim)] hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]"
          >
            <X size={16} />
          </button>
        </header>

        <div data-chat-scroll className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-5">
          {messages.length === 0 && (
            <>
              <div className="rounded-overlay border border-[var(--color-line)] bg-[var(--color-surface-2)] p-4 text-xs leading-relaxed text-[var(--color-ink-dim)]">
                {agentMode ? (
                  <>
                    I investigate and act through the platform&rsquo;s own API, under your
                    account and role. Every tool call, its result, my reasoning summary
                    and the token cost are shown — check my claims against the evidence.
                  </>
                ) : (
                  <>
                    I answer from the alerts, chains and statistics currently loaded — I do
                    not generate text, so nothing I say is invented. Ask about what is
                    happening now, or about what a detector does.
                  </>
                )}
              </div>
              <div className="space-y-2">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-left text-xs text-[var(--color-ink-dim)] transition hover:border-[var(--color-accent)]/40 hover:text-[var(--color-ink)]"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </>
          )}

          <ChatMessages onNavigate={onClose} />
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(draft);
          }}
          className="border-t border-[var(--color-line)] p-4"
        >
          <div className="relative">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={streaming ? "Working…" : "Ask about current threats…"}
              disabled={streaming}
              className="w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] py-3 pr-11 pl-4 text-xs focus:border-[var(--color-accent)]/50 focus:outline-none disabled:opacity-60"
            />
            <button
              type="submit"
              aria-label="Send"
              className="absolute top-1/2 right-2 grid size-7 -translate-y-1/2 place-items-center rounded-lg bg-[var(--color-accent)] text-[var(--color-on-accent)] disabled:opacity-40"
              disabled={!draft.trim() || streaming}
            >
              <ArrowUp size={14} />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
