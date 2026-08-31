# CLAUDE.md

Context for Claude Code working in this repo.

> **This is a living document.** It is only useful while it is true. If you
> change something it describes, update it in the same commit. See
> [Keeping this current](#keeping-this-current) at the bottom for what counts.
>
> Last verified against a real deploy: **2026-08-31** (evening).

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

All writes are upserts, so any handle can be re-run from scratch. `POST
/wrapped/{handle}` short-circuits that: if the handle is already
`status='ready'`, it returns immediately without touching the pipeline at all
(pass `?refresh=true` to force a real re-run). This exists so a repeat demo of
the same handle is instant instead of a full re-run — see `CONTRACTS.md`.

`generate-slides` fires its five Bedrock calls **concurrently** (a thread pool,
bounded by `context.get_remaining_time_in_millis()`), not one at a time — see
the gotcha below. A Lambda timeout is a hard kill that `@step`'s except-block
never sees, so a step that might run long has to police its own wall-clock
budget and finish (successfully or with `status='error'`) before that timeout,
not rely on the exception path.

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
not bare foundation model ids, and the exact id matters -- a guessed one fails
as `ValidationException`, which reads like a bad request body rather than a bad
id. Get the real list, never guess:

```bash
aws bedrock list-inference-profiles --region us-west-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId,`anthropic`)].inferenceProfileId'
```

There are **three** independent gates, each with a different error and a
different fix. Clearing one surfaces the next, so "the form went through" does
not mean the model works:
- `ResourceNotFoundException: Model use case details have not been submitted`
  → self-serve form in the Bedrock console, ~15 min to propagate.
- `AccessDeniedException: ... not authorized to perform the required AWS
  Marketplace actions (aws-marketplace:Subscribe)` → the model needs a
  Marketplace subscription. **This is not an IAM problem** and adding
  permissions will not fix it -- it happens as the account *root*, which cannot
  be denied anything. Enable the model in the Bedrock console's Model access
  page, which performs the subscription.
- `AccessDeniedException: <model> is not available for this account`
  → account tier limit; needs AWS Sales.

As of 2026-08-31 in this account: `sonnet-4-6`, `sonnet-4-5` and `haiku-4-5`
work. `opus-4-6` is stuck on the Marketplace gate. `opus-5`, `opus-4-7` and
`opus-4-8` are tier-limited. The default is therefore
`us.anthropic.claude-sonnet-4-6`. To move once opus-4-6 clears:

```bash
python scripts/deploy.py --only app --bedrock-model us.anthropic.claude-opus-4-6-v1
```

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

**Amplify's git integration is deliberately NOT used.** The repo is owned by
another personal account (`mosspaul`), and connecting a repository -- via the
GitHub App or a token with `admin:repo_hook` -- requires repository admin.
Personal-account repos have only two levels, owner and write-collaborator, so
there is no admin to grant. The site is published by pushing a build artifact
to Amplify's manual deployment API (`scripts/deploy-web.py`), which needs no
GitHub access at all. An Amplify branch does not require a repository behind
it. `03-web.yaml` keeps the token path behind a condition for if the repo ever
moves to an org.

**With manual deployment, Amplify's environment variables do nothing.** They
only apply to builds Amplify runs from a connected repo. We build the bundle,
and Vite inlines env vars at build time -- so `deploy-web.py` injects
`VITE_API_BASE` from the app stack's output and then greps the built bundle to
confirm it landed. Without that, the site deploys, loads fine, and sends every
request to `undefined/wrapped/...`.

**GitHub Actions variables/secrets also need repo admin**, so the OIDC role ARN
is hardcoded in the workflow. That is safe: a role ARN is not a credential, and
the trust policy only accepts a token whose `sub` is exactly
`repo:mosspaul/github_wrapped:ref:refs/heads/main`.

**The GitHub OIDC provider is account-global.** One already existed in this
account, so `04-access` is deployed with `--oidc-provider-arn` to reuse it;
creating a second fails with `AlreadyExists`.

**GitHub sends immutable OIDC subject claims.** The `sub` is not the documented
`repo:owner/name:ref:refs/heads/main`. It carries numeric ids:

    repo:mosspaul@97133779/github_wrapped@1352708903:ref:refs/heads/main

The ids are the point -- a renamed or re-registered repo keeps its name but
never its id, so trust cannot be inherited by a look-alike. A trust policy
written the documented way fails `StringEquals` and every run dies with
`Not authorized to perform sts:AssumeRoleWithWebIdentity`.

That message covers *every* possible mismatch and STS will not say which one,
because naming it would let a stranger enumerate your roles. **Do not reason
forward from the template about what GitHub should be sending -- read what it
actually sent.** Every rejected attempt is in CloudTrail Event history (90 days,
no trail required) with the real claim in `userIdentity.userName`:

```bash
python scripts/diagnose-oidc.py     # does exactly this, and names the fix
```

`04-access.yaml` trusts **both** forms, as two exact strings -- never a
wildcard, which would let any repo on GitHub assume the role. Both are pinned
to this repo and branch; the second is a safety net in case GitHub rolls the
change back. `GithubOwnerId`/`GithubRepoId` come from the GitHub API and must
be updated if the repo ever moves.

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

**A Lambda timeout can orphan a job forever, and adaptive thinking made it
easy to hit one.** `generate-slides` used to call Bedrock for its five slides
one at a time inside a single 300s Lambda invocation. In production, one slide
spent its entire `max_tokens` budget on thinking and returned zero output text
(`output_tokens: 16000, thinking_tokens: 16000` — nothing left for the actual
answer); by the time a later slide was mid-call the function hit its own 300s
timeout and was hard-killed. That's not a raised exception, so `@step`'s
except-block never ran, and the job sat at `status='generating'` forever with
nothing to retry it. Fix was three-part, all still true and all load-bearing:
`shared/ai.py` sets `"output_config": {"effort": "low"}` so one slide can't
burn its whole budget on thinking (effort is GA on Bedrock, confirmed via
`shared/platform-availability.md` in the claude-api skill — don't reach for
the deprecated `budget_tokens` escape hatch instead); `generate-slides`
fires all five calls concurrently via a thread pool bounded by
`context.get_remaining_time_in_millis()` so total wall-clock is ~1x one call's
latency instead of 5x, and gives up on (not blocks on) whatever hasn't
returned by its own deadline; `GenerateSlidesFn`'s timeout went 300s → 600s and
Bedrock retries went `max_attempts: 3` → `2`, so one call's worst case
(read_timeout × attempts) still fits under the Lambda timeout with room for the
DB writes after. Verified: a real `octocat` run after all three landed —
22.9s for `generate-slides`, 5/5 slides, thinking tokens 19–29 (was 16000).

**The AWS CLI's configured default region is not this project's region.**
`aws configure get region` on this machine returns `us-east-1`; this project
is `us-west-1` (see Stack, above). `scripts/deploy.py` and `push-fn.py` only
use `us-west-1` if you pass `--region us-west-1` — without it they fail with
`ValidationError: Stack with id gh-wrapped-<stage>-bootstrap does not exist`,
which reads like a stack that was never deployed rather than a region miss.
Always pass `--region us-west-1` explicitly, or `aws configure set region
us-west-1` once for this machine.

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

**The full pipeline runs green, and reliably fast now.** All six functions
verified against real data: `octocat` goes pending -> ingesting -> computing ->
generating -> ready and returns five slides with model-authored HTML (5-9KB
each, no `<script>`), ordered by `slide-types.json` rather than by what the
database happened to return. Bedrock logs real token usage against
`us.anthropic.claude-sonnet-4-6`. A cold run finishes in well under a minute
(`generate-slides` itself: 22.9s, 5/5 slides) since slide generation went
concurrent and effort-capped; a repeat run of an already-`ready` handle
returns in a couple seconds via the `POST /wrapped/{handle}` fast path. See
the "Lambda timeout can orphan a job" gotcha above for what this replaced.

All five stacks are deployed. The site is live at
`https://main.d2uskcsztnslls.amplifyapp.com`, serving the real API URL and with
the SPA rewrite working. `04-access` provides the developer group and the
deploy role `arn:aws:iam::802133075723:role/gh-wrapped-dev-deploy`.

One collaborator exists (`derk5`) in `gh-wrapped-dev-developers`. Add more with
`scripts/new-dev-creds.py <name>`.

Still unproven: the GitHub Actions workflow. The OIDC trust policy has been
fixed and verified against the claim GitHub actually sends, but no run has
succeeded yet.

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
