# DEPLOYMENT.md — running GraphGuard as a service

How the platform is deployed, and why it is shaped this way. The architecture decisions behind the
component choices are recorded in `DECISIONS.md`; this file is the operational companion.

---

## 1. Topology

```
                    ┌─────────┐
   internet  ──────▶│  nginx  │  :80/:443  TLS, static dashboard, /api proxy, rate limits
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │   api   │  :8000  FastAPI + uvicorn (2 workers)
                    └────┬────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼─────┐    ┌─────▼────┐    ┌──────▼─────┐
   │ postgres │    │  redis   │    │   worker   │  Celery, --concurrency=1
   └──────────┘    └──────────┘    └──────┬─────┘
        ▲                ▲                │
        └────────────────┴────────────────┘
```

Only **nginx** publishes a port. Postgres and Redis are reachable on the compose network alone —
publishing 5432 on a VPS is how databases end up indexed by internet-wide scanners.

**There is no load balancer, deliberately.** One in front of a single-instance service would be
decoration. The API is stateless (session state lives in Redis, not in process memory), so scaling
out is adding replicas behind this proxy and changing nothing else. That is a stronger claim than a
load balancer that balances one thing, because the design demonstrates it.

## 2. First deployment

```bash
cp .env.example .env
uv run python -c "from graphguard.platform.settings import generate_secret; print(generate_secret())"
# paste into GRAPHGUARD_JWT_SECRET, and set POSTGRES_PASSWORD

docker compose up -d --build
docker compose exec api graphguard platform create-admin
```

The `migrate` service runs `alembic upgrade head` and exits; `api` and `worker` wait for it to
complete successfully, so a container can never serve traffic against a schema older than its code.

The first administrator is created through the CLI rather than an HTTP route. An unauthenticated
"create the first user" endpoint is a permanent privilege-escalation hole if its first-user check
is ever wrong, and it is reachable by whoever finds the service first. Requiring shell access
removes that surface entirely.

## 3. Configuration

All deployment settings are environment variables prefixed `GRAPHGUARD_`; see `.env.example`. They
are deliberately separate from `config/default.yaml`, which holds *analytical* configuration.
`DECISIONS.md` D4 requires every run to snapshot its analytical config next to its results — if the
signing key lived on that object it would be serialised into `runs/` the first time anyone saved a
run.

In `production` the service refuses to start when:

- `GRAPHGUARD_JWT_SECRET` is still the development default, or is shorter than 32 characters
- `GRAPHGUARD_DATABASE_URL` points at SQLite

A forgotten variable must crash at boot. Signing tokens with a value published in this repository
would let anyone mint an administrator token, and that failure is silent.

## 4. TLS

Not configured in the repository — and `certbot --nginx` does not apply here: nginx runs in a
container, so certbot on the host cannot rewrite its config. On the VPS, with a domain pointing at
the host:

```bash
# Issue the certificate with the standalone challenge server; port 80 must be
# free for the minute this takes.
docker compose stop nginx
sudo certbot certonly --standalone -d graphguard.example.tn

# A VPS-local copy of the config, extended with a 443 server block and the HTTP
# redirect. It stays untracked so the deploy pipeline's `git checkout` is never
# blocked by a locally modified tracked file.
cp deploy/nginx.conf deploy/nginx.tls.conf   # then edit in the 443 block
```

Wire both in through `docker-compose.override.yml` (untracked, VPS-only — compose merges it over
the committed file automatically):

```yaml
services:
  nginx:
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/nginx.tls.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
```

Renewal needs port 80 back, so hook the container lifecycle into certbot's timer:

```bash
sudo certbot renew --pre-hook  "docker compose -f /opt/graphguard/docker-compose.yml stop nginx" \
                   --post-hook "docker compose -f /opt/graphguard/docker-compose.yml start nginx"
```

Add `Strict-Transport-Security` **after** TLS works, not before — sent over plain HTTP it is either
ignored or pins browsers to a scheme the host does not yet serve.

## 5. Observability

`/metrics` exposes the Prometheus text format. It is restricted to private ranges in
`deploy/nginx.conf`: the endpoint carries no secrets, but it does reveal traffic shape and run
cadence, which is reconnaissance.

Metrics worth alerting on:

| Metric | Why |
|---|---|
| `graphguard_runs_total{status="failed"}` | A failed run produces no alerts, which is indistinguishable from a quiet day |
| `graphguard_run_duration_seconds` | Measured at ~7 s; drift means telemetry volume has outgrown the host |
| `graphguard_alerts_produced_total{detector=...}` | A detector that stops contributing is a misconfiguration the total hides |
| `graphguard_http_request_duration_seconds` | Ordinary service health |

No metric is labelled with an entity identifier. Prometheus labels become time series, so labelling
by API key or IP would explode cardinality *and* copy security telemetry into a store with a
different access-control model from the database.

## 6. Continuous delivery

Two systems, with a deliberate split (`DECISIONS.md` D24):

| | Runs | Why there |
|---|---|---|
| **GitHub Actions** | ruff, black, mypy, pytest on SQLite *and* PostgreSQL, migration-drift check, image build | Gating code needs no secrets and no specific host |
| **Jenkins** (`Jenkinsfile`) | gates again, image build, **full-stack smoke test**, deploy to the VPS | Deployment needs credentials for one host; that belongs on a runner you control |

### Why Jenkins re-runs the gates

A deploy is triggered against a *commit*, and nothing guarantees that commit is the one Actions
checked — a force-push, a manual build, or a branch parameter all break the assumption. Re-running
costs minutes; deploying an untested commit costs an outage.

### The stage that earns the pipeline

`Smoke test the stack` starts the real thing — Postgres, Redis, migrations, API, worker, nginx —
and drives it over HTTP: create an administrator, sign in, call an authenticated endpoint, and
confirm the same endpoint returns **401 without a token**. That last check matters: without it the
smoke test would pass on a build that had lost its auth guards entirely.

A container that builds is not a container that runs. A missing runtime library, a bad entrypoint,
or a migration that fails on an empty database all pass a build and fail here.

### Jenkins setup

Credentials to create:

| Id | Kind | Contents |
|---|---|---|
| `graphguard-vps-ssh` | SSH private key | Deploy key for the VPS |
| `graphguard-env` | Secret file | The production `.env` |

The `.env` is copied from the credential store straight to the host and never persists in the
workspace. `DEPLOY_HOST` has no default, so a misconfigured job fails validation rather than
deploying somewhere unexpected. `RUN_DEPLOY` defaults to *on*: the job polls the repository
(`pollSCM`, every two minutes) and deploys `main` automatically once the gates and the smoke test
are green — that is the continuous-deployment loop. Turn the parameter off for a build-and-verify
run. Polling was chosen over a GitHub webhook so the Jenkins controller can stay bound to
localhost instead of exposing an endpoint to the internet.

The agent needs Docker and network access to fetch `uv`. Node is not required: the dashboard
builds inside the nginx image (`deploy/Dockerfile.nginx`), which is also what fixes the earlier
gap where the `dashboard-dist` volume had no producer and a deployed stack served the API but a
404 for the UI. The smoke test now asserts `/` returns the SPA for exactly that reason.

### Deploy safety

- **Database dumped before migrating.** Alerts can be regenerated from a run; an analyst's verdicts
  cannot. That dump is the recovery path if a migration is what went wrong.
- **Rollback is code-only.** If the new release fails its health check the previous commit is
  restored and rebuilt. The schema is deliberately *not* downgraded: an Alembic downgrade against a
  schema the old image may not expect is a second failure stacked on the first.
- **`disableConcurrentBuilds`** — two deploys racing to migrate one database is a corruption bug
  with no good outcome.

### Not yet verified

The pipeline has never run. There is no Jenkins server yet, and no Docker daemon on the development
machine, so the image has never been built and the compose stack has never started. What *has* been
checked: the `Jenkinsfile` parses (balanced blocks, all declarative sections present) and all four
embedded shell scripts pass `sh -n`. Everything past that is unproven until it runs on the VPS.

Expect the first run to surface at least one of: a missing package in the image, a permission
problem on the non-root user, or the CUDA-bundled `torch` wheel making the image far larger than
necessary (pinning the CPU-only index is the fix, and is worth doing against a real build rather
than guessed at now).

## 7. Backups

Postgres holds the only irreplaceable state — accounts, and the triage decisions analysts have
recorded. Alerts themselves are reproducible from a run's stored configuration and seed; analyst
judgements are not.

```bash
docker compose exec postgres pg_dump -U graphguard graphguard | gzip > backup-$(date +%F).sql.gz
```

The Redis volume is persisted (`--appendonly yes`) for one specific reason: losing it would
silently un-revoke every refresh token that had been logged out or revoked after a compromise.

## 8. Known gaps

Stated rather than hidden, because a deployment section that claims completeness invites the
question it cannot answer.

- **No TLS in the repository.** Deliberate (§4), but it means `docker compose up` alone is not
  internet-ready.
- **`X-Forwarded-For` is trusted.** Correct behind this nginx, wrong if the API is ever exposed
  directly — the header is attacker-controlled, and the rate limiter keys on it.
- **Single worker.** `--concurrency=1` because a run is CPU-bound on numerical work that already
  uses multiple threads; concurrent runs on one host contend for the same cores. Scaling means more
  worker *containers*, ideally on more hosts.
- **No log aggregation.** Container logs only.
- **Secrets are environment variables**, not a secret manager. Adequate for a single VPS; a
  multi-host deployment should move them.
