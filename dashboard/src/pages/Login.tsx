/**
 * The sign-in form.
 *
 * Styled as part of the landing page rather than as the first screen of the console: it is
 * reached from `/`, it is the same navy field with the same colour-bend shader behind it,
 * and it uses the `.landing` token scope so it stays dark under both themes. Anything else
 * makes signing in feel like a different product from the one the visitor just read about.
 *
 * The shader is the same component and the same props as the hero, deliberately — a second
 * set of values would drift from it. It runs at a lower intensity here because there is
 * a form on top rather than a headline, and form labels are small type.
 */

import { useLayoutEffect, useState } from "react";
import { ArrowLeft, Loader2, Lock, Mail } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { GraphGuardLogo } from "../components/Logo";
import ColorBends from "../components/backgrounds/ColorBends";

export function Login() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Same flag the landing page sets: without it the themed body colour shows through the
  // overscroll bounce and the UA paints light scrollbars and form controls on a navy page.
  useLayoutEffect(() => {
    document.documentElement.dataset.landing = "true";
    return () => {
      delete document.documentElement.dataset.landing;
    };
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (cause) {
      // The server deliberately returns the same message for an unknown address and a
      // wrong password, so the form must not try to be more helpful than that — doing so
      // would rebuild the account-enumeration oracle the API avoids.
      setError(cause instanceof Error ? cause.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  // No `focus:outline-none` here. The border is an inline style, so a focus utility that
  // recolours the border cannot win against it — killing the outline as well would leave
  // the field with no focus indication at all. The document's own :focus-visible ring is
  // the right answer, and `.landing` maps --color-accent onto the landing accent so that
  // ring is legible against navy.
  //
  // The border is 0.35 white rather than the 0.10 hairline used for decorative edges:
  // WCAG 1.4.11 wants 3:1 for the boundary of a control, and 0.10 measured 1.5:1 against
  // this card (0.26 got to 2.86 — still short on both edges). A field the eye cannot find
  // is not a style question.
  const field =
    "w-full rounded-xl py-2.5 pr-3 pl-9 text-sm text-[color:var(--landing-ink)] transition-colors " +
    "placeholder:text-[color:var(--landing-ink-faint)]";
  const fieldStyle = {
    background: "rgb(255 255 255 / 0.07)",
    border: "1px solid rgb(255 255 255 / 0.35)",
  } as const;

  return (
    <div className="landing relative isolate grid min-h-dvh place-items-center overflow-hidden px-6">
      <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
        <ColorBends
          colors={["#2f6bf0"]}
          rotation={90}
          speed={0.2}
          frequency={1.0}
          noise={0.15}
          bandWidth={6}
          iterations={1}
          intensity={0.75}
        />
      </div>
      {/* The same scrim technique as the hero: darken only where the type sits, so the
          field keeps its brightness at the edges and the form keeps its contrast. */}
      <div
        className="pointer-events-none absolute inset-0 -z-10"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(46% 40% at 50% 50%, rgb(6 15 33 / 0.9) 0%, rgb(6 15 33 / 0.66) 45%, rgb(6 15 33 / 0) 78%)",
        }}
      />

      <div className="w-full max-w-sm py-16">
        <Link
          to="/"
          className="mb-7 inline-flex items-center gap-1.5 text-xs font-medium text-[color:var(--landing-ink-faint)] transition-colors hover:text-[color:var(--landing-ink)]"
        >
          <ArrowLeft size={14} />
          Back to GraphGuard
        </Link>

        <div className="mb-8 text-[color:var(--landing-ink)]">
          <GraphGuardLogo size={30} />
        </div>

        <form
          onSubmit={submit}
          className="rounded-2xl p-6"
          style={{
            background: "rgb(255 255 255 / 0.04)",
            border: "1px solid var(--landing-line)",
            boxShadow:
              "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 30px 60px -30px rgb(3 8 18 / 0.9)",
            backdropFilter: "blur(14px)",
            WebkitBackdropFilter: "blur(14px)",
          }}
        >
          <h1 className="text-base font-semibold text-[color:var(--landing-ink)]">Sign in</h1>
          <p className="mt-1 mb-6 text-xs text-[color:var(--landing-ink-dim)]">
            Analyst access to the detection console.
          </p>

          <label className="mb-1.5 block text-xs font-medium text-[color:var(--landing-ink-dim)]">
            Email
          </label>
          <div className="relative mb-4">
            <Mail
              size={15}
              className="absolute top-1/2 left-3 -translate-y-1/2 text-[color:var(--landing-ink-faint)]"
            />
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={field}
              style={fieldStyle}
            />
          </div>

          <label className="mb-1.5 block text-xs font-medium text-[color:var(--landing-ink-dim)]">
            Password
          </label>
          <div className="relative mb-5">
            <Lock
              size={15}
              className="absolute top-1/2 left-3 -translate-y-1/2 text-[color:var(--landing-ink-faint)]"
            />
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={field}
              style={fieldStyle}
            />
          </div>

          {error && (
            <div
              role="alert"
              className="mb-4 rounded-xl px-3 py-2.5 text-xs"
              style={{
                background: "rgb(255 90 95 / 0.12)",
                color: "#ff8a8e",
                border: "1px solid rgb(255 90 95 / 0.28)",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-full py-2.5 text-sm font-semibold text-white transition-transform active:scale-[0.99] disabled:opacity-60"
            style={{
              background: "linear-gradient(90deg,#2a63e4 0%,#0853e7 100%)",
              boxShadow:
                "inset 0 1px 2px rgb(255 255 255 / 0.3), 0 12px 32px -14px rgb(8 83 231 / 0.95)",
            }}
          >
            {busy && <Loader2 size={15} className="animate-spin" />}
            {busy ? "Signing in" : "Sign in"}
          </button>
        </form>

        <p className="mt-4 text-center text-[11px] leading-relaxed text-[color:var(--landing-ink-dim)]">
          Accounts are provisioned by an administrator.
          <br />
          There is no self-service registration.
        </p>
      </div>
    </div>
  );
}
