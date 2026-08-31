# GitHub Wrapped — Contracts

The agreement between the front end, the API Lambdas, and the pipeline Lambdas.
**If you change something here, say so in chat** — someone else is coding against it.

## Slide types

Defined once in [`shared/slide-types.json`](./slide-types.json). Both
`lambdas/ts/shared/types.ts` and `lambdas/py/shared/slides.py` load that file at
build time. Adding a slide = add an entry there + a builder in `compute-stats`.

Current ids: `commit_activity`, `languages`, `coding_personality`,
`year_in_code`, `standout_projects`.

## HTTP API

Base URL is the `ApiEndpoint` output of the `gh-wrapped-<stage>-app` stack.
CORS is wide open (`*`) because this is a demo. Do not copy that to anything real.

### `POST /wrapped/{handle}`
Starts a wrapped run. Returns immediately; the work happens async.

If this handle already finished (`status = 'ready'`), this **skips the run**
and returns the ready status directly instead of re-fetching from GitHub and
re-generating slides — a repeat request for a finished handle is meant to be
near-instant. Pass `?refresh=true` to force a real re-run regardless of
current status (this is also what a failed/stale handle gets automatically —
only `ready` short-circuits).

```
202 { "handle": "octocat", "status": "pending" }   // fresh run started
202 { "handle": "octocat", "status": "ready" }     // already done, no run started
400 { "error": "invalid handle" }
```

Handles must match `^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$` — GitHub's own rule.

### `GET /wrapped/{handle}/status`
Poll this every ~2s while a run is in flight.

```
200 { "handle": "octocat", "status": "computing", "error": null,
      "updatedAt": "2026-08-31T18:04:22.000Z" }
404 { "error": "no run for handle" }
```

Status state machine — strictly forward, except `error`, which is terminal from anywhere:

```
pending -> ingesting -> computing -> generating -> ready
                \___________|___________|______ error
```

### `GET /wrapped/{handle}`
The payload the slide deck renders.

```jsonc
200 {
  "user": {
    "handle": "octocat",
    "displayName": "The Octocat",
    "profileImageUrl": "https://...",
    "bio": "...",
    "followers": 1234,
    "publicRepos": 8,
    "accountCreatedAt": "2011-01-25T18:44:36.000Z"
  },
  "slides": [
    {
      "slideType": "languages",
      "title": "Languages",
      "stats": { "top": [{ "language": "Python", "bytes": 91234 }] },
      "html": "<section class=\"...\">...</section>",
      "generatedAt": "2026-08-31T18:05:01.000Z"
    }
  ]
}
404 { "error": "not ready" }   // also returned while status != ready
```

`slides` comes back in `slide-types.json` order, not database order.

## The `html` field

`generate-slides` asks Claude for a **self-contained fragment**:

- One root element, no `<html>`/`<head>/<body>`
- All styling inline or in a scoped `<style>` inside the fragment
- No `<script>`, no external URLs, no network requests
- Must render legibly at 400x700 (portrait, phone-sized)

The front end mounts it with `dangerouslySetInnerHTML`. That is only acceptable
because the content comes from our own Bedrock call with our own prompt — never
put user-supplied HTML through that path.

## Pipeline contract

Each Lambda is invoked async with `{"handle": "octocat"}` and is responsible for:

1. Setting status to its own phase on entry (`db.set_status`)
2. Doing its work
3. Invoking `NEXT_FN` (env var) with the same payload

On exception, set status `error` with the message and **do not** invoke the next
function. Every phase is safe to re-run from scratch — writes are upserts.

## Database

Schema lives in [`db/schema.sql`](../db/schema.sql). Edit it, then run
`python db/migrate.py`. It is all `CREATE TABLE IF NOT EXISTS`, so adding a table
is safe; **changing an existing column needs an explicit `ALTER TABLE`** appended
to the file, because `IF NOT EXISTS` will silently skip a table that already
exists. Keep those ALTERs idempotent or drop the dev tables and re-migrate.

Access is via the RDS Data API — no MySQL socket, no VPC. Use `sql()` from
`lambdas/ts/shared/dataApi.ts` or `lambdas/py/shared/db.py` rather than calling
`rds-data` directly.
