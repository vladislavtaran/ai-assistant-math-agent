# Cloud Run deployment — what was actually done

Record of the real session, 2026-09-28. The runbook (`CLOUDRUN-RUNBOOK.md`) is the *plan*;
this is the *log*, including the two things that went wrong and how they were fixed.

**Result:** the agent runs on Google Cloud Run, deployed by GitHub Actions with no
service-account key anywhere, and reachable only by authenticated callers.

> Project number and service URL are redacted as `<PROJECT_NUMBER>` / `<SERVICE_URL>`.
> Find your own with `gcloud projects describe PROJECT_ID --format='value(projectNumber)'`
> and `gcloud run services describe SERVICE --region REGION --format='value(status.url)'`.

| | |
|---|---|
| Project | `ai-assistant-vlad` (number `<PROJECT_NUMBER>`) |
| Region | `europe-central2` (Warsaw — nearest GCP region to the Kyiv VPS) |
| Service | `ai-assistant`, revision `ai-assistant-00002-f2z` |
| Repo | `github.com/vladislavtaran/ai-assistant-math-agent` |

---

## Phase 0 — SDK, project, billing

**What for:** nothing can be created until a project exists and has a billing account attached,
even for free-tier usage.

```bash
brew install --cask google-cloud-sdk
gcloud auth login
gcloud config set project ai-assistant-vlad
```

```bash
gcloud billing accounts list
```
Returned `Listed 0 items.` — no billing account existed yet.

Created one at <https://console.cloud.google.com/billing/create> (Individual, card required).
This activated the **$300 / 90-day free trial**, expiring 28 Dec 2026. Google does not auto-charge
when a trial ends — services pause until you explicitly upgrade.

```bash
gcloud billing projects link ai-assistant-vlad \
  --billing-account=$(gcloud billing accounts list --filter='open=true' --format='value(name)' --limit=1)
```

```bash
gcloud billing projects describe ai-assistant-vlad --format='value(billingEnabled)'
```
Wanted `True`.

> **Gotcha:** the trial signup silently created its own throwaway project
> (`project-55f9fe0a-4e7c-4689-bcb`). Billing had to be linked to *ours* explicitly.

---

## Phase 1 — Enable APIs

**What for:** each Google Cloud service is off by default. `iamcredentials` and `sts` are the two
people forget; without them Workload Identity Federation fails later with a confusing permissions error.

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com
```

> **Problem 1.** Run before billing was linked, this failed with
> `FAILED_PRECONDITION ... UREQ_PROJECT_BILLING_NOT_FOUND`.
> **Cause:** Phase 0's billing step had not been completed. **Fix:** link billing, re-run.

```bash
gcloud services list --enabled --format='value(config.name)' | sort
```

---

## Phase 2 — Artifact Registry

**What for:** somewhere to store the Docker image. The name and region are not arbitrary — the
workflow builds the image path from them, so a mismatch shows up much later as a `403` on push.

```bash
gcloud artifacts repositories create containers \
  --repository-format=docker --location=europe-central2 --description="Container images"
```

```bash
gcloud artifacts repositories list --location=europe-central2
```

---

## Phase 3 — Secret Manager

**What for:** the Gemini API key must not live in the repo, in the image, or in an env var in the
workflow file. Cloud Run mounts it at runtime from Secret Manager.

```bash
gcloud secrets create gemini-api-key --replication-policy=automatic
```

The name must be exactly `gemini-api-key` — the workflow reads
`--set-secrets GEMINI_API_KEY=gemini-api-key:latest`.

```bash
read -rs GEMINI_KEY && printf '%s' "$GEMINI_KEY" \
  | gcloud secrets versions add gemini-api-key --data-file=- \
  && unset GEMINI_KEY
```

**Why this form:** passing the key as a command argument would put it in shell history and in the
process table where any local user could read it. `read -rs` takes it from a silent prompt, pipes it
straight to gcloud, and unsets it.

```bash
gcloud secrets versions list gemini-api-key --format='value(name)'   # expect: 1
```

---

## Phase 4 — Two service accounts (least privilege)

**What for:** two separate identities so that a compromised CI pipeline cannot read secrets, and a
compromised container cannot redeploy itself.

### Runtime identity — what the container runs as

```bash
gcloud iam service-accounts create ai-assistant-run \
  --display-name="Cloud Run runtime (ai-assistant)"
```

```bash
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:ai-assistant-run@ai-assistant-vlad.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Note the scope: granted **on that one secret**, not project-wide. A second secret added later stays
invisible to this identity unless explicitly granted.

### Deploy identity — what GitHub Actions acts as

```bash
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"
```

```bash
gcloud projects add-iam-policy-binding ai-assistant-vlad \
  --member="serviceAccount:github-deployer@ai-assistant-vlad.iam.gserviceaccount.com" \
  --role="roles/run.admin"
```

```bash
gcloud projects add-iam-policy-binding ai-assistant-vlad \
  --member="serviceAccount:github-deployer@ai-assistant-vlad.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
```

**The binding people forget** — the deployer must be allowed to *assign* the runtime identity to the
service, or the deploy dies with `iam.serviceAccounts.actAs denied`:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  ai-assistant-run@ai-assistant-vlad.iam.gserviceaccount.com \
  --member="serviceAccount:github-deployer@ai-assistant-vlad.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

### Verification

```bash
gcloud iam service-accounts list --format='table(email,displayName)'
```

```bash
gcloud projects get-iam-policy ai-assistant-vlad \
  --flatten="bindings[].members" \
  --filter="bindings.members:github-deployer@ai-assistant-vlad.iam.gserviceaccount.com" \
  --format="value(bindings.role)"
```
Expected exactly `roles/artifactregistry.writer` and `roles/run.admin`.

```bash
gcloud iam service-accounts get-iam-policy \
  ai-assistant-run@ai-assistant-vlad.iam.gserviceaccount.com --format=yaml
```
Expected `roles/iam.serviceAccountUser` held by `github-deployer`.

> These two `add-iam-policy-binding` calls dump the entire project IAM policy — several screens of
> YAML. A failure scrolls past unnoticed, which is why the filtered verification above matters.

---

## Phase 5 — Workload Identity Federation (keyless CI)

**What for:** this is the step that removes long-lived credentials entirely. GitHub presents a
short-lived OIDC token; Google exchanges it for short-lived credentials. **No service-account JSON
key is created, stored or rotated, because none exists.**

```bash
gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner=='vladislavtaran'"
```

**`--attribute-condition` is the security control, not hygiene.** Google will let you create a
provider without it — and then *any* GitHub repository on the internet could request credentials
against the pool. This restricts it to repos owned by `vladislavtaran`.

```bash
gcloud iam service-accounts add-iam-policy-binding \
  github-deployer@ai-assistant-vlad.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/attribute.repository/vladislavtaran/ai-assistant-math-agent"
```

Two layers: the provider condition limits credentials to the *account*, this binding limits them to
one *repository*.

> **Uses the project NUMBER (`<PROJECT_NUMBER>`), not the ID.** Every other command uses the ID. Getting
> this wrong produces `unable to acquire impersonated credentials` on the first run, which looks like
> an auth bug rather than a typo.

```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global --workload-identity-pool=github \
  --format="yaml(name,attributeCondition,oidc.issuerUri)"
```

---

## Phase 6 — GitHub repository variables

**What for:** the workflow reads three `vars.*` to know what to authenticate against. None is
secret — that is the point of federation: there is no key to store.

```bash
unset GH_REPO GH_OWNER
cd /Users/vlad/new1/ai-assistant-math-agent
gh variable set GCP_PROJECT_ID  --body "ai-assistant-vlad"
gh variable set GCP_DEPLOY_SA   --body "github-deployer@ai-assistant-vlad.iam.gserviceaccount.com"
gh variable set GCP_WIF_PROVIDER --body "projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-provider"
gh variable list
```

> **Problem 2.** Every `gh` command failed with
> `expected the "[HOST/]OWNER/REPO" format, got "ai-assistant-math-agent"`.
> **Cause:** an earlier `export GH_REPO="ai-assistant-math-agent"` — `GH_REPO` is a **reserved
> environment variable for the `gh` CLI**, treated as a repo override and expecting `OWNER/REPO`.
> **Fix:** `unset GH_REPO GH_OWNER`, and use `REPO_OWNER` / `REPO_NAME` as variable names instead.

---

## Phase 7 — Two edits to the workflow

`.github/workflows/deploy-cloudrun.yml`:

1. `REGION: us-west1` → `REGION: europe-central2`
   The old value was a transatlantic hop from Kyiv *and* would not have matched the Artifact
   Registry, so the image push would have 403'd.

2. Added to the `gcloud run deploy` block:
   ```
   --service-account ai-assistant-run@${{ vars.GCP_PROJECT_ID }}.iam.gserviceaccount.com \
   ```
   Without this, Cloud Run runs the container as the **default compute service account**, which
   carries broad Editor permissions across the project.

---

## Phase 8 — Commit, push, and the first real run

The Dockerfile, `.dockerignore` and workflow had been untracked since 24 Aug 2026 — which is exactly
why the workflow had never executed.

```bash
git add Dockerfile .dockerignore .github/workflows/deploy-cloudrun.yml CLOUDRUN-RUNBOOK.md
git commit -m "Deploy the agent to Cloud Run via Workload Identity Federation"
git push
gh run watch
```

> **Problem 3 — the interesting one.** Run `36472542410` failed at the Deploy step:
> ```
> ERROR: (gcloud.run.deploy) argument --set-env-vars: Bad syntax for dict arg: [gemini-3.5-flash-lite]
> ```
> **Cause:** `--set-env-vars` uses commas to separate `KEY=VALUE` pairs, and the `GEMINI_MODELS`
> value *contains* commas. gcloud read `GEMINI_MODELS=gemini-2.5-flash-lite` as the first pair, then
> hit `gemini-3.5-flash-lite` with no `=`.
> **Fix:** the alternate-delimiter form, which tells gcloud to split on `:` instead:
> ```
> --set-env-vars "^:^GEMINI_MODELS=gemini-2.5-flash-lite,gemini-3.5-flash-lite,..."
> ```
> This bug had been latent in the file since August. **A workflow that exists but has never run hides
> bugs that only execution finds.**

**What the failed run already proved:** auth, Docker config, and build-and-push all succeeded —
so the federation, both service accounts, all four IAM bindings, the registry and the region were
all correct. Only the deploy argument was wrong.

Also fixed the git identity, which had been defaulting to a machine-local hostname address:

```bash
git config --global user.name "Vladyslav Taran"
git config --global user.email "vladislav.taran@gmail.com"
git commit --amend --reset-author --no-edit
git push --force-with-lease
```

| Run | Commit | Result |
|---|---|---|
| 36472542410 | `c641d65` | failure — `--set-env-vars` delimiter |
| 36473029203 | `9ef8f78` | **success** — revision 00001 |
| 36473164891 | `227226f` | **success** — revision 00002 (amended author) |

Two consecutive green runs: the pipeline is repeatable, not a one-off.

---

## Phase 9 — Verify

```bash
gcloud run services describe ai-assistant --region europe-central2 --format='value(status.url)'
```

```bash
curl -fsS https://<SERVICE_URL>/api/health
```

```json
{"ok": true, "configured": true, "model": "gemini-2.5-flash-lite",
 "tools": ["math_solver", "portfolio_search", "datetime", "network"]}
```

**`"configured": true` is the proof that matters** — it means the container read the Gemini key from
Secret Manager at runtime, i.e. the `secretAccessor` binding works end to end.

```bash
gcloud run services describe ai-assistant --region europe-central2 \
  --format='value(spec.template.spec.serviceAccountName)'
# ai-assistant-run@ai-assistant-vlad.iam.gserviceaccount.com  (not the default compute account)
```

---

## Phase 10 — Lock it down

**What for:** the deploy used `--allow-unauthenticated`, which grants `roles/run.invoker` to
`allUsers`. Anyone with the URL could call it, and every call spends Gemini API quota.
`--max-instances 3` caps Cloud Run compute but does nothing to bound LLM spend.

```bash
gcloud run services remove-iam-policy-binding ai-assistant --region europe-central2 \
  --member="allUsers" --role="roles/run.invoker"
```

Enforcement propagated after roughly 45 seconds — the endpoint returned 200 for a short window after
the IAM change, so **verify rather than assume**:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<SERVICE_URL>/api/health
# 403
```

```bash
curl -fsS -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://<SERVICE_URL>/api/health
# 200, healthy
```

The identity token is short-lived (1 hour) and identifies `vladislav.taran@gmail.com`. The URL was
never the security boundary — IAM is.

---

## Still open

1. **Budget alert** — not yet set. Billing → Budgets & alerts, a few dollars, 50/90/100%.
2. **Smoke-test step will now fail** on future deploys, because it curls `/api/health`
   unauthenticated. Fix:
   `curl -fsS -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/api/health"`
3. **Phase 11** — put Cloud Run behind chrome.net.ua as an nginx `upstream` with the local
   `127.0.0.1:8090` service as `backup`. Needs `proxy_ssl_server_name on` and an explicit `Host`
   header or Cloud Run returns 404. Also needs an auth decision: either the VPS mints ID tokens, or
   the service is re-opened and gated by a shared-secret header the app checks.
4. **Delete the throwaway trial project**: `gcloud projects delete project-55f9fe0a-4e7c-4689-bcb`
5. **Eval suite in CI** — 30–50 prompts with expected tool selections, failing the build on
   regression, plus tracing and cost-per-request. This layer, not the deploy itself, is what
   distinguishes "deployed an agent" from "operates agents in production".

---

## What can honestly be claimed

**Yes:** managed-cloud deployment (Cloud Run), keyless CI authentication via Workload Identity
Federation, least-privilege IAM, Secret Manager, Artifact Registry, containerisation, scale-to-zero,
a CI pipeline that builds, deploys and health-checks on every push.

**Not yet:** that it serves production user traffic (it is private; chrome.net.ua is still served by
the VPS), Kubernetes, Terraform/IaC, GKE, or evals and tracing in CI.

**Do not put the `*.run.app` URL on a CV** — it returns 403 to anyone clicking it, which reads as
broken rather than secure. Link chrome.net.ua.
