# Cloud Run deploy runbook — chrome.net.ua AI Assistant

Target architecture:

```
browser → chrome.net.ua (nginx, TLS, your domain, real traffic)
            └─ /chat/api/ → Cloud Run  europe-central2   (primary)
                            └─ backup: 127.0.0.1:8090    (existing chatbot.service)
```

Region: **europe-central2 (Warsaw)** — nearest GCP region to the Kyiv VPS (~700 km).

Set these once per shell session so the commands below paste cleanly.
**Do not name them `GH_OWNER` / `GH_REPO`** — `gh` treats `GH_REPO` as a repo override and expects
`OWNER/REPO`, so every `gh` command in Phase 6 fails with `expected the "[HOST/]OWNER/REPO" format`.

```bash
export PROJECT_ID="<your-project-id>"
export REGION="europe-central2"
export REPO="containers"
export SERVICE="ai-assistant"
export REPO_OWNER="vladislavtaran"
export REPO_NAME="ai-assistant-math-agent"
```

---

## Phase 0 — SDK and project

```bash
brew install --cask google-cloud-sdk
```

```bash
gcloud auth login
```

Create the project, then create a billing account at https://console.cloud.google.com/billing/create
(needs a card; the $300 / 90-day trial does not auto-charge when it ends). Then link it:

```bash
gcloud billing projects link "$PROJECT_ID" --billing-account=$(gcloud billing accounts list --filter='open=true' --format='value(name)' --limit=1)
```

```bash
gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'
```

**This must read `True` before Phase 1.** Otherwise `services enable` fails with
`UREQ_PROJECT_BILLING_NOT_FOUND`. Note the trial signup silently creates its own throwaway
project — link billing to *yours* explicitly, do not assume it landed in the right place.

```bash
gcloud config set project "$PROJECT_ID"
```

```bash
gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'
```

Save that number — Workload Identity Federation needs the project **number**, not the ID:

```bash
export PROJECT_NUMBER="<number-from-above>"
```

**Checkpoint:** `gcloud config list` shows the right account and project.

---

## Phase 1 — Enable APIs

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com
```

Takes a minute or two. `iamcredentials` and `sts` are the ones people forget — without them WIF fails with a confusing permission error.

---

## Phase 2 — Artifact Registry

The Docker images live here. `REPO=containers` matches what the workflow expects.

```bash
gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" --description="Container images"
```

**Checkpoint:**

```bash
gcloud artifacts repositories list --location="$REGION"
```

---

## Phase 3 — Secret Manager

The workflow reads `--set-secrets GEMINI_API_KEY=gemini-api-key:latest`, so the secret must be named `gemini-api-key`.

```bash
gcloud secrets create gemini-api-key --replication-policy=automatic
```

Now add the value. **Do not paste the key as a command argument** — it lands in your shell history and in the process table. Type it at the prompt instead:

```bash
read -rs GEMINI_KEY && printf '%s' "$GEMINI_KEY" | gcloud secrets versions add gemini-api-key --data-file=- && unset GEMINI_KEY
```

(The prompt shows nothing as you type. Press Enter when done.)

**Checkpoint:** should print `1`:

```bash
gcloud secrets versions list gemini-api-key --format='value(name)'
```

---

## Phase 4 — Two service accounts (least privilege)

Two identities, deliberately separate. This is worth being able to explain.

**4a. Runtime identity** — what the container runs as. Only needs to read the secret.

```bash
gcloud iam service-accounts create ai-assistant-run --display-name="Cloud Run runtime (ai-assistant)"
```

```bash
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:ai-assistant-run@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```

**4b. Deploy identity** — what GitHub Actions acts as. Needs to push images and deploy, nothing else.

```bash
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"
```

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:github-deployer@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/run.admin"
```

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:github-deployer@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
```

The deployer must be allowed to *assign* the runtime identity to the service — that is what `serviceAccountUser` means here:

```bash
gcloud iam service-accounts add-iam-policy-binding "ai-assistant-run@${PROJECT_ID}.iam.gserviceaccount.com" --member="serviceAccount:github-deployer@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/iam.serviceAccountUser"
```

---

## Phase 5 — Workload Identity Federation (the fiddly part)

This is why there is no service-account JSON key anywhere. GitHub presents a short-lived OIDC
token; Google trades it for short-lived credentials. Nothing long-lived to leak.

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github-provider --location=global --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" --attribute-condition="assertion.repository_owner=='${REPO_OWNER}'"
```

`--attribute-condition` is mandatory and it is the security control: without it **any** GitHub
repository on the internet could request credentials against your pool. This restricts it to
repos you own.

Now allow only *this* repo to impersonate the deploy account:

```bash
gcloud iam service-accounts add-iam-policy-binding "github-deployer@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO_OWNER}/${REPO_NAME}"
```

**Checkpoint** — this prints the provider resource name you need next:

```bash
echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
```

---

## Phase 6 — GitHub repository variables

The workflow reads three `vars.*`. Set them from the repo directory:

```bash
gh variable set GCP_PROJECT_ID --body "$PROJECT_ID"
```

```bash
gh variable set GCP_DEPLOY_SA --body "github-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
```

```bash
gh variable set GCP_WIF_PROVIDER --body "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
```

**Checkpoint:**

```bash
gh variable list
```

---

## Phase 7 — Two edits to the workflow

`.github/workflows/deploy-cloudrun.yml`:

1. Region — currently `us-west1`, which is a transatlantic hop from Kyiv:

```
  REGION: europe-central2
```

2. Add the runtime identity to the `gcloud run deploy` block, so the container is not running
   as the over-privileged Compute default service account. Add this line alongside the other flags:

```
            --service-account ai-assistant-run@${{ vars.GCP_PROJECT_ID }}.iam.gserviceaccount.com \
```

---

## Phase 8 — Commit and push

These three paths have been untracked since 24 Aug 2026 — that is why the workflow has never run.

```bash
git add Dockerfile .dockerignore .github/workflows/deploy-cloudrun.yml CLOUDRUN-RUNBOOK.md
```

```bash
git commit -m "Deploy the agent to Cloud Run via Workload Identity Federation"
```

```bash
git push
```

Watch it:

```bash
gh run watch
```

**Expected failures on the first run** — all normal:
- `Permission denied on secret` → the runtime SA binding in 4a did not apply, or the secret name differs
- `iam.serviceAccounts.actAs` denied → the binding in 4b is missing
- `Unable to acquire impersonated credentials` → `PROJECT_NUMBER` vs `PROJECT_ID` mixed up in the WIF provider string
- `403 on artifactregistry` → wrong region in the image path

---

## Phase 9 — Verify

```bash
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
```

```bash
curl -fsS "$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')/api/health"
```

---

## Phase 10 — Cost guardrails

Budget alert: console → Billing → Budgets & alerts → create a budget of a few dollars with
alerts at 50/90/100%. Do this before you forget; it is the safety net for a public endpoint.

Confirm nothing is pinned warm (should print `0` or empty):

```bash
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")'
```

---

## Phase 11 — Point chrome.net.ua at it

On the Ukrainian server, replace the single `proxy_pass` with an upstream that fails over to the
existing local service. Keep `chatbot.service` running — it is now the backup.

```nginx
upstream chat_backend {
    server cloud-run-hostname-xxxx.a.run.app:443 max_fails=2 fail_timeout=10s;
    server 127.0.0.1:8090 backup;
}

location /chat/api/ {
    proxy_pass https://chat_backend/api/;
    proxy_ssl_server_name on;
    proxy_set_header Host cloud-run-hostname-xxxx.a.run.app;
    proxy_http_version 1.1;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
    proxy_buffering off;
}
```

Note `proxy_ssl_server_name on` and the explicit `Host` header — Cloud Run routes by hostname and
returns 404 without them. This is the step most people get wrong.

```bash
nginx -t && systemctl reload nginx
```

---

## What to do next (the part that actually answers the objection)

Deploying is table stakes. The layer that separates you:

1. **Eval suite in CI** — 30–50 prompts with expected tool selections; fail the build if
   tool-selection accuracy regresses. This makes deploys quality-gated, not just automated.
2. **Tracing** — log which tool fired, latency, token cost and failures per run.
3. **Cost per request** — track it. Almost nobody does, and it is the first thing that bites
   in a real agent deployment.

Then the answer to "tell me about deploying agents to production" is a five-minute conversation
with specifics, instead of a sentence.
