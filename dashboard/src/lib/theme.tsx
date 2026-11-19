/**
 * Theme selection.
 *
 * Three states, not two. "System" is the default and follows the operating system, which
 * matters for a tool someone sits in front of all day: an analyst who switches their
 * machine to light at dawn should not have to switch this too. Choosing explicitly pins
 * it and survives reload.
 *
 * The attribute is written to <html> rather than a React context consumed by components,
 * because the whole palette is CSS custom properties. One attribute change re-themes
 * every surface at once, with no re-render and nothing to keep in sync.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "gg.theme";

interface ThemeValue {
  /** What the user chose, including "system". */
  preference: ThemePreference;
  /** What that resolves to right now. */
  theme: ResolvedTheme;
  setPreference: (next: ThemePreference) => void;
  /** Flip to the opposite of what is currently showing. */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

function systemTheme(): ResolvedTheme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function storedPreference(): ThemePreference {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setStored] = useState<ThemePreference>(() => storedPreference());
  const [system, setSystem] = useState<ResolvedTheme>(() => systemTheme());

  // Track the OS setting while "system" is selected, so a change takes effect without
  // a reload.
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setSystem(query.matches ? "light" : "dark");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const theme: ResolvedTheme = preference === "system" ? system : preference;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setPreference = useCallback((next: ThemePreference) => {
    setStored(next);
    if (next === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const toggle = useCallback(() => {
    // Toggling from "system" pins the opposite of what is on screen, which is what
    // someone reaching for the button actually means.
    setPreference(theme === "dark" ? "light" : "dark");
  }, [theme, setPreference]);

  const value = useMemo(
    () => ({ preference, theme, setPreference, toggle }),
    [preference, theme, setPreference, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
