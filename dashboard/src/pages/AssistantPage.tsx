/**
 * The full-page assistant.
 *
 * Same conversation as the slide-over panel — both read the shared AssistantChat context —
 * but a different job. The panel is a glance beside the work; this page is where an analyst
 * sits down with the run, so it is laid out as a conversation surface rather than as another
 * dense console page: one centred column, a composer that takes multi-line input, and a
 * welcome state that makes the assistant's two very different modes legible before the first
 * question is asked.
 *
 * Two things are load-bearing and easy to undo by accident:
 *
 *  - The header states the mode in words, always. Grounded mode answers only from the loaded
 *    run and invents nothing; agent mode calls the platform API under the analyst's own role.
 *    Which one is answering changes how much of the reply needs checking, so it is text, not
 *    a colour.
 *  - The composer sits *under* the welcome block when the transcript is empty and docks to
 *    the bottom once it is not. It is one element in both cases; duplicating it would let the
 *    two copies drift and would lose focus and draft text on the first message.
 */

import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  CornerDownLeft,
  Layers,
  ListChecks,
  Radar,
  RotateCcw,
  ShieldQuestion,
} from "lucide-react";
import {
  AGENT_SUGGESTIONS,
  ChatMessages,
  SUGGESTIONS,
  useAssistant,
} from "../components/AssistantChat";
import { AssistantOrb } from "../components/AssistantOrb";
import { GraphGuardMark } from "../components/Logo";

/**
 * Presentation for the suggestion cards, by position.
 *
 * The prompts themselves stay in AssistantChat, which is what the panel renders too — this
 * table only adds a category and a glyph so the four cards can be told apart at a glance.
 * Indexed rather than keyed on the prompt string so editing a prompt's wording there cannot
 * silently blank a card here; a fifth prompt simply gets the fallback.
 */
const CARD_META = [
  { icon: Radar, kind: "Triage" },
  { icon: Layers, kind: "Cross-layer" },
  { icon: ListChecks, kind: "Queue" },
  { icon: ShieldQuestion, kind: "Detectors" },
];

export function AssistantPage() {
  const { messages, streaming, agentMode, model, ask, reset } = useAssistant();
  const [draft, setDraft] = useState("");

  const suggestions = agentMode ? AGENT_SUGGESTIONS : SUGGESTIONS;
  const empty = messages.length === 0;

  function send(question: string) {
    ask(question);
    setDraft("");
  }

  const composer = (
    <Composer
      value={draft}
      onChange={setDraft}
      onSubmit={() => send(draft)}
      streaming={streaming}
      placeholder={
        streaming
          ? "Working…"
          : agentMode
            ? "Ask anything, or tell me what to investigate…"
            : "Ask about this run — alerts, chains, detectors…"
      }
    />
  );

  return (
    <div className="relative isolate flex h-full min-h-0 flex-col overflow-hidden">
      {/* Decorative only, and only where there is empty space to fill: once a transcript
          exists the analyst is reading, and a drifting glow behind text is noise. */}
      {empty && (
        <div
          className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
          aria-hidden="true"
        >
          <div
            className="gg-aurora absolute top-[-18%] left-1/2 h-[46rem] w-[46rem] -translate-x-1/2 rounded-full blur-3xl"
            style={{
              background:
                "radial-gradient(closest-side, color-mix(in oklab, var(--color-accent) 20%, transparent), transparent 70%)",
            }}
          />
        </div>
      )}

      <header className="flex shrink-0 items-center justify-between gap-3 px-6 py-3">
        <ModeBadge agentMode={agentMode} model={model} />
      </header>

      {empty ? (
        // `my-auto` rather than `items-center`: centring on the cross axis of a scroll
        // container makes the overflow above the child unreachable, so on a short window
        // the orb and the heading would scroll off the top with no way back to them.
        <div className="flex flex-1 justify-center overflow-y-auto px-6">
          <div className="animate-enter my-auto w-full max-w-3xl py-10">
            <div className="flex flex-col items-center text-center">
              <AssistantOrb size={112}>
                <GraphGuardMark size={40} className="relative drop-shadow-sm" />
              </AssistantOrb>

              <h1 className="mt-7 text-[clamp(1.6rem,3.4vw,2.15rem)] leading-tight font-semibold tracking-[-0.02em]">
                How can I help you{" "}
                <span
                  className="bg-clip-text text-transparent"
                  style={{
                    backgroundImage:
                      "linear-gradient(100deg, var(--color-accent), var(--color-accent-soft))",
                  }}
                >
                  investigate
                </span>
                ?
              </h1>

              <p className="mt-3 max-w-lg text-sm leading-relaxed text-[var(--color-ink-dim)]">
                {agentMode
                  ? "I work through the platform's own API under your account and role. Every tool call, its result and its token cost is shown, so you can check each claim against the evidence rather than trust it."
                  : "I answer from the alerts, chains and statistics already loaded in this run. Nothing I say is generated, so there is nothing here to hallucinate — and nothing I cannot show you the source of."}
              </p>
            </div>

            <div className="mx-auto mt-8 max-w-2xl">{composer}</div>

            <div className="mx-auto mt-6 grid max-w-2xl grid-cols-1 gap-2.5 sm:grid-cols-2">
              {suggestions.map((suggestion, index) => (
                <SuggestionCard
                  key={suggestion}
                  index={index}
                  suggestion={suggestion}
                  onSelect={() => send(suggestion)}
                />
              ))}
            </div>
          </div>
        </div>
      ) : (
        <>
          <div data-chat-scroll className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6">
            <div className="mx-auto max-w-3xl space-y-5 pb-6">
              <ChatMessages variant="page" />
            </div>
          </div>
          <div className="shrink-0 px-6 pb-5">
            {/* The transcript scrolls under the composer, so it needs an edge that reads as
                "there is more above" rather than as a hard cut. */}
            <div
              className="pointer-events-none -mt-6 h-6"
              aria-hidden="true"
              style={{
                background:
                  "linear-gradient(to bottom, transparent, var(--color-canvas))",
              }}
            />
            <div className="mx-auto max-w-3xl">
              {composer}
              {/* Below the composer rather than up in the header: starting over is something
                  you decide having just read the last reply, so the control belongs where
                  the eye already is, not at the far end of the page. */}
              <div className="mt-2.5 flex justify-center">
                <button
                  onClick={reset}
                  disabled={streaming}
                  className="flex items-center gap-1.5 rounded-control px-2.5 py-1.5 text-[11px] text-[var(--color-ink-faint)] transition-colors hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)] disabled:opacity-40"
                >
                  <RotateCcw size={11} />
                  New chat
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** Which assistant is answering, in words — see the note at the top of the file. */
function ModeBadge({
  agentMode,
  model,
}: {
  agentMode: boolean;
  model: string | null;
}) {
  return (
    <span className="inline-flex items-center gap-2 rounded-chip border border-[var(--color-line)] bg-[var(--color-surface)] py-1 pr-3 pl-2.5 text-[11px] text-[var(--color-ink-dim)]">
      <span
        className={`size-1.5 rounded-full ${agentMode ? "bg-[var(--color-accent)]" : "bg-[var(--color-ok)]"}`}
        aria-hidden="true"
      />
      {agentMode ? (
        <>
          <span className="font-medium text-[var(--color-ink)]">Agent</span>
          <span className="font-mono text-[10px] text-[var(--color-ink-faint)]">
            {model}
          </span>
          <span className="hidden sm:inline">
            · acting as you, through the platform API
          </span>
        </>
      ) : (
        <>
          <span className="font-medium text-[var(--color-ink)]">Grounded</span>
          <span className="hidden sm:inline">
            · answers read from this run, not generated
          </span>
        </>
      )}
    </span>
  );
}

function SuggestionCard({
  index,
  suggestion,
  onSelect,
}: {
  index: number;
  suggestion: string;
  onSelect: () => void;
}) {
  const meta = CARD_META[index] ?? { icon: Radar, kind: "Ask" };
  const Icon = meta.icon;
  return (
    <button
      onClick={onSelect}
      onPointerMove={(event) => {
        // Written straight to the element: a hover highlight is not application state, and
        // routing it through React would re-render the grid on every pointer sample.
        const box = event.currentTarget.getBoundingClientRect();
        event.currentTarget.style.setProperty(
          "--gg-x",
          `${event.clientX - box.left}px`,
        );
        event.currentTarget.style.setProperty(
          "--gg-y",
          `${event.clientY - box.top}px`,
        );
      }}
      className="gg-spotlight group relative overflow-hidden rounded-card border border-[var(--color-line)] bg-[var(--color-surface)] p-3.5 text-left transition-colors duration-150 ease-out hover:border-[color-mix(in_oklab,var(--color-accent)_45%,var(--color-line))]"
    >
      <span className="relative z-10 flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-field bg-[var(--color-wash-accent)] text-[var(--color-accent)]">
          <Icon size={15} />
        </span>
        <span className="min-w-0">
          <span className="block text-[10px] font-medium tracking-wide text-[var(--color-ink-faint)] uppercase">
            {meta.kind}
          </span>
          <span className="mt-0.5 block text-xs leading-snug text-[var(--color-ink-dim)] transition-colors group-hover:text-[var(--color-ink)]">
            {suggestion}
          </span>
        </span>
      </span>
    </button>
  );
}

/**
 * The composer.
 *
 * A textarea rather than an input, because the questions worth asking an agent run past one
 * line — "investigate X, then check whether Y happened on the same address" is a normal
 * prompt and an <input> makes it unreadable while typing. Enter sends and Shift+Enter breaks
 * the line, which is the convention every chat surface has trained analysts on.
 */
function Composer({
  value,
  onChange,
  onSubmit,
  streaming,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  streaming: boolean;
  placeholder: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow with the content up to a ceiling, then scroll. Height is reset to `auto` first so
  // the box can shrink again when the draft is cut back down or sent.
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  }, [value]);

  const canSend = value.trim().length > 0 && !streaming;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="panel elev-2 relative rounded-overlay p-1.5 transition-colors duration-150 focus-within:border-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-line))]">
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (canSend) onSubmit();
            }
          }}
          placeholder={placeholder}
          disabled={streaming}
          aria-label="Message the assistant"
          className="block max-h-[200px] w-full resize-none bg-transparent px-3.5 py-3 text-sm leading-relaxed focus:outline-none disabled:opacity-60"
        />
        <div className="flex items-center justify-between gap-3 px-2 pt-1 pb-1">
          <span className="flex items-center gap-1.5 text-[10px] text-[var(--color-ink-faint)]">
            <CornerDownLeft size={10} />
            Enter to send · Shift+Enter for a new line
          </span>
          <button
            type="submit"
            aria-label="Send"
            disabled={!canSend}
            className="grid size-8 place-items-center rounded-control bg-[var(--color-accent)] text-[var(--color-on-accent)] transition-opacity duration-150 disabled:opacity-40 motion-safe:active:scale-[0.97]"
          >
            <ArrowUp size={15} />
          </button>
        </div>
      </div>
    </form>
  );
}
