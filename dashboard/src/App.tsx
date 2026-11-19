/**
 * Application shell and routing.
 *
 * The run-scoped data an analyst looks at — alerts, chains, the timeline, the graph — is
 * fetched once here and passed down, rather than fetched per page. Three reasons: the
 * topbar's search and notification centre need the alert list regardless of which page is
 * open, moving between pages should not re-request a graph that has not changed, and a
 * triage decision made on one page must be reflected in the sidebar badge on every other.
 */

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./lib/api";
import { AuthProvider, useAuth } from "./lib/auth";
import { ThemeProvider } from "./lib/theme";
import type {
  AlertPage,
  AlertSummary,
  AttackChain,
  NetworkGraph,
  PlatformStats,
  RunSummary,
  TimelineBucket,
} from "./lib/types";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { Assistant } from "./components/Assistant";
import { AssistantProvider } from "./components/AssistantChat";
import { AssistantPage } from "./pages/AssistantPage";
import { Spinner } from "./components/ui";
import { Login } from "./pages/Login";
import { Landing } from "./pages/Landing";
import { Overview } from "./pages/Overview";
import { NetworkMap } from "./pages/NetworkMap";
import { AlertDetailPage, Alerts } from "./pages/Alerts";
import { Chains } from "./pages/Chains";
import { RunDetailPage, Runs } from "./pages/Runs";
import { ErrorState } from "./components/feedback";
import { Settings } from "./pages/Settings";
import { Investigations } from "./pages/Investigations";
import { Help } from "./pages/Help";

function Console() {
  const isAssistantPage = useLocation().pathname === "/assistant";
  const [collapsed, setCollapsed] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);

  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [alerts, setAlerts] = useState<AlertSummary[]>([]);
  /** What the API says exists, so the queue can admit when it is showing a subset. */
  const [alertTotal, setAlertTotal] = useState(0);
  const [chains, setChains] = useState<AttackChain[]>([]);
  const [timeline, setTimeline] = useState<TimelineBucket[]>([]);
  const [graph, setGraph] = useState<NetworkGraph | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  /**
   * A failed load used to be swallowed, leaving the pages rendering empty chrome — which
   * is indistinguishable from "the run genuinely found nothing". In a security tool that
   * ambiguity is the worst possible failure mode, so the error is surfaced with a retry.
   */
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [statsResult, runsResult] = await Promise.all([
        api.get<PlatformStats>("/api/v1/stats"),
        api.get<{ runs: RunSummary[] }>("/api/v1/runs"),
      ]);
      setStats(statsResult);
      setRuns(runsResult.runs);

      const latest = statsResult.last_run ?? runsResult.runs.find((r) => r.status === "succeeded");
      setRun(latest ?? null);

      // Everything below is scoped to one run. Without a completed one there is nothing
      // to draw, and the pages say so rather than rendering empty chrome.
      if (!latest) {
        setAlerts([]);
        setAlertTotal(0);
        setChains([]);
        setTimeline([]);
        setGraph(null);
        return;
      }

      const [alertPage, chainResult, timelineResult, graphResult] = await Promise.all([
        api.get<AlertPage>(`/api/v1/alerts?run_id=${latest.id}&limit=500`),
        api.get<{ chains: AttackChain[] }>(`/api/v1/runs/${latest.id}/chains`),
        api.get<{ buckets: TimelineBucket[] }>(`/api/v1/runs/${latest.id}/timeline`),
        api.get<NetworkGraph>(`/api/v1/runs/${latest.id}/graph`),
      ]);
      setAlerts(alertPage.alerts);
      setAlertTotal(alertPage.total);
      setChains(chainResult.chains);
      setTimeline(timelineResult.buckets);
      setGraph(graphResult);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The request did not complete.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openAlerts = alerts.filter((a) => a.state === "new" || a.state === "investigating").length;

  return (
    <AssistantProvider alerts={alerts} chains={chains} stats={stats}>
    <div className="flex min-h-dvh">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((value) => !value)}
        openAlerts={openAlerts}
      />
      <main className={`min-w-0 flex-1 bg-[var(--color-canvas)] ${isAssistantPage ? "grid h-dvh grid-rows-[auto_minmax(0,1fr)] overflow-hidden" : ""}`}>
        <Topbar
          alerts={alerts}
          onOpenAssistant={() => setAssistantOpen(true)}
          onRefresh={load}
          refreshing={loading}
        />
        {error ? (
          <ErrorState
            title="Could not reach the GraphGuard API"
            message={error}
            onRetry={() => {
              setLoading(true);
              void load();
            }}
          />
        ) : (
        <Routes>
          <Route
            path="/"
            element={
              <Overview
                stats={stats}
                alerts={alerts}
                chains={chains}
                timeline={timeline}
                run={run}
                loading={loading}
              />
            }
          />
          <Route
            path="/alerts"
            element={<Alerts alerts={alerts} loading={loading} total={alertTotal} />}
          />
          <Route path="/alerts/:alertId" element={<AlertDetailPage onChanged={load} />} />
          <Route
            path="/network"
            element={<NetworkMap graph={graph} alerts={alerts} loading={loading} />}
          />
          <Route path="/chains" element={<Chains chains={chains} loading={loading} />} />
          <Route path="/runs" element={<Runs runs={runs} loading={loading} onRefresh={load} />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route
            path="/evidence"
            element={<Investigations alerts={alerts} chains={chains} />}
          />
          <Route path="/settings" element={<Settings />} />
          <Route path="/help" element={<Help />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        )}
      </main>

      <Assistant open={assistantOpen} onClose={() => setAssistantOpen(false)} />
    </div>
    </AssistantProvider>
  );
}

/**
 * The signed-out surface: a public landing page at `/`, with the sign-in form on its own
 * route behind it.
 *
 * Routing rather than a boolean is what makes the landing reachable at all — the console's
 * own routes only exist once there is a session, so an unauthenticated visitor gets this
 * router instead. Anything else redirects to `/` rather than to `/login`, so a stale
 * bookmark into the console lands on the pitch and not on a bare password box.
 */
function PublicRoutes() {
  const navigate = useNavigate();
  return (
    <Routes>
      <Route path="/" element={<Landing onSignIn={() => navigate("/login")} />} />
      <Route path="/login" element={<Login />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function Gate() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner label="Restoring session" />
      </div>
    );
  }
  return user ? <Console /> : <PublicRoutes />;
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <Gate />
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
