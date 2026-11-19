// =============================================================================
// GraphGuard deployment pipeline.
// =============================================================================
// Division of labour with GitHub Actions (DECISIONS.md D24): Actions gates the
// *code* — ruff, black, mypy, pytest on both SQLite and PostgreSQL, migration
// drift, image build. Jenkins deploys, because deployment needs credentials for
// a specific host and that belongs on a runner you control, not on a shared
// cloud runner.
//
// The pipeline deliberately re-runs the gates rather than trusting that Actions
// was green. A deploy is triggered against a *commit*, and nothing guarantees
// the commit being deployed is the one Actions checked — a force-push, a
// manually triggered build, or a branch parameter all break that assumption.
// Re-running costs minutes; deploying an untested commit costs an outage.
//
// Required Jenkins credentials:
//   graphguard-vps-ssh      SSH private key for the deployment host
//   graphguard-env          Secret file: the production .env (JWT secret, DB password)
//
// Required Jenkins parameters are declared below; DEPLOY_HOST has no default on
// purpose, so a misconfigured job fails at validation rather than deploying
// somewhere unexpected. For unattended (poll-triggered) builds the target comes
// from the GRAPHGUARD_DEPLOY_HOST global environment variable instead — set it
// once in Manage Jenkins → System.
// =============================================================================

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timeout(time: 45, unit: 'MINUTES')
    }

    // Poll rather than webhook: polling means the Jenkins controller can stay
    // bound to localhost, instead of exposing an endpoint to the internet just
    // to hear about pushes. Two minutes of latency costs nothing here.
    // Declarative triggers register on the job's *first* run — run it manually
    // once after creating it, or pushes will be ignored.
    triggers {
        pollSCM('H/2 * * * *')
    }

    parameters {
        string(
            name: 'DEPLOY_HOST',
            defaultValue: '',
            description: 'user@host of the target VPS. Required for the deploy stage.'
        )
        string(
            name: 'DEPLOY_PATH',
            defaultValue: '/opt/graphguard',
            description: 'Directory on the VPS holding the checkout and the compose file.'
        )
        booleanParam(
            name: 'RUN_DEPLOY',
            defaultValue: true,
            description: 'Deploy after a green smoke test. Turn off to build and verify only.'
        )
    }

    environment {
        // Never the real values — the smoke test runs against a throwaway stack.
        COMPOSE_PROJECT_NAME = "graphguard-ci-${env.BUILD_NUMBER}"
        UV_VERSION = '0.9.7'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    env.GIT_SHA = sh(returnStdout: true, script: 'git rev-parse --short HEAD').trim()
                    currentBuild.displayName = "#${env.BUILD_NUMBER} · ${env.GIT_SHA}"
                }
            }
        }

        stage('Quality gates') {
            steps {
                sh '''
                    set -eu
                    curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh
                    export PATH="$HOME/.local/bin:$PATH"
                    uv sync --frozen --extra dev
                    uv run ruff check .
                    uv run black --check .
                    uv run mypy src
                    uv run pytest -q
                '''
            }
        }

        stage('Build images') {
            steps {
                // The dashboard builds inside the nginx image (deploy/
                // Dockerfile.nginx), so the agent needs Docker but not Node.
                sh '''
                    set -eu
                    docker build -t graphguard-backend:${GIT_SHA} -t graphguard-backend:latest .
                    docker build -f deploy/Dockerfile.nginx \
                        -t graphguard-nginx:${GIT_SHA} -t graphguard-nginx:latest .
                '''
            }
        }

        // The stage that makes this pipeline worth having. It starts the real
        // stack — Postgres, Redis, migrations, API, worker — and exercises it
        // over HTTP. A container that builds is not a container that runs: a
        // missing runtime library, a bad entrypoint or a migration that fails on
        // an empty database all pass a build and fail here.
        stage('Smoke test the stack') {
            steps {
                sh '''
                    set -eu
                    export POSTGRES_PASSWORD="ci-$(openssl rand -hex 16)"
                    export GRAPHGUARD_JWT_SECRET="ci-$(openssl rand -hex 32)"
                    export HTTP_PORT=18080

                    docker compose up -d --wait postgres redis
                    docker compose run --rm migrate
                    docker compose up -d api worker nginx

                    # Poll rather than sleep: a fixed wait is either flaky or slow.
                    for attempt in $(seq 1 30); do
                        if curl -fsS "http://localhost:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
                            echo "API healthy after ${attempt} attempts"
                            break
                        fi
                        if [ "$attempt" -eq 30 ]; then
                            echo "API never became healthy"
                            docker compose logs --tail=100 api migrate
                            exit 1
                        fi
                        sleep 2
                    done

                    # Authentication must actually work end to end, not merely respond.
                    docker compose exec -T api graphguard platform create-admin \
                        --email ci@graphguard.local --password ci-password-not-secret

                    TOKEN=$(curl -fsS -X POST "http://localhost:${HTTP_PORT}/api/auth/login" \
                        -H 'Content-Type: application/json' \
                        -d '{"email":"ci@graphguard.local","password":"ci-password-not-secret"}' \
                        | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

                    curl -fsS "http://localhost:${HTTP_PORT}/api/v1/stats" \
                        -H "Authorization: Bearer $TOKEN" >/dev/null
                    echo "authenticated request succeeded"

                    # An unauthenticated request must be refused. Without this the
                    # smoke test would pass on a build that had lost its guards.
                    STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
                        "http://localhost:${HTTP_PORT}/api/v1/stats")
                    if [ "$STATUS" != "401" ]; then
                        echo "expected 401 without a token, got $STATUS"
                        exit 1
                    fi
                    echo "unauthenticated request correctly refused"

                    # The dashboard must actually be served. Before the dist was
                    # baked into the nginx image, an API-only stack passed every
                    # check above while / returned 404 — this line is what would
                    # have caught that.
                    curl -fsS "http://localhost:${HTTP_PORT}/" | grep -qi '<!doctype html'
                    echo "dashboard served"
                '''
            }
            post {
                always {
                    sh 'docker compose logs --tail=200 > compose-logs.txt 2>&1 || true'
                    archiveArtifacts artifacts: 'compose-logs.txt', allowEmptyArchive: true
                    sh 'docker compose down -v --remove-orphans || true'
                }
            }
        }

        stage('Deploy') {
            when {
                allOf {
                    expression { params.RUN_DEPLOY }
                    // Not `branch 'main'`: that condition only evaluates in a
                    // multibranch pipeline, where BRANCH_NAME is set. In a plain
                    // pipeline job checkout scm sets GIT_BRANCH to 'origin/main',
                    // and `branch` would silently skip the stage forever.
                    expression {
                        def b = env.BRANCH_NAME ?: env.GIT_BRANCH ?: ''
                        return b == 'main' || b.endsWith('/main')
                    }
                }
            }
            steps {
                script {
                    // Poll-triggered builds run with parameter *defaults*, and
                    // DEPLOY_HOST deliberately has none — so unattended deploys
                    // read the target from a Jenkins-global environment variable
                    // (Manage Jenkins → System → Global properties →
                    // GRAPHGUARD_DEPLOY_HOST). The parameter still wins when a
                    // human sets it on a manual build.
                    env.DEPLOY_TARGET = params.DEPLOY_HOST?.trim() ?: (env.GRAPHGUARD_DEPLOY_HOST ?: '')
                    if (!env.DEPLOY_TARGET) {
                        error 'No deploy target: set the DEPLOY_HOST parameter or the GRAPHGUARD_DEPLOY_HOST global.'
                    }
                }
                withCredentials([
                    sshUserPrivateKey(credentialsId: 'graphguard-vps-ssh', keyFileVariable: 'SSH_KEY'),
                    file(credentialsId: 'graphguard-env', variable: 'ENV_FILE')
                ]) {
                    sh '''
                        set -eu
                        SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new"

                        # The .env never lives in the repository or in the Jenkins
                        # workspace after this stage — it is copied from the credential
                        # store straight to the host.
                        scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
                            "$ENV_FILE" "${DEPLOY_TARGET}:${DEPLOY_PATH}/.env"

                        $SSH "${DEPLOY_TARGET}" bash -se <<REMOTE
set -eu
cd "${DEPLOY_PATH}"

# Record what is currently deployed *before* changing anything, so the rollback
# below has somewhere to go. \`git checkout -\` does not work here: the working
# tree is already detached, so there is no previous branch for it to name.
PREVIOUS=\\$(git rev-parse HEAD)
echo "currently deployed: \\$PREVIOUS"

# Back the database up before migrating. A migration that goes wrong on a
# database holding real triage history is not recoverable from the repository —
# alerts can be regenerated from a run, an analyst's verdicts cannot.
mkdir -p backups
docker compose exec -T postgres pg_dump -U graphguard graphguard \
  | gzip > "backups/pre-deploy-\\$(date +%Y%m%dT%H%M%S).sql.gz"

git fetch --all --prune
git checkout --detach "${GIT_SHA}"

docker compose build
docker compose run --rm migrate
docker compose up -d --wait api worker nginx

for attempt in \\$(seq 1 30); do
  if curl -fsS http://localhost/api/health >/dev/null 2>&1; then
    echo "deployment healthy at ${GIT_SHA}"
    exit 0
  fi
  sleep 2
done

# Roll the *code* back. The database is deliberately left alone: an Alembic
# downgrade against a schema the previous image may not expect is a second
# failure on top of the first, and the pre-deploy dump above is the recovery
# path if the migration itself was the problem.
echo "deployment did not become healthy — rolling back to \\$PREVIOUS"
git checkout --detach "\\$PREVIOUS"
docker compose up -d --build --wait api worker nginx
exit 1
REMOTE
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "GraphGuard ${env.GIT_SHA} passed. Deployed: ${params.RUN_DEPLOY}"
        }
        failure {
            echo "GraphGuard ${env.GIT_SHA} failed at stage: ${currentBuild.description ?: 'see log'}"
        }
        cleanup {
            cleanWs()
        }
    }
}
