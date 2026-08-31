# GitHub Wrapped

Spotify Wrapped, but for a GitHub account. Enter a handle, and the app pulls
that user's public data from GitHub, computes a fixed set of stats, asks Claude
to design a slide for each one, and plays the result back as a deck.

This repo currently contains **the scaffolding**: infrastructure, deployment
scripts, and a working stub for every Lambda. The pipeline runs end to end
today; most of the actual logic is still to be written, and the stubs mark where.

> **New here? Read [ONBOARDING.md](ONBOARDING.md)** — what to run and where your
> code goes, in about 10 minutes. This README is the reference for whoever owns
> the infrastructure. [CLAUDE.md](CLAUDE.md) carries the architectural decisions
> and the gotchas that have already cost someone an afternoon.

---

## Architecture

```
        browser
           |
     Amplify Hosting  (web/, Vite + React)
           |
     API Gateway HTTP API
           |
  +--------+---------+---------------+
  |        |         |               |
start-  get-      get-wrapped        |   TypeScript, Node 22
ingest  status                       |   (web <-> database only)
  |                                  |
  | async invoke                     |
  v                                  |
ingest-github --> compute-stats --> generate-slides    Python 3.12
  |                   |                   |            (APIs + logic)
  | GitHub REST       |                   | Bedrock (Claude)
  |                   |                   |
  +-------------------+-------------------+
                      |
              Aurora Serverless v2 MySQL
              (via the RDS Data API)
```

**The Data API is the key architectural decision.** Aurora has to live in a VPC,
but a VPC-attached Lambda loses default internet access — which would mean a
~$32/mo NAT Gateway just so the Python functions could still reach GitHub and
Bedrock. The RDS Data API turns database access into an ordinary IAM-signed
HTTPS call, so **no Lambda is attached to the VPC at all**. Nothing opens a
MySQL socket; there are no security-group rules to manage and no connection
pooling to get wrong.

If you ever find yourself turning `EnableHttpEndpoint` off, the whole design
changes. Don't.

---

## First-time setup (account owner)

You need the AWS CLI, Node 22, and Python 3.11+.

```bash
python -m pip install -r requirements-dev.txt   # boto3 for scripts/ and db/migrate.py
```

```bash
aws sso login                     # this repo's default profile is SSO
aws sts get-caller-identity       # confirm you're in the right account
```

1. **Bedrock model access** — one-time, manual, and there are *three separate
   gates*. Each has a different error and a different fix, and clearing one
   just surfaces the next:

   - `ResourceNotFoundException: Model use case details have not been
     submitted` → fill in the Anthropic use case form in the Bedrock console
     under **Model access**. Self-serve; takes ~15 min to take effect.
   - `AccessDeniedException: ... AWS Marketplace actions
     (aws-marketplace:Subscribe)` → the model needs a Marketplace subscription.
     Not an IAM problem — it happens as account root. Enable the model on the
     Bedrock console's **Model access** page, which subscribes it.
   - `AccessDeniedException: <model> is not available for this account` → the
     account tier doesn't include that model; contact AWS Sales.

   Models must be referenced by **inference profile id** (`us.anthropic....`),
   not the bare foundation model id, and a guessed id fails as a confusing
   `ValidationException`. List the real ones:

   ```bash
   aws bedrock list-inference-profiles --region us-west-1 \
     --query 'inferenceProfileSummaries[?contains(inferenceProfileId,`anthropic`)].inferenceProfileId'
   ```

   Working in this account today: `sonnet-4-6` (the default), `sonnet-4-5`,
   `haiku-4-5`. Blocked: `opus-4-6` (Marketplace), `opus-5`/`4-7`/`4-8` (tier).

   To change the model on an already-deployed stack:
   ```bash
   python scripts/deploy.py --only app --bedrock-model us.anthropic.claude-opus-5
   ```
   Editing the `Default:` in `02-app.yaml` is **not** enough — CloudFormation
   reuses the value a parameter was last deployed with.

2. **Create one GitHub PAT** — classic, `public_repo` scope. This is what
   `ingest-github` uses to read public GitHub data at 5000 req/hr instead of 60.
   Amplify does **not** need a token; see step 6.

3. **Deploy:**
   ```bash
   python scripts/deploy.py --stage dev --github-token ghp_YOUR_AMPLIFY_TOKEN
   ```
   The first run takes ~15 minutes, almost all of it waiting for Aurora.
   Later runs are ~2 minutes.

4. **Store the data PAT:**
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id gh-wrapped/dev/github-pat \
     --secret-string '{"token":"ghp_YOUR_DATA_TOKEN"}'
   ```

5. **Set up collaborator access** (see [Collaborators](#collaborators) below).

6. **Connect the repo to Amplify** — once, in the console. Open the
   `AmplifyConsoleUrl` from the web stack outputs, choose **Connect branch** →
   GitHub, authorize, and pick `mosspaul/github_wrapped` / `main`.

   This installs the Amplify GitHub App scoped to just this repo, so no token
   is created or stored anywhere. The build spec, SPA rewrite rule, and
   `VITE_API_BASE` are already set by CloudFormation — don't re-enter them.
   After this, every push to `main` rebuilds the front end automatically.

---

## Daily loop

**Changed a function's code?** This is what you want — about 10 seconds.

```bash
python scripts/push-fn.py compute-stats
aws logs tail /aws/lambda/gh-wrapped-dev-compute-stats --follow
```

> On Windows, run `aws logs tail` from **PowerShell**, not Git Bash. Git Bash
> rewrites the leading `/aws/lambda/...` into a Windows path and the command
> fails with an unhelpful `logGroupName` validation error.

**Changed the schema?** Edit `db/schema.sql`, then:

```bash
python db/migrate.py            # add --dry-run to see the statements first
```

**Changed anything in `infra/`** (env vars, timeouts, IAM, routes)? Only then do
you need CloudFormation:

```bash
python scripts/deploy.py --only app
```

**Front end:**

```bash
cd web
cp .env.example .env.local      # paste in the ApiEndpoint output
npm install && npm run dev
```

---

## Testing it end to end

```bash
API=$(aws cloudformation describe-stacks --stack-name gh-wrapped-dev-app \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)

curl -X POST "$API/wrapped/octocat"          # 202, starts the run
curl "$API/wrapped/octocat/status"           # poll: pending -> ... -> ready
curl "$API/wrapped/octocat" | python -m json.tool
```

Status moves `pending → ingesting → computing → generating → ready`. If it
stops, the status names the Lambda to go read logs for. `error` is terminal and
carries a message.

Straight to the database, no MySQL client needed:

```bash
aws rds-data execute-statement \
  --resource-arn $(aws cloudformation describe-stacks --stack-name gh-wrapped-dev-data \
      --query "Stacks[0].Outputs[?OutputKey=='DbClusterArn'].OutputValue" --output text) \
  --secret-arn $(aws cloudformation describe-stacks --stack-name gh-wrapped-dev-data \
      --query "Stacks[0].Outputs[?OutputKey=='DbSecretArn'].OutputValue" --output text) \
  --database gh_wrapped --sql "SELECT handle, status FROM wrapped_jobs" \
  --format-records-as JSON
```

Or use the **RDS Query Editor** in the console — it works because the Data API
is enabled.

---

## Collaborators

Everyone works against **one shared `dev` stack**. You get your own IAM user in
the owner's AWS account; nobody shares credentials.

**Owner**, once:
```bash
python scripts/deploy.py --only access --stage dev
python scripts/new-dev-creds.py alice
```
That prints an access key and a console password **once**. Send it privately —
not in the repo, not in a group chat.

**Collaborator**, once:
```bash
aws configure --profile gh-wrapped
export AWS_PROFILE=gh-wrapped         # $env:AWS_PROFILE = "gh-wrapped" on PowerShell
aws sts get-caller-identity
```

You can update Lambda code, read logs, run migrations, and query the database.
You **cannot** delete stacks, modify the Aurora cluster, or change IAM — those
are explicitly denied in `infra/04-access.yaml`, so a leaked key can't take the
demo down mid-judging.

Pushes to `main` also deploy automatically via GitHub Actions (OIDC, no stored
keys). That's a safety net, not the inner loop — don't wait on CI to see a change.

---

## Where to start

| You want to work on | Go to |
|---|---|
| Fetching languages and commit history | `lambdas/py/ingest-github/handler.py` — see the two EXTENSION POINT comments |
| Making the stats interesting | `lambdas/py/compute-stats/handler.py` — three builders return `placeholder: True` |
| Slide design / prompting | `lambdas/py/generate-slides/handler.py` — the `SYSTEM` prompt |
| The actual slide deck UI | `web/src/App.tsx` is a throwaway; `web/src/api.ts` is the real client |
| API shapes | `lambdas/ts/` |
| Adding a slide type | `shared/slide-types.json`, then a builder in `compute-stats` |

**Read [`shared/CONTRACTS.md`](shared/CONTRACTS.md) first.** It's the agreement
between the front end, the API, and the pipeline — if you change something in
it, tell everyone, because someone is coding against it.

---

## Layout

```
infra/       CloudFormation: 00 bootstrap, 01 data, 02 app, 03 web, 04 access
db/          schema.sql (edit freely) + migrate.py
lambdas/ts/  API handlers -- web <-> database
lambdas/py/  Pipeline steps -- GitHub, stats, Bedrock
web/         Vite + React front end
shared/      slide-types.json + CONTRACTS.md
scripts/     build.py, deploy.py, push-fn.py, new-dev-creds.py
```

Python dependencies are **per function** (`lambdas/py/<fn>/requirements.txt`).
`compute-stats` needs nothing beyond the runtime's boto3 and bundles at 33KB;
putting `anthropic` in a shared file made it 46MB for no reason. `build.py`
fetches Linux wheels explicitly (`--platform manylinux2014_x86_64`), because
plain `pip install -t` on Windows or macOS produces binaries Lambda can't load.

---

## Cost and teardown

Aurora at 0.5 minimum ACU is the only meaningful ongoing cost: roughly
**$45/month if left running**, a few dollars over a hackathon weekend. Everything
else is effectively free at this scale.

Set `MinAcu=0` to let the cluster scale to zero when idle — near-$0, at the cost
of a ~15s resume on the first query after a pause. Good after the event, risky
during a live demo.

```bash
# after the hackathon
aws iam delete-access-key --user-name alice --access-key-id AKIA...
for s in access web app data bootstrap; do
  aws cloudformation delete-stack --stack-name gh-wrapped-dev-$s
done
```

The artifacts bucket is `Retain`, so delete it by hand if you want it gone.

---

## Known gaps

Deliberately not built yet: end-user auth, rate limiting, caching or TTL on
GitHub data, Step Functions in place of the async-invoke chain, a prod stage,
alarms, and cost budgets. CORS is wide open (`*`) because this is a demo.
