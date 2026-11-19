/**
 * Form controls.
 *
 * These exist because the same input class string was pasted verbatim into six files.
 * The one that mattered most was the detector filter on the triage queue: a bare native
 * <select>, which renders as unstyled operating-system chrome in the middle of a themed
 * console — and which appears in a report figure.
 *
 * Every control here inherits its colours from the surrounding surface's tokens, so the
 * same component is correct on the neutral canvas and inside a .panel-brand.
 */

import type { ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import type { InputHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

/** Shared field skin. Sink rather than raise: a field is recessed into its surface. */
const FIELD_BASE =
  "w-full rounded-field border border-[var(--color-line)] bg-[var(--color-sink)] text-sm text-[var(--color-ink)] transition-colors duration-150 ease-out placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60";

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  /** Rendered inside the field on the leading edge. Pads the text across automatically. */
  icon?: ReactNode;
  className?: string;
}

export function Input({ icon, className = "", ...rest }: InputProps) {
  const field = (
    <input {...rest} className={`${FIELD_BASE} py-2.5 pr-3 ${icon ? "pl-9" : "pl-3"} ${className}`} />
  );
  if (!icon) return field;
  return (
    <div className="relative">
      <span
        className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-[var(--color-ink-faint)]"
        aria-hidden
      >
        {icon}
      </span>
      {field}
    </div>
  );
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  children: ReactNode;
  className?: string;
}

/**
 * A themed select.
 *
 * `appearance-none` plus our own chevron, because the native indicator cannot be styled
 * and drags the OS's own light-mode chrome into a dark console. The element underneath
 * is still a real <select>, so the picker itself stays native — which is the right
 * trade: keyboard behaviour, type-ahead and mobile pickers all come free, and only the
 * closed state needed fixing.
 */
export function Select({ children, className = "", ...rest }: SelectProps) {
  return (
    <div className="relative">
      <select {...rest} className={`${FIELD_BASE} appearance-none py-2.5 pr-9 pl-3 ${className}`}>
        {children}
      </select>
      <ChevronDown
        size={14}
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[var(--color-ink-faint)]"
      />
    </div>
  );
}

export function Textarea({
  className = "",
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { className?: string }) {
  return <textarea {...rest} className={`${FIELD_BASE} px-3 py-2.5 ${className}`} />;
}
