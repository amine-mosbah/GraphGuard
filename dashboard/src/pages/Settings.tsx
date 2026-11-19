/**
 * Settings.
 *
 * Three surfaces that were previously one JSON textarea: detection parameters, the signed-in
 * account, and what this deployment is actually running.
 *
 * **On the form.** The original comment here argued that a hand-maintained form would silently
 * fall behind a pydantic model that grows as detectors are added, leaving parameters an
 * administrator cannot reach and no indication that they exist. That reasoning is right, and the
 * conclusion it reached — ship a raw JSON editor — is not the only way to honour it. The form
 * below is *generated from the server's own JSON Schema* (`GET /api/v1/config/schema`), so a new
 * detector parameter appears in the interface the moment it exists on the model, and drift is
 * impossible rather than merely discouraged. The JSON editor is kept as the advanced tab, because
 * a generated form is a poor way to restructure a nested object and pasting a known-good
 * configuration is a real workflow.
 *
 * **On the crash this page used to be.** Opening this page returned a 500. A revision saved in
 * August carried `correlation.explanation.subgraph_radius`; the field was later removed from the
 * model, and because the model forbids unknown keys the stored payload stopped validating — so
 * every read of the effective configuration raised. The server now degrades to the file and
 * reports which revision is stale (see platform/config_store.py); this page shows that report
 * rather than a blank screen, and offers the one action that resolves it.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Cpu,
  History,
  Info,
  KeyRound,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import type { ConfigResponse, ConfigVersionSummary, SystemInfo } from "../lib/types";
import {
  Button,
  PageHeader,
  Panel,
  PanelHead,
  Segmented,
  SkeletonPanel,
} from "../components/ui";
import { ConfigForm, type JsonSchema } from "../components/config-form";

type Tab = "detection" | "account" | "platform";

export function Settings() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("detection");

  return (
    <PageHeader
      title="Settings"
      subtitle="Detection parameters used by the next run, the account you are signed in as, and what this deployment is running."
      actions={
        <Segmented
          ariaLabel="Settings section"
          value={tab}
          onChange={setTab}
          options={[
            { value: "detection", label: "Detection" },
            { value: "account", label: "Account" },
            { value: "platform", label: "Platform" },
          ]}
        />
      }
    >
      {tab === "detection" && <Detection isAdmin={isAdmin} />}
      {tab === "account" && <Account />}
      {tab === "platform" && <PlatformInfo />}
    </PageHeader>
  );
}

/* --- detection ----------------------------------------------------------- */

function Detection({ isAdmin }: { isAdmin: boolean }) {
  const [current, setCurrent] = useState<ConfigResponse | null>(null);
  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [versions, setVersions] = useState<ConfigVersionSummary[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"form" | "json">("form");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [config, schemaResult, history] = await Promise.all([
        api.get<ConfigResponse>("/api/v1/config"),
        api.get<JsonSchema>("/api/v1/config/schema"),
        api.get<{ versions: ConfigVersionSummary[] }>("/api/v1/config/versions"),
      ]);
      setCurrent(config);
      setSchema(schemaResult);
      setDraft(config.config);
      setText(JSON.stringify(config.config, null, 2));
      setVersions(history.versions);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load configuration");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** The draft, whichever editor produced it. Returns null and reports if the JSON is broken. */
  function payload(): Record<string, unknown> | null {
    if (mode === "form") return draft;
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch (cause) {
      setError(`That is not valid JSON — ${cause instanceof Error ? cause.message : "parse error"}`);
      return null;
    }
  }

  /** Keep the two editors in step, so switching tabs never silently discards an edit. */
  function switchMode(next: "form" | "json") {
    if (next === mode) return;
    if (next === "json") {
      setText(JSON.stringify(draft, null, 2));
    } else {
      try {
        setDraft(JSON.parse(text) as Record<string, unknown>);
      } catch {
        setError("The JSON has to parse before the form can show it.");
        return;
      }
    }
    setMode(next);
  }

  async function validate() {
    setError(null);
    setSaved(null);
    const body = payload();
    if (!body) return;
    setBusy(true);
    try {
      await api.put("/api/v1/config/validate", body);
      setSaved("Valid. Nothing has been saved — use Save and activate to apply it.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Validation failed");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setError(null);
    setSaved(null);
    const body = payload();
    if (!body) return;
    setBusy(true);
    try {
      await api.post("/api/v1/config", { config: body, note });
      setNote("");
      setSaved("Saved and activated. The next run will use it.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function rollback(versionId: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await api.post(`/api/v1/config/versions/${versionId}/activate`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Rollback failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr]">
        <SkeletonPanel rows={8} />
        <SkeletonPanel rows={4} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {current?.stale_version && (
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-high)]/12 px-4 py-3 ring-1 ring-[var(--color-high)]/30">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[var(--color-high)]" />
          <div className="min-w-0 text-xs leading-relaxed text-[var(--color-ink-dim)]">
            <p className="font-medium text-[var(--color-high)]">
              Revision {current.stale_version.revision} is marked active but no longer matches the
              configuration schema, so runs are using <code>config/default.yaml</code> instead.
            </p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-[var(--color-surface-2)] px-3 py-2 font-mono text-[11px] whitespace-pre-wrap">
              {current.stale_reason}
            </pre>
            <p className="mt-2">
              This happens when a parameter is renamed or removed after a revision was saved. The
              values below are the file's; adjust them and save a new revision to get back onto a
              stored configuration.
            </p>
          </div>
        </div>
      )}

      {!isAdmin && (
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-medium)]/10 px-4 py-3 text-xs text-[var(--color-medium)] ring-1 ring-[var(--color-medium)]/25">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            You are signed in as an analyst, so this is read-only. Changing detection parameters is
            restricted to administrators — a threshold quietly raised is indistinguishable, from the
            queue, from a period with no attacks.
          </span>
        </div>
      )}

      {error && (
        <div className="rounded-xl bg-[var(--color-critical)]/12 px-4 py-3 font-mono text-xs whitespace-pre-wrap text-[var(--color-critical)] ring-1 ring-[var(--color-critical)]/25">
          {error}
        </div>
      )}
      {saved && !error && (
        <div className="flex items-center gap-2 rounded-xl bg-[var(--color-ok)]/12 px-4 py-3 text-xs text-[var(--color-ok)] ring-1 ring-[var(--color-ok)]/25">
          <Check size={15} /> {saved}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[1.7fr_1fr]">
        <Panel>
          <PanelHead
            title="Effective configuration"
            subtitle={
              current?.active_version
                ? `Revision ${current.active_version.revision}, saved ${new Date(current.active_version.created_at).toLocaleString()}`
                : `Loaded from ${current?.source ?? "config/default.yaml"} — no revision in force`
            }
            action={
              <Segmented
                ariaLabel="How to edit the configuration"
                value={mode}
                onChange={switchMode}
                options={[
                  { value: "form", label: "Form" },
                  { value: "json", label: "JSON" },
                ]}
              />
            }
          />
          <div className="px-5 pb-5">
            {mode === "form" && schema ? (
              <ConfigForm
                schema={schema}
                value={draft}
                onChange={setDraft}
                readOnly={!isAdmin}
              />
            ) : (
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                readOnly={!isAdmin}
                spellCheck={false}
                rows={26}
                aria-label="Configuration as JSON"
                className="w-full resize-y rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-ink-dim)] focus:border-[var(--color-accent)]/40 focus:outline-none"
              />
            )}

            {isAdmin && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--color-line)] pt-4">
                <input
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="What changed, and why (kept with the revision)"
                  className="min-w-[220px] flex-1 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2 text-xs focus:border-[var(--color-accent)]/40 focus:outline-none"
                />
                <Button variant="ghost" size="sm" onClick={validate} disabled={busy}>
                  <Check size={14} /> Validate
                </Button>
                <Button size="sm" onClick={save} loading={busy}>
                  <Save size={14} /> Save and activate
                </Button>
              </div>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHead
            title="Revision history"
            subtitle="Every configuration that has been in force"
            action={<History size={16} className="text-[var(--color-ink-faint)]" />}
          />
          <div className="px-5 pb-5">
            {versions.length === 0 ? (
              <p className="text-xs leading-relaxed text-[var(--color-ink-faint)]">
                No revisions saved. The platform is using <code>{current?.source}</code> unchanged,
                which is also what the command line and the evaluation harness use.
              </p>
            ) : (
              <ul className="space-y-2">
                {versions.map((version) => (
                  <li
                    key={version.id}
                    className={`rounded-xl px-3.5 py-3 ring-1 ${
                      version.is_active && version.parses
                        ? "bg-[var(--color-accent)]/10 ring-[var(--color-accent)]/30"
                        : !version.parses
                          ? "bg-[var(--color-high)]/8 ring-[var(--color-high)]/25"
                          : "bg-[var(--color-surface-2)] ring-transparent"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium">
                        Revision {version.revision}
                        {version.is_active && (
                          <span className="ml-2 text-[10px] text-[var(--color-accent)]">active</span>
                        )}
                      </span>
                      {isAdmin && !version.is_active && version.parses && (
                        <button
                          onClick={() => rollback(version.id)}
                          disabled={busy}
                          className="inline-flex items-center gap-1 text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-accent)]"
                        >
                          <RotateCcw size={11} /> Activate
                        </button>
                      )}
                    </div>
                    {!version.parses && (
                      /* Stated on the revision rather than only in the banner: an administrator
                         scanning the history needs to know which ones are no longer usable, not
                         just that one of them is. */
                      <p className="mt-1 text-[11px] text-[var(--color-high)]">
                        Cannot be activated — written against an earlier schema.
                      </p>
                    )}
                    {version.note && (
                      <p className="mt-1 text-[11px] text-[var(--color-ink-dim)] italic">
                        “{version.note}”
                      </p>
                    )}
                    <p className="mt-1 text-[10px] text-[var(--color-ink-faint)]">
                      {new Date(version.created_at).toLocaleString()}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

/* --- account ------------------------------------------------------------- */

function Account() {
  const { user, signOut } = useAuth();
  const { preference, setPreference } = useTheme();

  if (!user) return null;

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Panel>
        <PanelHead title="Signed in as" subtitle="Identity attached to every triage decision" />
        <dl className="space-y-3.5 px-5 pb-5 text-sm">
          <Field icon={<UserRound size={14} />} label="Name" value={user.full_name || "—"} />
          <Field icon={<KeyRound size={14} />} label="Email" value={user.email} />
          <Field
            icon={<ShieldCheck size={14} />}
            label="Role"
            value={user.role === "admin" ? "Administrator" : "Analyst"}
            hint={
              user.role === "admin"
                ? "May change detection parameters and start runs."
                : "May triage alerts and open cases; detection parameters are read-only."
            }
          />
          <Field
            icon={<Info size={14} />}
            label="Last signed in"
            value={user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "—"}
          />
        </dl>
        <div className="px-5 pb-5">
          <Button variant="ghost" size="sm" onClick={() => void signOut()}>
            Sign out
          </Button>
          <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            Signing out revokes this session's refresh token on the server, so it cannot be replayed
            from anywhere it may have been copied.
          </p>
        </div>
      </Panel>

      <Panel>
        <PanelHead title="Appearance" subtitle="Stored on this device, not on the account" />
        <div className="px-5 pb-5">
          <Segmented
            ariaLabel="Colour theme"
            value={preference}
            onChange={setPreference}
            options={[
              { value: "system" as const, label: "System" },
              { value: "light" as const, label: "Light" },
              { value: "dark" as const, label: "Dark" },
            ]}
          />
          <p className="mt-3 text-xs leading-relaxed text-[var(--color-ink-dim)]">
            Both themes are designed rather than inverted: the severity ramp and the entity hues
            have separate values for each, because the dark set is tuned for luminance against
            near-black and washes out on white — which would erase exactly the distinctions the
            queue is read by.
          </p>
          <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            Motion follows your system's reduced-motion setting; nothing here overrides it.
          </p>
        </div>
      </Panel>
    </div>
  );
}

function Field({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <dt className="flex items-center gap-1.5 text-xs text-[var(--color-ink-faint)]">
        {icon}
        {label}
      </dt>
      <dd className="mt-0.5 text-[var(--color-ink)]">{value}</dd>
      {hint && <p className="mt-0.5 text-[11px] text-[var(--color-ink-faint)]">{hint}</p>}
    </div>
  );
}

/* --- platform ------------------------------------------------------------ */

function PlatformInfo() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<SystemInfo>("/api/v1/system")
      .then(setInfo)
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : "Could not read system information"),
      );
  }, []);

  if (error) {
    return (
      <Panel>
        <div className="px-5 py-6 text-xs text-[var(--color-critical)]">{error}</div>
      </Panel>
    );
  }
  if (!info) return <SkeletonPanel rows={5} />;

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Panel>
        <PanelHead
          title="Deployment"
          subtitle="What this instance is running"
          action={<Cpu size={16} className="text-[var(--color-ink-faint)]" />}
        />
        <dl className="space-y-3.5 px-5 pb-5 text-sm">
          <Field icon={<Info size={14} />} label="Version" value={info.version} />
          <Field
            icon={<Info size={14} />}
            label="Environment"
            value={info.environment}
            hint={
              info.environment === "production"
                ? "Development defaults for the signing key and the database are refused at startup."
                : "Development defaults are in use; production startup would refuse them."
            }
          />
          <Field
            icon={<Info size={14} />}
            label="Database"
            value={info.database}
            hint="Access goes through SQLAlchemy, so this is the only line that differs between a laptop and a server."
          />
          <Field
            icon={<Info size={14} />}
            label="Assistant"
            value={
              info.assistant_enabled
                ? `${info.assistant_provider} · ${info.assistant_model}`
                : "Disabled"
            }
            hint={
              info.assistant_enabled
                ? "Its only tools are this platform's own API, under your role's permissions."
                : "The local answerer is in use. The agent mode needs a credential in the environment."
            }
          />
        </dl>
      </Panel>

      <Panel>
        <PanelHead title="Sessions" subtitle="How long credentials stay valid" />
        <dl className="space-y-3.5 px-5 pb-5 text-sm">
          <Field
            icon={<KeyRound size={14} />}
            label="Access token"
            value={`${info.access_token_ttl_minutes} minutes`}
            hint="Short by design; the console refreshes transparently, so a session never dies mid-investigation."
          />
          <Field
            icon={<KeyRound size={14} />}
            label="Refresh token"
            value={`${info.refresh_token_ttl_days} days`}
            hint="Rotated on every exchange and revocable, which is what makes stateless tokens revocable at all."
          />
          <Field
            icon={<SlidersHorizontal size={14} />}
            label="Detectors that have produced alerts here"
            value={info.detectors.length ? info.detectors.join(", ") : "none yet"}
          />
        </dl>
      </Panel>
    </div>
  );
}
