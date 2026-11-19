/**
 * A form generated from a JSON Schema.
 *
 * Written for one consumer — the detection configuration — but deliberately generic, because
 * the reason it exists is that the configuration model changes. `GraphGuardConfig` gains a field
 * whenever a detector gains a parameter, and a hand-written form would fall behind it silently:
 * the parameter would exist, be validated server-side, be unreachable from the interface, and
 * nothing anywhere would say so. Rendering from `GraphGuardConfig.model_json_schema()` means the
 * form and the validator are the same artifact.
 *
 * Scope, stated plainly: this covers the constructs the configuration model actually uses —
 * nested objects, `$ref` into `$defs`, scalars with bounds, enums, booleans, arrays of scalars,
 * and free-form maps. It is not a general JSON-Schema form library, and it does not pretend to
 * be: anything it cannot render falls through to a JSON field for that subtree rather than being
 * dropped, so an unsupported construct costs the administrator a raw editor for one branch
 * instead of costing them the parameter.
 *
 * Validation stays on the server. Doing bounds checking here as well would put a second,
 * drifting copy of the rules in the browser — the one failure this whole design exists to avoid.
 * The client's job is to produce a well-formed object; the server decides whether it is legal.
 */

import { useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  additionalProperties?: boolean | JsonSchema;
  items?: JsonSchema;
  enum?: unknown[];
  anyOf?: JsonSchema[];
  allOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  default?: unknown;
  [key: string]: unknown;
}

/** Follow a local `$ref` (`#/$defs/Name`) and merge in anything declared alongside it. */
function resolve(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (!schema.$ref) return schema;
  const name = schema.$ref.replace("#/$defs/", "");
  const target = root.$defs?.[name];
  if (!target) return schema;
  const { $ref: _ignored, ...rest } = schema;
  return { ...target, ...rest };
}

/**
 * Collapse `anyOf` to the branch worth rendering.
 *
 * Pydantic emits `T | None` as `anyOf: [T, {type: "null"}]`, which is by far the most common
 * shape here and means "optional T", not "a choice the administrator should be asked to make".
 * Anything more genuinely polymorphic is left alone and falls through to the JSON escape hatch.
 */
function simplify(schema: JsonSchema, root: JsonSchema): JsonSchema {
  const resolved = resolve(schema, root);
  if (resolved.allOf?.length === 1) return simplify(resolved.allOf[0]!, root);
  if (!resolved.anyOf) return resolved;
  const branches = resolved.anyOf
    .map((branch) => resolve(branch, root))
    .filter((branch) => branch.type !== "null");
  if (branches.length !== 1) return resolved;
  return { ...simplify(branches[0]!, root), ...stripSchemaKeywords(resolved) };
}

/** The annotations worth carrying down from the wrapper onto the branch it wrapped. */
function stripSchemaKeywords(schema: JsonSchema): JsonSchema {
  const kept: JsonSchema = {};
  if (schema.title) kept.title = schema.title;
  if (schema.description) kept.description = schema.description;
  if (schema.default !== undefined) kept.default = schema.default;
  return kept;
}

/**
 * A human label for a property.
 *
 * Derived from the key, never from the schema's `title`. Pydantic sets `title` to the *class*
 * name on a nested model, so trusting it labelled the sections "RunConfig", "IngestionConfig",
 * "SimulatorConfig" — the identifiers of the Python types, shown to an administrator who has
 * never seen the source. On scalar fields `title` is only the humanised key anyway, so nothing
 * is lost by ignoring it everywhere.
 */
const ACRONYMS: Record<string, string> = { api: "API", http: "HTTP", ip: "IP", url: "URL" };

function labelFor(key: string, _schema: JsonSchema): string {
  return key
    .split("_")
    .map((word, index) =>
      ACRONYMS[word] ?? (index === 0 ? word.replace(/^./, (c) => c.toUpperCase()) : word),
    )
    .join(" ");
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function ConfigForm({
  schema,
  value,
  onChange,
  readOnly,
}: {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  readOnly: boolean;
}) {
  const sections = useMemo(() => Object.entries(schema.properties ?? {}), [schema]);

  if (sections.length === 0) {
    return (
      <p className="text-xs text-[var(--color-ink-faint)]">
        The server returned a schema with no properties, so there is nothing to render. Use the
        JSON editor.
      </p>
    );
  }

  return (
    <div className="space-y-2.5">
      {sections.map(([key, raw], index) => {
        const child = simplify(raw, schema);
        return (
          <Section
            key={key}
            title={labelFor(key, child)}
            description={child.description}
            // Open the first section only. A configuration with eight top-level groups
            // expanded at once is a wall of forty inputs, and the administrator is nearly
            // always here to change one thing.
            defaultOpen={index === 0}
          >
            <NodeEditor
              schema={child}
              root={schema}
              value={value[key]}
              onChange={(next) => onChange({ ...value, [key]: next })}
              readOnly={readOnly}
            />
          </Section>
        );
      })}
    </div>
  );
}

function Section({
  title,
  description,
  defaultOpen,
  children,
}: {
  title: string;
  description?: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)]">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-[var(--color-raise)]"
      >
        <ChevronDown
          size={15}
          className={`shrink-0 text-[var(--color-ink-faint)] transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-medium">{title}</span>
          {description && (
            <span className="mt-0.5 block text-[11px] text-[var(--color-ink-faint)]">
              {description}
            </span>
          )}
        </span>
      </button>
      {open && <div className="space-y-4 border-t border-[var(--color-line)] px-4 py-4">{children}</div>}
    </div>
  );
}

/** Dispatch on the shape of the schema node. */
function NodeEditor({
  schema,
  root,
  value,
  onChange,
  readOnly,
}: {
  schema: JsonSchema;
  root: JsonSchema;
  value: unknown;
  onChange: (next: unknown) => void;
  readOnly: boolean;
}) {
  const properties = schema.properties;

  if (properties && Object.keys(properties).length > 0) {
    const object = isPlainObject(value) ? value : {};
    return (
      <div className="space-y-4">
        {Object.entries(properties).map(([key, raw]) => {
          const child = simplify(raw, root);
          const isGroup = Boolean(child.properties && Object.keys(child.properties).length);
          const body = (
            <NodeEditor
              schema={child}
              root={root}
              value={object[key]}
              onChange={(next) => onChange({ ...object, [key]: next })}
              readOnly={readOnly}
            />
          );
          if (isGroup) {
            return (
              <fieldset
                key={key}
                className="rounded-lg border border-[var(--color-line)]/70 px-3.5 pt-2.5 pb-3.5"
              >
                <legend className="px-1 text-[11px] font-semibold tracking-wide text-[var(--color-ink-dim)] uppercase">
                  {labelFor(key, child)}
                </legend>
                {child.description && (
                  <p className="mb-3 text-[11px] text-[var(--color-ink-faint)]">
                    {child.description}
                  </p>
                )}
                {body}
              </fieldset>
            );
          }
          return (
            <Row key={key} label={labelFor(key, child)} hint={hintFor(child)}>
              {body}
            </Row>
          );
        })}
      </div>
    );
  }

  return <ScalarEditor schema={schema} value={value} onChange={onChange} readOnly={readOnly} />;
}

/** Bounds and defaults, said in a sentence rather than left for the server to reject. */
function hintFor(schema: JsonSchema): string | undefined {
  const parts: string[] = [];
  if (schema.description) parts.push(schema.description);
  const lower = schema.minimum ?? schema.exclusiveMinimum;
  const upper = schema.maximum ?? schema.exclusiveMaximum;
  if (lower !== undefined && upper !== undefined) parts.push(`Between ${lower} and ${upper}.`);
  else if (lower !== undefined) parts.push(`At least ${lower}.`);
  else if (upper !== undefined) parts.push(`At most ${upper}.`);
  return parts.length ? parts.join(" ") : undefined;
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5 sm:grid-cols-[minmax(0,1fr)_220px] sm:items-center sm:gap-4">
      <span className="min-w-0">
        <span className="block text-[13px] text-[var(--color-ink)]">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            {hint}
          </span>
        )}
      </span>
      {children}
    </label>
  );
}

const FIELD =
  "w-full rounded-control border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-ink)] focus:border-[var(--color-accent)]/50 focus:outline-none disabled:opacity-60";

function ScalarEditor({
  schema,
  value,
  onChange,
  readOnly,
}: {
  schema: JsonSchema;
  value: unknown;
  onChange: (next: unknown) => void;
  readOnly: boolean;
}) {
  const type = Array.isArray(schema.type) ? schema.type[0] : schema.type;

  if (schema.enum) {
    return (
      <select
        value={String(value ?? "")}
        disabled={readOnly}
        onChange={(event) => onChange(event.target.value)}
        className={FIELD}
      >
        {schema.enum.map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  }

  if (type === "boolean") {
    return (
      <span className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={value === true}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.checked)}
          className="size-4 accent-[var(--color-accent)]"
        />
        <span className="text-xs text-[var(--color-ink-dim)]">
          {value === true ? "Enabled" : "Disabled"}
        </span>
      </span>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <input
        type="number"
        // An empty field must not become 0. A threshold silently reset to zero would flag
        // everything, which is the sort of change that looks like a detector improvement.
        value={value === null || value === undefined ? "" : String(value)}
        step={type === "integer" ? 1 : "any"}
        min={schema.minimum}
        max={schema.maximum}
        disabled={readOnly}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") return onChange(null);
          const parsed = type === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
          onChange(Number.isNaN(parsed) ? raw : parsed);
        }}
        className={`${FIELD} tabular-nums`}
      />
    );
  }

  if (type === "string") {
    return (
      <input
        type="text"
        value={value === null || value === undefined ? "" : String(value)}
        disabled={readOnly}
        onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
        className={`${FIELD} font-mono`}
      />
    );
  }

  if (type === "array" && (!schema.items || schema.items.type !== "object")) {
    // Comma-separated is the right affordance for the arrays this model actually has —
    // enabled scenario names, hour ranges. A repeater UI would be more machinery than the
    // data justifies.
    const list = Array.isArray(value) ? value : [];
    return (
      <input
        type="text"
        value={list.map((item) => String(item)).join(", ")}
        disabled={readOnly}
        onChange={(event) =>
          onChange(
            event.target.value
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean)
              .map((item) => (Number.isNaN(Number(item)) ? item : Number(item))),
          )
        }
        className={`${FIELD} font-mono`}
      />
    );
  }

  // Everything else — free-form maps, arrays of objects, genuine unions — keeps its raw
  // editor for that subtree. Not being able to render a construct must not mean losing it.
  return <JsonSubtree value={value} onChange={onChange} readOnly={readOnly} />;
}

function JsonSubtree({
  value,
  onChange,
  readOnly,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
  readOnly: boolean;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [broken, setBroken] = useState(false);

  return (
    <span className="sm:col-span-1">
      <textarea
        value={text}
        disabled={readOnly}
        spellCheck={false}
        rows={Math.min(10, Math.max(2, text.split("\n").length))}
        onChange={(event) => {
          setText(event.target.value);
          try {
            onChange(JSON.parse(event.target.value));
            setBroken(false);
          } catch {
            // Hold the last valid value rather than propagating a parse failure upward,
            // so a half-typed edit cannot corrupt the draft being saved.
            setBroken(true);
          }
        }}
        className={`${FIELD} resize-y font-mono leading-relaxed ${
          broken ? "border-[var(--color-critical)]/60" : ""
        }`}
      />
      {broken && (
        <span className="mt-1 block text-[11px] text-[var(--color-critical)]">
          Not valid JSON — the last value that parsed is still what will be saved.
        </span>
      )}
    </span>
  );
}
