/**
 * A small markdown renderer for assistant output.
 *
 * Deliberately dependency-free and element-based: model output is untrusted input in a
 * security console, and building React elements (never innerHTML) makes script
 * injection structurally impossible rather than a sanitiser's promise. Covers what a
 * chat model actually emits — headings, bold/italic, inline code, fenced code blocks,
 * bulleted and numbered lists, links — and lets anything fancier degrade to plain text.
 */

import type { ReactNode } from "react";

const INLINE = /(`[^`]+`)|(\*\*[^*]+?\*\*)|(\*[^*\s][^*]*?\*)|(\[[^\]]+\]\((?:https?:\/\/|\/)[^)\s]+\))/;

function renderInline(text: string, key = 0): ReactNode[] {
  const match = INLINE.exec(text);
  if (!match || match.index === undefined) return text ? [text] : [];
  const parts: ReactNode[] = [];
  if (match.index > 0) parts.push(text.slice(0, match.index));
  const token = match[0];

  if (token.startsWith("`")) {
    parts.push(
      <code
        key={key}
        className="rounded bg-[var(--color-raise-strong)] px-1 py-px font-mono text-[0.92em]"
      >
        {token.slice(1, -1)}
      </code>,
    );
  } else if (token.startsWith("**")) {
    parts.push(
      <strong key={key} className="font-semibold text-[var(--color-ink)]">
        {renderInline(token.slice(2, -2), key + 1)}
      </strong>,
    );
  } else if (token.startsWith("*")) {
    parts.push(<em key={key}>{renderInline(token.slice(1, -1), key + 1)}</em>);
  } else {
    const split = token.indexOf("](");
    const label = token.slice(1, split);
    const href = token.slice(split + 2, -1);
    parts.push(
      <a
        key={key}
        href={href}
        target={href.startsWith("/") ? undefined : "_blank"}
        rel="noopener noreferrer"
        className="text-[var(--color-accent)] underline decoration-[var(--color-accent)]/40 hover:decoration-[var(--color-accent)]"
      >
        {label}
      </a>,
    );
  }
  parts.push(...renderInline(text.slice(match.index + token.length), key + 2));
  return parts;
}

type Block =
  | { kind: "heading"; depth: number; text: string }
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "paragraph"; lines: string[] };

function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // closing fence
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", depth: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }
    const bullet = /^\s*[-*]\s+/.test(line);
    const numbered = /^\s*\d+[.)]\s+/.test(line);
    if (bullet || numbered) {
      const items: string[] = [];
      while (
        index < lines.length &&
        (bullet ? /^\s*[-*]\s+/.test(lines[index]) : /^\s*\d+[.)]\s+/.test(lines[index]))
      ) {
        items.push(lines[index].replace(bullet ? /^\s*[-*]\s+/ : /^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push({ kind: "list", ordered: numbered, items });
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() !== "" &&
      !lines[index].startsWith("```") &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ kind: "paragraph", lines: paragraph });
  }
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {parseBlocks(text).map((block, index) => {
        if (block.kind === "heading") {
          return (
            <div
              key={index}
              className={`font-semibold text-[var(--color-ink)] ${
                block.depth <= 2 ? "text-[1.05em]" : "text-[1em]"
              }`}
            >
              {renderInline(block.text)}
            </div>
          );
        }
        if (block.kind === "code") {
          return (
            <pre
              key={index}
              className="overflow-x-auto rounded-lg bg-[var(--color-sink)] p-2.5 font-mono text-[0.92em] leading-snug whitespace-pre-wrap"
            >
              {block.text}
            </pre>
          );
        }
        if (block.kind === "list") {
          const items = block.items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item)}</li>
          ));
          return block.ordered ? (
            <ol key={index} className="ml-4 list-decimal space-y-1 marker:text-[var(--color-ink-faint)]">
              {items}
            </ol>
          ) : (
            <ul key={index} className="ml-4 list-disc space-y-1 marker:text-[var(--color-ink-faint)]">
              {items}
            </ul>
          );
        }
        return <p key={index}>{renderInline(block.lines.join(" "))}</p>;
      })}
    </div>
  );
}
