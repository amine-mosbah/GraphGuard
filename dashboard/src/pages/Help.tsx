/**
 * The help centre.
 *
 * What was here before was a single reference essay: correct, well written, and not a help
 * centre. It answered "what is a community-drift detector" and nothing else — not "how do I work
 * the queue", not "what does this button do", not "why is this page empty", not "what does this
 * word mean". An analyst arriving with a task had to read the whole page to find out it did not
 * contain their answer.
 *
 * So this is organised the way help is actually used: by the question being asked. Task
 * walkthroughs for someone trying to do something, concept articles for someone trying to
 * understand something, a glossary for someone who hit an unfamiliar word, troubleshooting for
 * someone whose screen is not doing what they expected, and the limitations — kept verbatim,
 * because a tool that presents scores without saying where they stop being reliable teaches
 * over-trust, and every one of them is measured and recorded in the project's decision log.
 *
 * Search is over the article text, not just the titles. Titles are the one part of a help centre
 * the reader is least likely to be able to guess.
 */

import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  BookOpen,
  Bug,
  Fingerprint,
  GitCompareArrows,
  GraduationCap,
  Library,
  ListChecks,
  Search,
  Users,
  Waypoints,
} from "lucide-react";
import { EmptyState, PageHeader, Panel } from "../components/ui";

type Category = "start" | "concepts" | "workflow" | "glossary" | "trouble" | "limits";

interface Article {
  id: string;
  category: Category;
  title: string;
  /** One line, shown in the index and matched by search. */
  summary: string;
  /** Extra words search should match — the terms a reader is likely to type. */
  keywords?: string;
  body: ReactNode;
}

const CATEGORIES: { id: Category; label: string; blurb: string; Icon: typeof BookOpen }[] = [
  {
    id: "start",
    label: "Getting started",
    blurb: "What this console is and how to work it",
    Icon: GraduationCap,
  },
  {
    id: "workflow",
    label: "Doing the work",
    blurb: "Triage, cases, runs and configuration, step by step",
    Icon: ListChecks,
  },
  {
    id: "concepts",
    label: "How detection works",
    blurb: "The graph, the three detectors, and how to read a score",
    Icon: BookOpen,
  },
  {
    id: "glossary",
    label: "Glossary",
    blurb: "Every term the interface uses",
    Icon: Library,
  },
  {
    id: "trouble",
    label: "Troubleshooting",
    blurb: "When a page is empty or a control refuses",
    Icon: Bug,
  },
  {
    id: "limits",
    label: "Known limitations",
    blurb: "Where the system is measurably weak",
    Icon: AlertTriangle,
  },
];

export function Help() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | null>(null);
  const { hash } = useLocation();

  const articles = useMemo(() => ARTICLES, []);

  const results = useMemo(() => {
    const term = query.trim().toLowerCase();
    const pool = category ? articles.filter((a) => a.category === category) : articles;
    if (term.length < 2) return pool;
    return pool.filter((article) =>
      `${article.title} ${article.summary} ${article.keywords ?? ""} ${textOf(article.body)}`
        .toLowerCase()
        .includes(term),
    );
  }, [articles, category, query]);

  const searching = query.trim().length >= 2;

  return (
    <PageHeader
      title="Help centre"
      subtitle="How to work the console, what each detector looks for, what every term means, and where the system is known to be weak."
    >
      <Panel className="px-5 py-5">
        <label className="relative block">
          <Search
            size={16}
            className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-[var(--color-ink-faint)]"
            aria-hidden
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search help — try “window score”, “why is my queue empty”, “export”"
            aria-label="Search the help centre"
            className="w-full rounded-control border border-[var(--color-hairline)] bg-[var(--color-raise)] py-2.5 pr-3.5 pl-10 text-sm text-[var(--color-ink)] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)]/50 focus:outline-none"
          />
        </label>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <CategoryChip
            label="Everything"
            active={category === null}
            onClick={() => setCategory(null)}
            count={articles.length}
          />
          {CATEGORIES.map((entry) => (
            <CategoryChip
              key={entry.id}
              label={entry.label}
              active={category === entry.id}
              onClick={() => setCategory(entry.id)}
              count={articles.filter((a) => a.category === entry.id).length}
            />
          ))}
        </div>
      </Panel>

      {searching && (
        <p className="text-xs text-[var(--color-ink-faint)]">
          {results.length === 0
            ? `Nothing matches “${query}”.`
            : `${results.length} article${results.length === 1 ? "" : "s"} match “${query}”.`}
        </p>
      )}

      {results.length === 0 ? (
        <Panel>
          <EmptyState
            icon={<Search size={26} />}
            title="No article covers that yet"
            body="Try a single word from the interface — a column heading, a state name, or the label on the control that is confusing you."
          />
        </Panel>
      ) : (
        CATEGORIES.filter((entry) => results.some((article) => article.category === entry.id)).map(
          (entry) => (
            <section key={entry.id} className="space-y-3">
              <header className="flex items-center gap-2.5 pt-1">
                <entry.Icon size={17} className="text-[var(--color-accent)]" aria-hidden />
                <h2 className="text-[15px] font-semibold tracking-tight">{entry.label}</h2>
                <span className="text-xs text-[var(--color-ink-faint)]">{entry.blurb}</span>
              </header>
              <div className="space-y-3">
                {results
                  .filter((article) => article.category === entry.id)
                  .map((article) => (
                    <ArticleCard
                      key={article.id}
                      article={article}
                      // Deep links from elsewhere in the console (`/help#chains`) land on the
                      // article expanded, so the link resolves to an answer rather than to a
                      // page the reader then has to search.
                      startOpen={searching || hash === `#${article.id}`}
                    />
                  ))}
              </div>
            </section>
          ),
        )
      )}
    </PageHeader>
  );
}

function CategoryChip({
  label,
  active,
  onClick,
  count,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  count: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-xs font-medium ring-1 transition-colors ${
        active
          ? "bg-[var(--color-accent)]/16 text-[var(--color-accent)] ring-[var(--color-accent)]/35"
          : "bg-[var(--color-raise)] text-[var(--color-ink-dim)] ring-[var(--color-hairline)] hover:text-[var(--color-ink)]"
      }`}
    >
      {label}
      <span className="tabular-nums opacity-60">{count}</span>
    </button>
  );
}

function ArticleCard({ article, startOpen }: { article: Article; startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  // A search that narrows to one article should show it, not make the reader click again.
  const expanded = startOpen || open;

  return (
    <Panel id={article.id} className="scroll-mt-6 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-3 px-5 py-4 text-left transition-colors hover:bg-[var(--color-raise-subtle)]"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-medium text-[var(--color-ink)]">
            {article.title}
          </span>
          <span className="mt-0.5 block text-xs text-[var(--color-ink-faint)]">
            {article.summary}
          </span>
        </span>
        <span className="mt-0.5 shrink-0 text-[var(--color-ink-faint)]">{expanded ? "−" : "+"}</span>
      </button>
      {expanded && (
        <div className="space-y-3 border-t border-[var(--color-line)] px-5 py-4 text-sm leading-relaxed text-[var(--color-ink-dim)]">
          {article.body}
        </div>
      )}
    </Panel>
  );
}

/** Flatten an article body to plain text so search can match its contents, not just its title. */
function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  if (typeof node === "object" && "props" in node) {
    return textOf((node.props as { children?: ReactNode }).children);
  }
  return "";
}

/* --- reusable article pieces --------------------------------------------- */

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="space-y-2.5">
      {items.map((item, index) => (
        <li key={index} className="flex gap-3">
          <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-chip bg-[var(--color-accent)]/14 text-[10px] font-semibold text-[var(--color-accent)]">
            {index + 1}
          </span>
          <span className="min-w-0 flex-1 text-sm">{item}</span>
        </li>
      ))}
    </ol>
  );
}

function Terms({ items }: { items: { term: string; body: ReactNode }[] }) {
  return (
    <dl className="space-y-3">
      {items.map((item) => (
        <div key={item.term}>
          <dt className="text-[13px] font-medium text-[var(--color-ink)]">{item.term}</dt>
          <dd className="mt-0.5 text-xs leading-relaxed text-[var(--color-ink-faint)]">
            {item.body}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Note({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg bg-[var(--color-surface-2)] px-3.5 py-2.5 text-xs leading-relaxed text-[var(--color-ink-faint)]">
      {children}
    </p>
  );
}

function Where({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-[var(--color-accent)] hover:underline">
      {children}
    </Link>
  );
}

/* --- the articles -------------------------------------------------------- */

const ARTICLES: Article[] = [
  {
    id: "what-is-this",
    category: "start",
    title: "What this console is",
    summary: "A graph over your telemetry, three detectors on top of it, and a queue for a human",
    keywords: "overview introduction graphguard purpose",
    body: (
      <>
        <p>
          GraphGuard reads telemetry from more than one source — the AI platform's API calls and
          the HTTP gateway's access log — and turns all of it into a{" "}
          <strong className="text-[var(--color-ink)]">single graph</strong> in which users, API
          keys, models, addresses and endpoints are vertices and every event is a timestamped edge
          between two of them.
        </p>
        <p>
          Three detectors then run over that graph, and what they find becomes a ranked queue of
          alerts for you to judge. Nothing is ever blocked automatically.
        </p>
        <p>The six pages, in the order you would normally use them:</p>
        <Terms
          items={[
            {
              term: "Overview",
              body: "The state of the current run at a glance: how many alerts are open, what the telemetry looked like, whether anything crossed layers.",
            },
            {
              term: "Triage queue",
              body: "Every alert, worst first. This is where the work happens.",
            },
            {
              term: "Network map",
              body: "The graph itself, drawn. Useful for seeing what a flagged entity is connected to.",
            },
            {
              term: "Attack chains",
              body: "Findings that span both telemetry layers — an external address that became an internal identity.",
            },
            {
              term: "Detection runs",
              body: "Every execution, what it found, and how well each detector scored on it.",
            },
            {
              term: "Investigations",
              body: "Cases. Where a dozen related alerts become one incident.",
            },
          ]}
        />
      </>
    ),
  },
  {
    id: "first-hour",
    category: "start",
    title: "Your first hour",
    summary: "A concrete path from opening the console to closing your first alert",
    keywords: "tutorial walkthrough getting started first time new user",
    body: (
      <Steps
        items={[
          <>
            Open <Where to="/runs">Detection runs</Where> and check that a run has succeeded. If the
            list is empty, an administrator has to start one — everything else in the console is
            scoped to a completed run.
          </>,
          <>
            Open that run and read the <strong className="text-[var(--color-ink)]">Evaluation</strong>{" "}
            tab. This tells you which detector is trustworthy on this data before you start acting
            on what it says.
          </>,
          <>
            Go to the <Where to="/alerts">triage queue</Where>. It is already sorted worst-first;
            start at the top.
          </>,
          <>
            Open the first alert. Read the explanation and the contributing factors — they say
            which behaviours were unusual and by how much, measured against the entity's peers in
            the same window.
          </>,
          <>
            Move it to <strong className="text-[var(--color-ink)]">Investigating</strong> so nobody
            else duplicates your work, then decide. Record the verdict with a rationale; the
            rationale is what makes the queue re-readable next month.
          </>,
          <>
            If several alerts turn out to be the same incident, open a case in{" "}
            <Where to="/evidence">Investigations</Where> and attach them.
          </>,
        ]}
      />
    ),
  },
  {
    id: "shortcuts",
    category: "start",
    title: "Keyboard and navigation",
    summary: "The few things worth knowing about moving around",
    keywords: "keyboard shortcuts accessibility navigation collapse sidebar theme dark light",
    body: (
      <>
        <Terms
          items={[
            {
              term: "Collapse the sidebar",
              body: "The chevron at the top of the navigation rail. Worth doing on the network map, where 260px of chrome is a real cost.",
            },
            {
              term: "Theme",
              body: (
                <>
                  Settings → Account, or the control in the top bar. Both themes are designed
                  separately rather than inverted.
                </>
              ),
            },
            {
              term: "Segmented controls",
              body: "Arrow keys move within them; they hold one stop in the tab order rather than one per option.",
            },
            {
              term: "Reduced motion",
              body: "Honoured from your system setting, including in the charts — nothing here overrides it.",
            },
          ]}
        />
        <Note>
          Severity is never carried by colour alone anywhere in the console: every severity has an
          icon, and every entity type has a letter glyph as well as a hue.
        </Note>
      </>
    ),
  },

  {
    id: "why-graph",
    category: "concepts",
    title: "Why a graph rather than a log search",
    summary: "The one idea the rest of the product follows from",
    keywords: "graph theory relational structure motivation grep siem",
    body: (
      <>
        <p>
          Malicious activity is rarely visible in a single log line. A stolen credential, a ring of
          coordinated accounts, an external attacker who pivots inward — each individual event
          looks ordinary, and what is suspicious is the{" "}
          <em>pattern of relationships over time</em>.
        </p>
        <p>
          Modelling telemetry as a graph makes those relationships first-class and computable: the
          neighbourhood of an identity, the distance between an external address and an internal
          account, how tightly a cluster moves together, how far a group has drifted from where it
          was last week. Questions that cannot be expressed over a flat log become ordinary
          queries.
        </p>
        <Note>
          That the structure is also drawable is a useful side effect, not the reason for it. The
          value of the graph is computational; the network map is what you get for free.
        </Note>
      </>
    ),
  },
  {
    id: "detectors",
    category: "concepts",
    title: "The three detectors, and what each is blind to",
    summary: "Node outlier, abnormal subgraph, community drift",
    keywords: "node outlier egonet dominant abnormal subgraph community drift pygod detector",
    body: (
      <div className="grid gap-4 lg:grid-cols-3">
        <DetectorCard
          icon={<Fingerprint size={18} />}
          name="Node outlier"
          question="Does this identity still behave like itself?"
          catches="A compromised credential — new models, unfamiliar hours, a volume jump."
          caveat="A persistent attack gradually becomes the baseline it is measured against, so a slow, patient adversary is measurably favoured."
        />
        <DetectorCard
          icon={<Users size={18} />}
          name="Abnormal subgraph"
          question="Are these entities acting in suspicious unison?"
          catches="Coordinated abuse — many accounts driven by one actor, with near-identical timing and targets."
          caveat="Detection rests on synchronicity, so an attacker who deliberately jitters their schedule can dilute the signal."
        />
        <DetectorCard
          icon={<GitCompareArrows size={18} />}
          name="Community drift"
          question="Has a whole group moved together?"
          catches="Gradual compromise or misconfiguration, where no single member looks unusual because the neighbours moved too."
          caveat="A team legitimately adopting a new model looks the same as a drifting cohort. Drift is a question for a human, not an answer."
        />
      </div>
    ),
  },
  {
    id: "scores",
    category: "concepts",
    title: "Reading a score",
    summary: "What the numbers do and do not mean",
    keywords: "score raw window normalised threshold contributing factors reasons deviation",
    body: (
      <>
        <p>
          <strong className="text-[var(--color-ink)]">A score is evidence, never a verdict.</strong>{" "}
          It says an entity is unlike comparable peers, or unlike its own past. A legitimate power
          user is also unlike its peers — by construction. GraphGuard never blocks anything; it
          ranks things for a human to judge.
        </p>
        <p>
          <strong className="text-[var(--color-ink)]">Two scores, two meanings.</strong> The{" "}
          <em>raw</em> score orders the queue across every window. The <em>window</em> score is
          scaled within one window and answers a narrower question — did this entity stand out
          among its contemporaries? Ranking by the window score would give you each window's
          winner regardless of how anomalous any of them were.
        </p>
        <p>
          <strong className="text-[var(--color-ink)]">
            Contributing factors are peer deviations.
          </strong>{" "}
          They describe what is unusual about an entity, measured against the other entities in
          the same window. They are not a decomposition of how a model reached its score — for a
          neural detector that number is a reconstruction error and is not decomposable.
          Presenting one as the other would be a false claim.
        </p>
      </>
    ),
  },
  {
    id: "chains",
    category: "concepts",
    title: "Attack chains",
    summary: "A path, not a score — and how to read the figure",
    keywords: "chain cross layer correlation pivot breach confidence swimlane dwell",
    body: (
      <>
        <p>
          A chain links an external event to an internal identity through a shared address: a burst
          against the gateway, then a successful credential use from the same source, then that
          credential behaving unlike itself. Each half is unremarkable alone, which is why no
          single-layer detector produces this. It exists because both telemetry layers are
          vertices in <em>one</em> graph.
        </p>
        <p>
          On the <Where to="/chains">chains page</Where> each chain is drawn across two lanes. The
          upper lane is gateway telemetry, the lower is AI-platform telemetry, and the{" "}
          <strong className="text-[var(--color-ink)]">red connector is the crossing</strong> — the
          moment an external address becomes an internal identity. Steps are placed in time order
          and spaced apart to stay readable; the elapsed time printed on each connector is the real
          gap.
        </p>
        <Terms
          items={[
            {
              term: "Confidence",
              body: "How much corroborates the join. It reaches 1.00 when a detector independently flagged the internal identity after the pivot — that is, when the two halves agree without having been told about each other.",
            },
            {
              term: "Where the time went",
              body: "The dwell between consecutive steps. A long gap between the pivot and the internal detection is the window an attacker actually had.",
            },
            {
              term: "Steps view",
              body: "The same chain as a table — readable without colour, and copyable into an incident report.",
            },
          ]}
        />
        <Note>
          An empty chain list is a result, not a gap. The control case — a brute-force attempt that
          never succeeds — correctly yields nothing, and a correlator that produced a chain anyway
          would be the thing to worry about.
        </Note>
      </>
    ),
  },

  {
    id: "network-map",
    category: "concepts",
    title: "Reading the network map",
    summary: "What node size, edge colour and edge thickness are each saying",
    keywords: "network map graph 3d edges links relations colour red green risk click inspect",
    body: (
      <>
        <p>
          The layout is a real force simulation over the actual edges, so proximity means
          something: entities that interact settle near one another. It is not a decorative
          arrangement.
        </p>
        <Terms
          items={[
            {
              term: "Node size",
              body: "Reach and risk together — how many entities it connects to, how many alerts it carries, and how high they scored.",
            },
            {
              term: "Node colour and letter",
              body: "The entity type. The letter is there so type never depends on colour alone.",
            },
            {
              term: "A pulsing node",
              body: "It carries at least one alert. Only flagged entities animate; animating the whole estate would drown the signal in movement.",
            },
            {
              term: "Edge thickness and opacity",
              body: "How many interactions the relation represents, on a log scale, and how much risk it inherits. Both are encoded so the map still reads in greyscale.",
            },
            {
              term: "A bold red line",
              body: "Both entities it connects are flagged. These are the few worth looking at first.",
            },
            {
              term: "A faint pink line",
              body: "One end is flagged. In a typical run over a quarter of all edges touch a flagged entity, so they are deliberately kept quiet.",
            },
            {
              term: "The faint green mesh",
              body: "Neither end is flagged. Structure, not signal.",
            },
          ]}
        />
        <Note>
          <strong className="text-[var(--color-ink)]">Edge colour is inherited, not scored.</strong>{" "}
          GraphGuard&apos;s detectors score entities; nothing in the system assigns an anomaly
          score to a single interaction. A red line therefore means &ldquo;this relation touches
          something a detector flagged&rdquo;, not &ldquo;the traffic on this line was
          malicious&rdquo;. The one thing measured per relation — how many of its interactions
          were blocked, refused or errored — is shown separately when you click it, precisely so
          the two are not confused.
        </Note>
        <p>
          Clicking a line opens the relation: its two entities, the action type, the interaction
          count, the failure ratio, the period it spans, and the alerts on either end. Clicking a
          node instead isolates its direct neighbourhood. The relation chips above the canvas
          filter the map to one kind of interaction — <code>used_from</code> is the one that
          crosses telemetry layers.
        </p>
      </>
    ),
  },
  {
    id: "triage",
    category: "workflow",
    title: "Working the triage queue",
    summary: "What each state commits you to, and in what order to do things",
    keywords: "triage states new investigating true positive false positive exported workflow",
    body: (
      <>
        <Terms
          items={[
            { term: "New", body: "Produced by a run, not yet looked at." },
            {
              term: "Investigating",
              body: "Someone has picked it up. Reachable from any state, including a verdict you want to revisit.",
            },
            {
              term: "True positive",
              body: "Confirmed. Only a confirmed alert can be exported as an incident.",
            },
            {
              term: "False positive",
              body: "Judged benign. Not a deletion — the alert and your reasoning stay in the audit trail.",
            },
            { term: "Exported", body: "An incident report has left the system." },
          ]}
        />
        <p>
          Verdicts can be reopened. An analyst who cannot correct a mistake will avoid recording
          verdicts at all, which leaves the queue less honest than a correction would.
        </p>
        <Note>
          Every transition is recorded with who made it and why. The rationale is not paperwork —
          it is the only thing that makes a closed alert re-readable by someone who was not there.
        </Note>
      </>
    ),
  },
  {
    id: "cases",
    category: "workflow",
    title: "Opening and running a case",
    summary: "Turning a dozen alerts into one incident",
    keywords: "investigation case evidence attach export contained closed incident",
    body: (
      <>
        <p>
          One compromised credential produces alerts in every window it was active, from more than
          one detector. Triaging each alone loses the fact that they are a single incident — which
          would reproduce, in the interface, exactly the relational blindness this project argues
          against.
        </p>
        <Steps
          items={[
            <>
              In <Where to="/evidence">Investigations</Where>, open a case and give it a title that
              names the entity, not the detector.
            </>,
            <>
              Move the relevant alerts to <em>Investigating</em> or <em>True positive</em> first —
              a case is for evidence you have looked at, so only those are offered.
            </>,
            <>
              Attach them, and attach any chain that involves the same identity. Give each
              attachment a reason; it is what the case reads like afterwards.
            </>,
            <>
              Move the case to <em>Contained</em> when the exposure is stopped and{" "}
              <em>Closed</em> when it is written up. Export it to get one document covering the
              whole incident.
            </>,
          ]}
        />
      </>
    ),
  },
  {
    id: "runs-and-config",
    category: "workflow",
    title: "Runs and detection parameters",
    summary: "Starting a run, reading its evaluation, and changing thresholds safely",
    keywords: "run configuration threshold window revision rollback admin evaluation roc auc",
    body: (
      <>
        <p>
          A run analyses the telemetry with{" "}
          <strong className="text-[var(--color-ink)]">one configuration</strong> and writes its
          alerts, chains, graph and evaluation metrics. The configuration is stamped onto the run
          at dispatch, so an alert can always be read against the thresholds that actually produced
          it — not against whatever they have since become.
        </p>
        <Steps
          items={[
            <>
              Change a parameter in <Where to="/settings">Settings → Detection</Where>. Use{" "}
              <em>Validate</em> first: it checks the whole configuration server-side without saving
              anything.
            </>,
            <>
              Save it. Every save is a new revision with your note attached, and earlier revisions
              stay activatable.
            </>,
            <>
              Start a run from <Where to="/runs">Detection runs</Where> and open it when it
              finishes.
            </>,
            <>
              Read the <em>Evaluation</em> tab. ROC-AUC below the 0.50 rule means the detector did
              worse than a coin toss on that label set; average precision below the positive rate
              means worse than random ranking.
            </>,
          ]}
        />
        <Note>
          Only administrators may change parameters or start runs. That is deliberate: a threshold
          quietly raised is indistinguishable, from the queue, from a period with no attacks.
        </Note>
      </>
    ),
  },

  {
    id: "glossary",
    category: "glossary",
    title: "Terms used in this interface",
    summary: "Every word the console shows you, defined once",
    keywords:
      "glossary definitions window snapshot entity node edge egonet dominant baseline positive rate telemetry layer adapter atlas owasp",
    body: (
      <Terms
        items={[
          {
            term: "Trace event",
            body: "One normalised record — who, what, when, outcome — produced by an adapter from a raw log line. Nothing downstream ever sees the raw format.",
          },
          {
            term: "Layer",
            body: "Which telemetry source an event came from: ai (the platform's API), http (the gateway's access log). An address that appears in both is what lets a chain exist.",
          },
          {
            term: "Window",
            body: "A fixed slice of time. The graph is rebuilt per window, and an entity is scored against its contemporaries within one.",
          },
          {
            term: "Entity",
            body: "A vertex: a user, an API key, a model, an IP address, an endpoint. Identity is the pair (type, id) — never the bare id.",
          },
          {
            term: "Egonet",
            body: "An entity's immediate neighbourhood. The classical node-outlier baseline scores how far its shape departs from its peers'.",
          },
          {
            term: "DOMINANT",
            body: "A graph autoencoder from the PyGOD library. It scores by reconstruction error, which is why its score cannot be decomposed into contributing factors.",
          },
          {
            term: "Baseline",
            body: "A deliberately simple flat-log rule — request volume, error rate, out-of-hours activity — kept in every evaluation so the graph detectors have something honest to beat.",
          },
          {
            term: "ROC-AUC",
            body: "Ranking quality across all thresholds. 0.50 is a coin toss; below that is worse than guessing and is reported rather than hidden.",
          },
          {
            term: "Average precision",
            body: "How much of the top of the ranked queue is real. The fairer measure when positives are rare, because the baseline it must beat is the positive rate rather than 0.50.",
          },
          {
            term: "Positive rate",
            body: "The share of observations that are genuinely malicious. What random ranking would score on average precision.",
          },
          {
            term: "Ground truth",
            body: "The label the simulator attached when it generated the event. Present in evaluation data and absent in production, which is why detection never reads it.",
          },
          {
            term: "MITRE ATLAS / OWASP LLM Top 10",
            body: "Threat taxonomies. Alerts carry the tactic tags their detector family maps to, so a finding can be named in language a wider security team already uses.",
          },
          {
            term: "Revision",
            body: "One saved configuration. Runs record which one they used, so a verdict stays interpretable after the thresholds change.",
          },
        ]}
      />
    ),
  },

  {
    id: "empty-pages",
    category: "trouble",
    title: "A page is empty",
    summary: "Which empty screens are a problem and which are a result",
    keywords: "empty no data blank nothing missing alerts chains graph",
    body: (
      <Terms
        items={[
          {
            term: "Everything is empty",
            body: "There is no completed run. Every run-scoped page draws from the most recent successful one; ask an administrator to start a run.",
          },
          {
            term: "No chains, but alerts exist",
            body: "A result, not a gap. The correlator reports a chain only when both halves are present, and the no-breach control scenario correctly yields none.",
          },
          {
            term: "No alerts, but the run succeeded",
            body: "The alert threshold is above every score the run produced. The run's Configuration tab records what it was set to.",
          },
          {
            term: "The queue looks shorter than the total",
            body: "The queue fetches a page of alerts, not all of them. The header states the total when it is showing a subset.",
          },
          {
            term: "A case shows “evidence removed with its run”",
            body: "The alert or chain it referenced belonged to a run that has since been deleted. The slot stays visible on purpose, so the case does not silently shrink.",
          },
        ]}
      />
    ),
  },
  {
    id: "refused",
    category: "trouble",
    title: "A control refuses, or something errors",
    summary: "Permissions, stale configuration revisions, and expired sessions",
    keywords: "403 forbidden 409 500 error permission admin analyst read only stale schema session",
    body: (
      <Terms
        items={[
          {
            term: "“Start a run” or the settings form is read-only",
            body: "You are signed in as an analyst. Changing detection parameters and starting runs are administrator actions.",
          },
          {
            term: "A settings revision says it cannot be activated",
            body: "It was written against an earlier version of the configuration schema — a parameter has since been renamed or removed, so the stored payload no longer validates. Runs fall back to the file; adjust the values in the form and save a new revision.",
          },
          {
            term: "An alert refuses to export",
            body: "Only a confirmed true positive can be exported. Exporting an unreviewed or benign alert would push unvetted material into whatever consumes the report.",
          },
          {
            term: "A state change is rejected as a conflict",
            body: "Someone else moved the alert while you had it open. Reload and look at the history — it records who moved it and why.",
          },
          {
            term: "You were signed out mid-task",
            body: "Access tokens are short-lived and refresh transparently, so this should not happen. If it does, the refresh token was revoked or expired — sign in again; nothing you recorded is lost.",
          },
        ]}
      />
    ),
  },

  {
    id: "limitations",
    category: "limits",
    title: "Where this system is measurably weak",
    summary: "Documented, evidenced, and deliberately not tuned away",
    keywords: "limitations weakness baseline erosion evasion drift synthetic validation honesty",
    body: (
      <>
        <ul className="space-y-3">
          <Limitation title="A persistent attack erodes its own baseline">
            History-relative features compare an entity against its own recent past, so a long
            campaign is gradually absorbed into the baseline. Detection is not lost — out-of-hours
            and error ratios stay elevated — but a patient adversary is favoured.
          </Limitation>
          <Limitation title="Unison detection is evadable">
            Coordinated-abuse detection keys on synchronicity. Jittered schedules dilute it.
          </Limitation>
          <Limitation title="Drift cannot distinguish cause">
            A group moving together is detected. Whether it moved because of an attack or because
            the team adopted a new tool is a question the graph cannot answer.
          </Limitation>
          <Limitation title="The neural detector does not beat the classical one here">
            On this data the egonet baseline outperforms the graph autoencoder. That is reported
            rather than tuned around, and it is why both are run.
          </Limitation>
          <Limitation title="Validation is largely synthetic">
            Most scenarios come from a simulator with ground truth known by construction, which
            means detectors are shown to work against the adversary that was modelled.
          </Limitation>
        </ul>
        <Note>
          None of these are hedges. Each is measured, recorded in the project's decision log with
          its evidence, and left in place — a tool that presents scores without saying where they
          stop being reliable teaches its users to over-trust it, and an analyst who discovers a
          blind spot during an incident trusts nothing afterwards.
        </Note>
      </>
    ),
  },
];

function DetectorCard({
  icon,
  name,
  question,
  catches,
  caveat,
}: {
  icon: ReactNode;
  name: string;
  question: string;
  catches: string;
  caveat: string;
}) {
  return (
    <div className="rounded-xl bg-[var(--color-surface-2)] p-4">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="grid size-9 place-items-center rounded-xl bg-[var(--color-accent)]/12 text-[var(--color-accent)]">
          {icon}
        </span>
        <h3 className="text-sm font-semibold text-[var(--color-ink)]">{name}</h3>
      </div>
      <p className="text-sm text-[var(--color-ink)] italic">“{question}”</p>
      <p className="mt-2.5 text-xs leading-relaxed text-[var(--color-ink-dim)]">{catches}</p>
      <p className="mt-3 border-t border-[var(--color-line)] pt-2.5 text-xs leading-relaxed text-[var(--color-ink-faint)]">
        <span className="font-medium text-[var(--color-medium)]">Limit — </span>
        {caveat}
      </p>
    </div>
  );
}

function Limitation({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <Waypoints size={14} className="mt-1 shrink-0 text-[var(--color-medium)]" aria-hidden />
      <span>
        <span className="block text-[13px] text-[var(--color-ink)]">{title}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-[var(--color-ink-faint)]">
          {children}
        </span>
      </span>
    </li>
  );
}
