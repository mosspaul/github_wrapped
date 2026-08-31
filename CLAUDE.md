# CLAUDE.md

Context for Claude Code working in this repo.

> **This is a living document.** It is only useful while it is true. If you
> change something it describes, update it in the same commit. See
> [Keeping this current](#keeping-this-current) at the bottom for what counts.
>
> Last verified against a real deploy: **2026-08-31**.

## What this is

A hackathon demo: "Spotify Wrapped for GitHub". A user enters a GitHub handle;
we pull their public data, compute a fixed set of stats, ask Claude to design an
HTML slide for each, and play it back as a deck.

Several people work on this at once. Most Lambdas are **working stubs**, not
finished logic — they return plausible data so the whole pipeline runs end to
end while people fill in the pieces independently.

## Stack

- **IaC:** raw CloudFormation. No SAM, no CDK. Five stacks: `00-bootstrap`,
  `01-data`, `02-app`, `03-web`, `04-access`.
- **Database:** Aurora Serverless v2 MySQL, reached *only* through the RDS Data
  API.
- **Lambdas:** TypeScript (Node 22) for the API handlers, Python 3.12 for the
  pipeline.
- **AI:** Claude on Bedrock, via boto3.
- **Front end:** Vite + React + TypeScript on Amplify Hosting.
- **Region:** `us-west-1`. Account `802133075723`.

## The one architectural decision that matters

**No Lambda is attached to the VPC.** Aurora must live in a VPC, but a
VPC-attached Lambda loses default internet egress — which would force a ~$32/mo
NAT Gateway just so the Python functions could reach api.github.com and Bedrock.
The RDS Data API makes database access an ordinary IAM-signed HTTPS call, so the
functions stay outside the VPC entirely.

Consequences to preserve:
- `EnableHttpEndpoint: true` on the cluster is load-bearing. Turning it off
  breaks the whole design, not just one feature.
- The security group has **no ingress rules** and that is correct.
- Never add `VpcConfig` to a Lambda without re-solving the egress problem.
- Data API callers need `secretsmanager:GetSecretValue` **in addition to**
  `rds-data:*`. Omitting it produces an `AccessDeniedException` that looks like
  an rds-data problem and wastes a lot of time.

## Layout

```
infra/       CloudFormation templates
db/          schema.sql (edit freely) + migrate.py
lambdas/ts/  api-start-ingest, api-get-status, api-get-wrapped, shared/
lambdas/py/  ingest-github, compute-stats, generate-slides, shared/
web/         front end
shared/      slide-types.json + CONTRACTS.md
scripts/     build.py, deploy.py, push-fn.py, new-dev-creds.py, common.py
```

`shared/CONTRACTS.md` is the API/DB contract between the front end, the API
handlers, and the pipeline. Treat it as authoritative and update it when shapes
change.

## How the pipeline works

`api-start-ingest` async-invokes `ingest-github` → `compute-stats` →
`generate-slides`. Each step writes its phase to `wrapped_jobs` before handing
off, which is how the front end shows progress. Because these are async
invocations, **a raised exception goes nowhere a user can see** — writing
`status='error'` is the only way a failure surfaces. `shared/pipeline.py`'s
`@step` decorator handles that; use it rather than hand-rolling try/except.

All writes are upserts, so any handle can be re-run from scratch.

## Commands

```bash
python scripts/push-fn.py <function>     # one Lambda, ~10s -- the inner loop
python db/migrate.py                     # apply db/schema.sql
python scripts/deploy.py --only app      # only when infra/ changed
python scripts/build.py                  # bundle everything
```

`push-fn.py` updates **code only**. Env vars, timeouts, IAM, and routes need
`deploy.py`.

## Gotchas that have already cost time

Each of these was hit for real. Don't rediscover them.

**The `anthropic` SDK cannot make HTTP requests from this Lambda runtime.**
Every call failed with `ConnectError(OSError(16, 'Device or resource busy'))`
from httpx, while the identical request through botocore succeeded — so it is
the HTTP client, not the network, credentials, or endpoint. `shared/ai.py` uses
boto3 `bedrock-runtime` instead. This also dropped the bundle from 46MB to 9KB.
If you want the SDK back, first prove a plain httpx request works from inside a
deployed Lambda.

**Bedrock model ids must be inference profiles** (`us.anthropic.claude-...`),
not bare foundation model ids. Two distinct failures, with different fixes:
- `ResourceNotFoundException: Model use case details have not been submitted`
  → self-serve form in the Bedrock console, ~15 min to propagate.
- `AccessDeniedException: <model> is not available for this account`
  → account tier limit; needs AWS Sales. This account currently gets this for
  `opus-5` and `opus-4-8`, which is why the default is
  `us.anthropic.claude-opus-4-6-v1`.

**Changing a `Default:` in a template does not change a deployed stack.**
`cloudformation deploy` reuses the value a parameter was last deployed with.
Pass it explicitly (`deploy.py --bedrock-model ...`). This is also why
`--github-token` is omitted rather than passed empty on web redeploys.

**A YAML folded block (`>`) appends a trailing newline.** EC2 rejects newlines
in security group descriptions; this killed the first data-stack deploy. Use a
plain single-line string for anything with a restricted charset.

**`AWS::Amplify::App` cannot take an `IAMServiceRoleArn` pointing at a role in
the same stack.** Amplify's Early Validation hook checks the role exists and is
assumable *before* CloudFormation creates it, so the changeset fails with a
bare `AWS::EarlyValidation::PropertyValidation` and no detail in stack events.
A static SPA does not need the role; it is omitted. Diagnosed by bisecting a
minimal template property by property, which is the only practical way to find
an early-validation failure.

**Amplify needs no GitHub token.** The repo is connected once in the console,
which installs the Amplify GitHub App scoped to this repo alone. `03-web.yaml`
still supports the token path behind a condition, but it requires a *classic*
`ghp_` token with `repo` scope -- fine-grained tokens are silently rejected by
Amplify, and `repo` grants read/write to every repo the owner has.

**Aurora engine versions differ by region.** The Data API needs 3.07+, but
us-west-1 offers 3.08.0–3.13.0 and *no* 3.07.x. Pinned to the regional default
`8.0.mysql_aurora.3.10.3`. Check with `aws rds describe-db-engine-versions
--engine aurora-mysql --region <r>` before changing regions.

**Python dependencies are per function** (`lambdas/py/<fn>/requirements.txt`).
A shared file made `compute-stats` 46MB by pulling in `anthropic`'s boto3 for a
function that never calls Claude. `boto3` is in the runtime — never vendor it.

**`build.py` must fetch Linux wheels** (`--platform manylinux2014_x86_64
--only-binary=:all:`). Plain `pip install -t` on Windows or macOS produces
binaries Lambda cannot load. A local import failure of `pydantic_core` is the
build working correctly.

**Windows:** Git Bash rewrites `/aws/lambda/...` into a Windows path, so run
`aws logs tail` from PowerShell. And `build/` can be locked by a shell that is
`cd`'d into it; `clean()` retries and then says so.

**The account's root `login_session` credential provider refreshes
unreliably** — long-polling calls (CloudFormation waiters) fail intermittently
with `CreateOAuth2Token ... authorization grant is invalid`. It has silently
corrupted test results before. If AWS results look self-contradictory, re-run
`aws sts get-caller-identity` and repeat the test before believing them.
`botocore[crt]` in `requirements-dev.txt` is required by that provider.

## Conventions

- Named SQL parameters always (`:handle`), never string interpolation.
- Slide ids live in `shared/slide-types.json` and nowhere else. Both languages
  read it. A slide without a builder in `compute-stats` is a loud `KeyError`.
- One IAM role per Lambda, least privilege.
- Every `db/schema.sql` statement must be idempotent. `IF NOT EXISTS` skips a
  table that already exists, so **changing** a column needs an appended
  `ALTER TABLE`, not an edit to the `CREATE`.
- The `html` column holds model-authored fragments rendered with
  `dangerouslySetInnerHTML`. That is acceptable only because the content comes
  from our own prompt. Never route user-supplied HTML through it.

## Current state

Deployed and working in us-west-1: bootstrap, data (6 tables), app (6 Lambdas +
HTTP API at `https://ap4n9q6iei.execute-api.us-west-1.amazonaws.com`).

Five of six functions verified against real data. `generate-slides` is blocked
only on the Bedrock use-case form.

`03-web` is deployed: Amplify app `d2uskcsztnslls`, build spec / SPA rewrite /
`VITE_API_BASE` all set, awaiting a one-time console repo connection.

Not yet deployed: `04-access` (collaborator IAM + GitHub OIDC).

Deliberately not built: end-user auth, rate limiting, caching/TTL on GitHub
data, Step Functions, prod stage, alarms, cost budgets. CORS is `*`.

## Keeping this current

Update this file in the same commit when you:

- change the pipeline shape, a stack, or the database access path
- hit a new gotcha that cost more than ~15 minutes
- change the Bedrock model, region, or account
- deploy a stack listed above as not deployed, or finish a stub
- change anything in `shared/CONTRACTS.md`

When you do, refresh the "Last verified" date at the top. If something here
turns out to be wrong, fix it rather than working around it — a stale line here
is worse than no line, because it gets trusted.
