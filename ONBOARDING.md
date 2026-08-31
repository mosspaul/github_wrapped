# Onboarding

Everything you need to start. Should take about 10 minutes.

## 1. Set up (once)

```bash
git clone git@github.com:mosspaul/github_wrapped.git
cd github_wrapped

python -m pip install -r requirements-dev.txt
aws configure --profile gh-wrapped        # use the keys Caleb sent you
export AWS_PROFILE=gh-wrapped             # PowerShell: $env:AWS_PROFILE = "gh-wrapped"
export AWS_REGION=us-west-1               # PowerShell: $env:AWS_REGION = "us-west-1"

aws sts get-caller-identity               # should print your username
```

We all share **one `dev` stack** in Caleb's AWS account. You don't deploy
infrastructure — you push code into Lambdas that already exist.

## 2. Where your code goes

| If you're working on | Edit this |
|---|---|
| Pulling data from GitHub | `lambdas/py/ingest-github/handler.py` |
| Turning data into stats | `lambdas/py/compute-stats/handler.py` |
| Prompting Claude for slide HTML | `lambdas/py/generate-slides/handler.py` |
| The API the front end calls | `lambdas/ts/api-*/index.ts` |
| The slide deck UI | `web/src/` |
| Database tables | `db/schema.sql` |
| Adding a new slide type | `shared/slide-types.json` **and** a builder in `compute-stats` |

Shared helpers live in `lambdas/py/shared/` and `lambdas/ts/shared/`. Use
`sql()` from there instead of talking to the database yourself.

## 3. The three commands you'll actually use

**Changed a Lambda?** Push just that one — takes about 10 seconds.

```bash
python scripts/push-fn.py compute-stats
```

Valid names: `ingest-github`, `compute-stats`, `generate-slides`,
`api-start-ingest`, `api-get-status`, `api-get-wrapped`.

**Changed `db/schema.sql`?**

```bash
python db/migrate.py
```

Safe to run repeatedly. Add `--dry-run` to preview.

**Watch what your function did:**

```bash
aws logs tail /aws/lambda/gh-wrapped-dev-compute-stats --follow
```

> **Windows:** run `aws logs tail` in **PowerShell**, not Git Bash. Git Bash
> mangles the `/aws/lambda/...` path and you get a confusing
> `logGroupName` validation error.

## 4. Try it

```bash
API=https://ap4n9q6iei.execute-api.us-west-1.amazonaws.com

curl -X POST $API/wrapped/octocat        # start a run
curl $API/wrapped/octocat/status         # poll this
curl $API/wrapped/octocat                # the finished payload
```

Status goes `pending → ingesting → computing → generating → ready`.
If it stops somewhere, **that status names the Lambda to go read logs for**.
`error` is terminal and carries the message.

The deployed site: **https://main.d2uskcsztnslls.amplifyapp.com**

Front end locally, against the real API:

```bash
cd web
cp .env.example .env.local    # paste the API URL above into it
npm install && npm run dev
```

Pushing the front end live (also happens automatically on push to `main`):

```bash
python scripts/deploy-web.py
```

## 5. Look at the database

No MySQL client needed — we use the RDS Data API.

Easiest is the **RDS Query Editor** in the AWS console (sign in with the
console password Caleb sent you, pick the `gh-wrapped-dev` cluster).

## Rules of the road

- **Read [`shared/CONTRACTS.md`](shared/CONTRACTS.md).** It's the agreement
  between the front end, the API, and the pipeline. If you change something in
  it, tell everyone — someone is coding against it.
- **Don't run `scripts/deploy.py`.** That's infrastructure and it's Caleb's.
  `push-fn.py` is your tool. You'll get an `AccessDenied` if you try anyway.
- **You can't break anything permanently.** Deleting stacks, modifying the
  database cluster, and changing IAM are explicitly denied for your account.
- Everyone shares one database. If you need a clean slate, re-run a handle —
  all writes are upserts.

## When something breaks

| Symptom | Cause |
|---|---|
| `AccessDeniedException` on `rds-data` | usually a missing secret permission, not the SQL — ask Caleb |
| Status stuck on one phase | that Lambda threw; check its logs |
| `Model use case details have not been submitted` | Bedrock model access, account-level — ask Caleb |
| `ModuleNotFoundError` after adding a package | add it to `lambdas/py/<fn>/requirements.txt`, then `push-fn.py` again |
| Build fails deleting `build/` on Windows | a terminal is `cd`'d into it; close it |

Stuck for more than 15 minutes? Ask. It's a hackathon.
