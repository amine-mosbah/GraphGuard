/**
 * Shared state and rendering for the analyst assistant.
 *
 * One conversation, two surfaces: the slide-over panel and the full-page chat both
 * consume this context, so expanding the panel into the page mid-conversation keeps
 * the transcript, the streaming state and the capability probe. The provider owns the
 * SSE loop; the surfaces own only their layout.
 */

/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import { ChevronRight, Wrench } from "lucide-react";
import { api } from "../lib/api";
import type { AlertSummary, AttackChain, PlatformStats } from "../lib/types";
import { severityOf } from "./ui";
import { Markdown } from "./Markdown";
import { AssistantWorking } from "./AssistantOrb";
import { GraphGuardMark } from "./Logo";

export interface ToolSegment {
  kind: "tool";
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: unknown;
  isError?: boolean;
}

export type Segment =
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "link"; path: string }
  | ToolSegment;

export interface Message {
  role: "user" | "assistant";
  text: string;
  segments?: Segment[];
  usage?: { input_tokens: number; output_tokens: number };
  links?: { label: string; to: string }[];
}

interface Capabilities {
  enabled: boolean;
  model: string | null;
}

export const SUGGESTIONS = [
  "What needs my attention right now?",
  "Any cross-layer attack chains?",
  "How is the queue looking?",
  "What does community drift detect?",
];

export const AGENT_SUGGESTIONS = [
  "Investigate the worst open alert and tell me what you find",
  "Walk me through the attack chain, step by step",
  "Which detector produced the most open alerts?",
  "Summarise the queue and take me to the worst alert",
];

interface AssistantState {
  messages: Message[];
  streaming: boolean;
  agentMode: boolean;
  model: string | null;
  ask: (question: string) => void;
  reset: () => void;
}

const AssistantContext = createContext<AssistantState | null>(null);

export function useAssistant(): AssistantState {
  const state = useContext(AssistantContext);
  if (!state) throw new Error("useAssistant outside AssistantProvider");
  return state;
}

export function AssistantProvider({
  alerts,
  chains,
  stats,
  children,
}: {
  alerts: AlertSummary[];
  chains: AttackChain[];
  stats: PlatformStats | null;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);

  useEffect(() => {
    api
      .get<Capabilities>("/api/v1/assistant/capabilities")
      .then(setCapabilities)
      .catch(() => setCapabilities({ enabled: false, model: null }));
  }, []);

  const agentMode = capabilities?.enabled === true;

  /** Mutate the trailing assistant message through the updater, immutably. */
  function patchLast(update: (message: Message) => Message) {
    setMessages((previous) => [
      ...previous.slice(0, -1),
      update(previous[previous.length - 1]),
    ]);
  }

  function appendSegment(message: Message, segment: Segment): Message {
    const segments = message.segments ? [...message.segments] : [];
    const last = segments[segments.length - 1];
    if (
      (segment.kind === "text" || segment.kind === "thinking") &&
      last &&
      last.kind === segment.kind
    ) {
      segments[segments.length - 1] = {
        kind: segment.kind,
        text: (last as { text: string }).text + segment.text,
      };
    } else {
      segments.push(segment);
    }
    const text =
      segment.kind === "text" ? message.text + segment.text : message.text;
    return { ...message, segments, text };
  }

  function handleEvent(event: Record<string, unknown>) {
    switch (event.type) {
      case "text":
        patchLast((m) =>
          appendSegment(m, { kind: "text", text: String(event.text) }),
        );
        break;
      case "thinking":
        patchLast((m) =>
          appendSegment(m, { kind: "thinking", text: String(event.text) }),
        );
        break;
      case "tool_use":
        patchLast((m) =>
          appendSegment(m, {
            kind: "tool",
            id: String(event.id),
            name: String(event.name),
            input: (event.input ?? {}) as Record<string, unknown>,
          }),
        );
        break;
      case "tool_result":
        patchLast((m) => ({
          ...m,
          segments: (m.segments ?? []).map((segment) =>
            segment.kind === "tool" && segment.id === event.id
              ? {
                  ...segment,
                  result: event.result,
                  isError: Boolean(event.is_error),
                }
              : segment,
          ),
        }));
        break;
      case "navigate":
        // On the full-page chat, yanking the analyst off the conversation mid-answer
        // is worse than not navigating — render an inline chip they can click instead.
        if (window.location.pathname === "/assistant") {
          patchLast((m) =>
            appendSegment(m, { kind: "link", path: String(event.path) }),
          );
        } else {
          navigate(String(event.path));
        }
        break;
      case "usage":
        patchLast((m) => ({
          ...m,
          usage: {
            input_tokens: Number(event.input_tokens),
            output_tokens: Number(event.output_tokens),
          },
        }));
        break;
      case "error":
        patchLast((m) =>
          appendSegment(m, {
            kind: "text",
            text: `\n⚠ ${String(event.message)}`,
          }),
        );
        break;
    }
  }

  async function askAgent(history: Message[]) {
    setMessages([...history, { role: "assistant", text: "", segments: [] }]);
    setStreaming(true);
    try {
      const response = await api.stream("/api/v1/assistant/chat", {
        messages: history
          .filter((message) => message.text.trim().length > 0)
          .slice(-20)
          .map((message) => ({ role: message.role, content: message.text })),
      });
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          handleEvent(JSON.parse(frame.slice(6)));
        }
      }
    } catch (error) {
      patchLast((message) =>
        appendSegment(message, {
          kind: "text",
          text: `\n⚠ ${error instanceof Error ? error.message : "stream failed"}`,
        }),
      );
    } finally {
      setStreaming(false);
    }
  }

  const ask = useCallback(
    (question: string) => {
      const text = question.trim();
      if (!text || streaming) return;
      const history = [...messages, { role: "user" as const, text }];
      if (agentMode) {
        void askAgent(history);
      } else {
        setMessages([...history, localAnswer(text, alerts, chains, stats)]);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [messages, streaming, agentMode, alerts, chains, stats],
  );

  const reset = useCallback(() => setMessages([]), []);

  return (
    <AssistantContext.Provider
      value={{
        messages,
        streaming,
        agentMode,
        model: capabilities?.model ?? null,
        ask,
        reset,
      }}
    >
      {children}
    </AssistantContext.Provider>
  );
}

// --------------------------------------------------------------------------- rendering

/**
 * The transcript, shared by the panel and the full page.
 *
 * `variant` is layout only — the same messages, either squeezed into a 440 px rail or given
 * a reading column. The page variant drops the bubble around assistant replies: at full
 * width a border around every reply boxes in the tool calls and reasoning blocks that are
 * already boxed, and the alternating alignment is what separates the turns anyway.
 */
export function ChatMessages({
  onNavigate,
  variant = "panel",
}: {
  onNavigate?: () => void;
  variant?: "panel" | "page";
}) {
  const { messages, streaming } = useAssistant();
  const navigate = useNavigate();
  const endRef = useRef<HTMLDivElement>(null);
  const page = variant === "page";

  useEffect(() => {
    // Scroll only the transcript. scrollIntoView also moves outer ancestors,
    // which can push the composer and conversation above the viewport.
    const transcript = endRef.current?.closest<HTMLElement>("[data-chat-scroll]");
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [messages]);

  return (
    <>
      {messages.map((message, index) => (
        <div
          key={index}
          className={
            message.role === "user" ? "flex justify-end" : "flex justify-start"
          }
        >
          {message.role === "user" ? (
            <div
              className={
                page
                  ? "max-w-[80%] rounded-2xl rounded-br-sm bg-[var(--color-accent)] px-4 py-3 text-sm font-medium whitespace-pre-wrap text-[var(--color-on-accent)]"
                  : "max-w-[85%] rounded-2xl rounded-br-sm bg-[var(--color-accent)] px-3.5 py-2.5 text-xs font-medium text-[var(--color-on-accent)]"
              }
            >
              {message.text}
            </div>
          ) : (
            <div
              className={
                page ? "flex w-full gap-3" : "w-full max-w-[92%] space-y-2"
              }
            >
              {/* At full width the alternating alignment alone stops separating turns once
                  a reply runs past a screenful, so the page marks who is speaking. */}
              {page && (
                <span
                  className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-[var(--color-wash-accent)]"
                  aria-hidden="true"
                >
                  <GraphGuardMark size={15} />
                </span>
              )}
              <div className="min-w-0 flex-1 space-y-2">
                {message.segments ? (
                  <AgentReply
                    message={message}
                    variant={variant}
                    streaming={streaming && index === messages.length - 1}
                  />
                ) : (
                  <div
                    className={
                      page
                        ? "text-[13.5px] leading-relaxed whitespace-pre-line text-[var(--color-ink)]"
                        : "rounded-2xl rounded-bl-sm border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-3 text-xs leading-relaxed whitespace-pre-line text-[var(--color-ink-dim)]"
                    }
                  >
                    {message.text}
                    {message.links && message.links.length > 0 && (
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {message.links.map((link) => (
                          <button
                            key={link.to + link.label}
                            onClick={() => {
                              navigate(link.to);
                              onNavigate?.();
                            }}
                            className="rounded-lg bg-[var(--color-wash-accent)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-accent)] hover:bg-[var(--color-raise-strong)]"
                          >
                            {link.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </>
  );
}

/** One agent reply: reasoning, tool calls and prose in the order they happened. */
function AgentReply({
  message,
  streaming,
  variant = "panel",
}: {
  message: Message;
  streaming: boolean;
  variant?: "panel" | "page";
}) {
  const navigate = useNavigate();
  const segments = message.segments ?? [];
  const page = variant === "page";
  return (
    <div
      className={
        page
          ? "text-[13.5px] leading-relaxed text-[var(--color-ink)]"
          : "rounded-2xl rounded-bl-sm border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-3 text-xs leading-relaxed text-[var(--color-ink-dim)]"
      }
    >
      {/* Nothing has arrived yet. An agent turn opens with a tool call and can sit here for
          tens of seconds, so the page — which has the room — shows the orb working rather
          than a static word that is indistinguishable from a stalled stream. */}
      {segments.length === 0 &&
        streaming &&
        (page ? (
          <AssistantWorking size={72} text="Generating" />
        ) : (
          <span className="text-[var(--color-ink-faint)]">Thinking…</span>
        ))}
      <div className="space-y-2">
        {segments.map((segment, index) =>
          segment.kind === "text" ? (
            <Markdown key={index} text={segment.text} />
          ) : segment.kind === "thinking" ? (
            <details
              key={index}
              className="group rounded-lg bg-[var(--color-raise-subtle)] px-2.5 py-1.5"
            >
              <summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] text-[var(--color-ink-faint)]">
                <ChevronRight
                  size={11}
                  className="transition group-open:rotate-90"
                />
                Reasoning
              </summary>
              <div className="mt-1.5 text-[11px] text-[var(--color-ink-faint)]">
                <Markdown text={segment.text} />
              </div>
            </details>
          ) : segment.kind === "link" ? (
            <button
              key={index}
              onClick={() => navigate(segment.path)}
              className="flex items-center gap-1.5 rounded-lg bg-[var(--color-wash-accent)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-accent)] hover:bg-[var(--color-raise-strong)]"
            >
              Open {segment.path}
              <ChevronRight size={11} />
            </button>
          ) : (
            <ToolChip key={segment.id} segment={segment} />
          ),
        )}
      </div>
      {message.usage && (
        <div className="mt-2.5 border-t border-[var(--color-hairline)] pt-1.5 text-[10px] text-[var(--color-ink-faint)]">
          {message.usage.input_tokens.toLocaleString()} in ·{" "}
          {message.usage.output_tokens.toLocaleString()} out tokens
        </div>
      )}
    </div>
  );
}

function ToolChip({ segment }: { segment: ToolSegment }) {
  const pending = segment.result === undefined;
  const args = Object.entries(segment.input)
    .filter(
      ([, value]) => value !== "" && value !== undefined && value !== null,
    )
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join("  ");
  return (
    <details className="group overflow-hidden rounded-lg border border-[var(--color-accent)]/25 bg-[var(--color-wash-accent)]">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-1.5 text-[11px]">
        <Wrench
          size={11}
          className={
            segment.isError
              ? "shrink-0 text-[var(--color-critical)]"
              : pending
                ? "shrink-0 animate-pulse text-[var(--color-ink-faint)]"
                : "shrink-0 text-[var(--color-accent)]"
          }
        />
        <span className="shrink-0 font-mono font-semibold text-[var(--color-accent)]">
          {segment.name}
        </span>
        <span className="truncate font-mono text-[10px] text-[var(--color-ink-dim)]">
          {args}
        </span>
        <ChevronRight
          size={11}
          className="ml-auto shrink-0 text-[var(--color-ink-faint)] transition group-open:rotate-90"
        />
      </summary>
      <pre className="max-h-48 overflow-auto border-t border-[var(--color-accent)]/15 bg-[var(--color-sink)] p-2.5 font-mono text-[10px] leading-snug whitespace-pre-wrap text-[var(--color-ink-dim)]">
        {pending ? "running…" : JSON.stringify(segment.result, null, 2)}
      </pre>
    </details>
  );
}

// --------------------------------------------------------------------------- local mode

/** Answer from the data on screen — the default when no language model is configured. */
function localAnswer(
  question: string,
  alerts: AlertSummary[],
  chains: AttackChain[],
  stats: PlatformStats | null,
): Message {
  const q = question.toLowerCase();
  const open = alerts.filter(
    (a) => a.state === "new" || a.state === "investigating",
  );
  const ranked = [...open].sort((a, b) => b.score - a.score);

  if (/(chain|cross.?layer|pivot|lateral)/.test(q)) {
    if (chains.length === 0) {
      return {
        role: "assistant",
        text: "No cross-layer attack chains in this run. A chain is only reported when an external burst against the gateway is followed by a successful credential use from the same address — the absence of one is a meaningful result, not a gap.",
      };
    }
    const chain = chains[0];
    return {
      role: "assistant",
      text: `${chains.length} chain${chains.length > 1 ? "s" : ""} found. The strongest links external address ${chain.source_ip} to internal credential ${chain.internal_entity_id} at confidence ${chain.confidence.toFixed(2)}, across ${chain.steps.length} steps.\n\nThis is the finding no single-layer detector can produce: each event alone is unremarkable, and it is the path between them that identifies the compromise.`,
      links: [{ label: "Open attack chains", to: "/chains" }],
    };
  }

  if (
    /(attention|urgent|worst|biggest|threat|priority|right now|critical)/.test(
      q,
    )
  ) {
    if (ranked.length === 0) {
      return {
        role: "assistant",
        text: "Nothing is open. Every alert in this run has been triaged.",
      };
    }
    const top = ranked[0];
    return {
      role: "assistant",
      text: `Highest-scoring open alert: ${top.entity_type} ${top.entity_id}, flagged by ${top.detector} at ${top.window_score.toFixed(2)} (${severityOf(top.window_score)}).\n\n${ranked.length} alert${ranked.length > 1 ? "s remain" : " remains"} open in total. A high score means the entity is unlike its peers — it is evidence for a human to weigh, not a verdict.`,
      links: [
        { label: "Open this alert", to: `/alerts/${top.id}` },
        { label: "Full queue", to: "/alerts" },
      ],
    };
  }

  if (/(queue|how many|status|summary|overview|state)/.test(q)) {
    const byState = stats?.alerts_by_state;
    const lines = byState
      ? Object.entries(byState)
          .filter(([, count]) => count > 0)
          .map(([state, count]) => `  · ${state.replace(/_/g, " ")}: ${count}`)
          .join("\n")
      : "  · no runs recorded yet";
    return {
      role: "assistant",
      text: `${stats?.alerts ?? alerts.length} alerts across ${stats?.runs ?? 0} run${stats?.runs === 1 ? "" : "s"}, ${open.length} still open.\n\n${lines}`,
      links: [{ label: "Triage queue", to: "/alerts" }],
    };
  }

  if (/(drift|community)/.test(q)) {
    return {
      role: "assistant",
      text: "Community drift looks for a *group* moving together. It measures each entity's displacement from its own recent baseline, removes the component the whole population shares, and clusters what remains by direction.\n\nIt catches what per-entity scoring cannot: when a whole cohort shifts, no single member looks unusual relative to its neighbours, because the neighbours moved too.",
    };
  }

  if (/(unison|subgraph|coordinat|ring)/.test(q)) {
    return {
      role: "assistant",
      text: "Abnormal-subgraph detection looks for a small set of entities acting in suspicious unison — near-identical timing and targets. It is the detector for coordinated abuse, where one actor drives many accounts.\n\nMembers are individually unremarkable by construction, which is exactly why a per-event rule has nothing to key on.",
    };
  }

  if (/(outlier|impostor|stolen|key|credential)/.test(q)) {
    return {
      role: "assistant",
      text: "Node-outlier scoring asks whether an identity still behaves like itself, and like comparable peers — new models, odd hours, a volume jump.\n\nWorth knowing its limit: a persistent attack gradually becomes the baseline it is measured against, so a patient adversary is measurably favoured. That is a documented weakness, not a surprise.",
    };
  }

  if (/(help|what can you|how do you)/.test(q)) {
    return {
      role: "assistant",
      text: "I can tell you what is open and worst, whether any cross-layer chains were found, how the queue stands, and what each detector actually does.\n\nI read the loaded data only — I have no model behind me, so I cannot speculate. That is deliberate: in an evidence tool, a confident guess is worse than a blank.",
    };
  }

  return {
    role: "assistant",
    text: `I do not have an answer grounded in this run's data for that.\n\nI can help with: what needs attention, cross-layer chains, queue status, and what the detectors do. ${open.length} alert${open.length === 1 ? " is" : "s are"} currently open.`,
    links: [{ label: "Triage queue", to: "/alerts" }],
  };
}
