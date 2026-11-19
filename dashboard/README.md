# GraphGuard dashboard

React + TypeScript + Vite + Tailwind. The analyst console for GraphGuard.

## Running it

The frontend needs the API. Both, from the repository root:

```bash
# 1. a database with a completed run
export GRAPHGUARD_DATABASE_URL="sqlite+pysqlite:///$PWD/devdata.db"
uv run alembic upgrade head
uv run graphguard platform create-admin        # prompts for email + password

# 2. the API
uv run uvicorn graphguard.api.app:create_app --factory --port 8000

# 3. the frontend
npm --prefix dashboard run dev                 # http://localhost:5173
```

Vite proxies `/api` to port 8000, so fetch paths are identical in development and
behind nginx in deployment — no environment-dependent base URL.

Sign in, open **Detection Runs**, and start one. It takes about eight seconds and
populates the queue, the timeline, the chains and the network map.

## Colour

The brand colour `#293754` is a **surface** — sidebar, cards, panels. White text on it
reads at 11.9:1.

It is deliberately not the accent. Against the near-black canvas it measures 1.6:1,
which is invisible; the accent is a lightened sibling in the same hue family at 7.6:1.
Both live in `src/styles.css` as `--color-brand` and `--color-accent`; changing those two
re-tunes the product.

## Layout

```
src/
  lib/        api client (token refresh), auth context, API types
  components/ Sidebar, Topbar, Assistant, NetworkCanvas, charts, ui primitives
  pages/      Login, Overview, NetworkMap, Alerts, Simple (chains/runs/stubs)
```

`App.tsx` fetches the run-scoped data once and passes it down: the topbar's search and
notification centre need the alert list on every page, and a triage decision made on one
page has to move the sidebar badge on all of them.

## The network map

`NetworkCanvas.tsx` runs a real 3D force simulation (`d3-force-3d`) over the actual graph
edges, so proximity means something — a decorative sphere layout would look similar and
mean nothing. Rendering is canvas 2D with a hand-written projection; at a few hundred
nodes WebGL's advantage does not appear, and this keeps a 3D engine out of the bundle.

Only flagged nodes pulse, and `prefers-reduced-motion` disables that entirely.

## The assistant

Answers from the loaded run data. It is **not** a language model and generates nothing.
In an evidence tool a confident fabrication is worse than a blank, so every answer is
computed from alerts, chains and statistics already on screen, and links to the record it
came from. Wiring a model behind it is a small change, and one to make deliberately.
