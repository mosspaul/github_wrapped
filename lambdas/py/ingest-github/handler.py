"""
Pipeline step 1: pull raw data from GitHub into Aurora.

Fetches the profile + repos (capped at MAX_REPOS, most recently pushed first),
the per-repo language byte counts, and a year of per-day commit counts, and
writes all four to Aurora.

This step derives nothing. Anything computed here would have to be written to a
table to survive anyway -- steps are invoked async and hand off only
{"handle": ...}, so a return value goes nowhere -- and `compute-stats` is where
raw rows become slide stats. Keep this function about fetching and storing.
"""

import itertools
import time
from datetime import datetime, timedelta, timezone

from shared import db, github
from shared.pipeline import step

MAX_REPOS = 15  # keeps DB writes cheap and bounds the two per-repo passes
                # below, each of which costs one GitHub call per non-fork repo.

# /stats/ endpoints answer 202 while GitHub builds the cache, so the first run
# for a cold repo gets nothing and has to ask again. Bounded twice over: by
# rounds, so a repo stuck on 202 cannot spend the rate limit budget in a loop,
# and by the Lambda's own clock, because a timeout is a hard kill that @step's
# except-block never sees and would strand the job at status='ingesting'.
STATS_MAX_ROUNDS = 4
STATS_RETRY_DELAY_S = 2.0
STATS_WRAP_UP_BUFFER_MS = 45_000


@step("ingesting")
def handler(handle: str, context) -> dict:
    profile = github.get(f"/users/{handle}")

    db.sql(
        """
        INSERT INTO users (handle, display_name, profile_image_url, bio,
                           followers, public_repos, account_created_at, raw_json)
        VALUES (:handle, :name, :avatar, :bio, :followers, :repos,
                STR_TO_DATE(:created, '%Y-%m-%dT%H:%i:%sZ'), :raw)
        ON DUPLICATE KEY UPDATE
            display_name = VALUES(display_name),
            profile_image_url = VALUES(profile_image_url),
            bio = VALUES(bio),
            followers = VALUES(followers),
            public_repos = VALUES(public_repos),
            account_created_at = VALUES(account_created_at),
            raw_json = VALUES(raw_json),
            fetched_at = CURRENT_TIMESTAMP
        """,
        {
            "handle": handle,
            "name": profile.get("name"),
            "avatar": profile.get("avatar_url"),
            "bio": profile.get("bio"),
            "followers": profile.get("followers", 0),
            "repos": profile.get("public_repos", 0),
            "created": profile.get("created_at"),
            "raw": profile,
        },
    )

    # Materialised rather than streamed: the language pass below needs a second
    # look at the same repos, and re-paginating would double the cost.
    repos = list(
        itertools.islice(github.paginate(f"/users/{handle}/repos", sort="pushed"), MAX_REPOS)
    )

    for repo in repos:
        db.sql(
            """
            INSERT INTO repos (handle, name, description, primary_language,
                               stars, forks, watchers, size_kb, is_fork,
                               created_at, pushed_at)
            VALUES (:handle, :name, :description, :language, :stars, :forks,
                    :watchers, :size, :is_fork,
                    STR_TO_DATE(:created, '%Y-%m-%dT%H:%i:%sZ'),
                    STR_TO_DATE(:pushed, '%Y-%m-%dT%H:%i:%sZ'))
            ON DUPLICATE KEY UPDATE
                description = VALUES(description),
                primary_language = VALUES(primary_language),
                stars = VALUES(stars),
                forks = VALUES(forks),
                watchers = VALUES(watchers),
                size_kb = VALUES(size_kb),
                is_fork = VALUES(is_fork),
                pushed_at = VALUES(pushed_at)
            """,
            {
                "handle": handle,
                "name": repo["name"],
                "description": repo.get("description"),
                "language": repo.get("language"),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "watchers": repo.get("watchers_count", 0),
                "size": repo.get("size", 0),
                "is_fork": bool(repo.get("fork")),
                "created": repo.get("created_at"),
                "pushed": repo.get("pushed_at"),
            },
        )

    print(f"ingested {len(repos)} repos for {handle}")

    # repo_languages and commit_history both key on repos.id, which the upsert
    # above does not hand back (ON DUPLICATE KEY UPDATE makes LAST_INSERT_ID
    # unreliable on the update path). One SELECT for the whole handle beats one
    # per repo, and both passes below share it.
    id_by_name = {
        row["name"]: row["id"]
        for row in db.sql(
            "SELECT id, name FROM repos WHERE handle = :handle", {"handle": handle}
        )
    }

    # Forks are skipped by both passes -- their languages and commit history are
    # someone else's work, and the consumers filter `is_fork = 0` anyway, so
    # fetching them would spend rate limit on rows nothing reads.
    owned = [r for r in repos if not r.get("fork") and r["name"] in id_by_name]

    languages = _ingest_languages(handle, owned, id_by_name)
    commit_days = _ingest_commit_history(handle, owned, id_by_name, context)

    # -----------------------------------------------------------------------
    # EXTENSION POINT -- hour-of-day, for the `coding_personality` slide
    #   /stats/commit_activity is per-day at best, so it cannot answer "what
    #   hour do they commit at". The only public source is
    #   GET /users/{handle}/events/public (last ~90 days, 100 events).
    #   Whoever builds that will need somewhere to put it; there is no column
    #   for an hour histogram today.
    # -----------------------------------------------------------------------

    return {
        "handle": handle,
        "repos": len(repos),
        "languages": languages,
        "commit_days": commit_days,
    }


def _ingest_languages(handle: str, owned: list[dict], id_by_name: dict[str, int]) -> int:
    """
    Populate repo_languages: one row per (repo, language) with bytes written.

    Costs one GitHub call per non-fork repo, which is why MAX_REPOS matters.
    """
    rows = []
    for repo in owned:
        repo_id = id_by_name[repo["name"]]
        # full_name rather than f"{handle}/{name}": it is the owner GitHub
        # itself reports, so it survives a handle typed in the wrong case.
        for language, byte_count in (github.get(f"/repos/{repo['full_name']}/languages") or {}).items():
            rows.append({"repo_id": repo_id, "language": language, "bytes": byte_count})

    # A language can disappear from a repo between runs (a file deleted, a
    # rewrite), and an upsert alone would leave that stale row behind forever.
    # Scoped to the repos being rewritten, so repos outside this run's
    # MAX_REPOS window keep whatever was ingested for them earlier.
    db.sql_batch(
        "DELETE FROM repo_languages WHERE repo_id = :repo_id",
        [{"repo_id": id_by_name[r["name"]]} for r in owned],
    )
    db.sql_batch(
        """
        INSERT INTO repo_languages (repo_id, language, bytes)
        VALUES (:repo_id, :language, :bytes)
        ON DUPLICATE KEY UPDATE bytes = VALUES(bytes)
        """,
        rows,
    )

    print(f"ingested {len(rows)} language rows across {len(owned)} repos for {handle}")
    return len(rows)


def _ingest_commit_history(
    handle: str, owned: list[dict], id_by_name: dict[str, int], context
) -> int:
    """
    Populate commit_history: one row per (repo, day) the repo saw a commit.

    /stats/commit_activity returns 52 weeks in a single call, which is far
    cheaper than walking /commits, and each week carries a 7-element `days`
    array -- so weekly data expands to the per-day rows the schema wants.

    CAVEAT worth knowing before trusting the number on a slide: this endpoint
    counts commits by EVERY contributor to the repo, not just this handle. On a
    solo repo they are the same; on a repo with collaborators it overcounts.
    Per-author data means /stats/contributors, which is weekly-only, so getting
    both per-author and per-day would take a different (and much more
    expensive) source.
    """
    pending = [(id_by_name[r["name"]], r["full_name"]) for r in owned]
    rows: list[dict] = []
    fetched_ids: list[int] = []

    for _ in range(STATS_MAX_ROUNDS):
        still_pending = []
        for repo_id, full_name in pending:
            weeks = github.get_stats(f"/repos/{full_name}/stats/commit_activity")
            if weeks is None:  # 202: still being computed, ask again later
                still_pending.append((repo_id, full_name))
                continue
            fetched_ids.append(repo_id)
            rows.extend(_days_from_weeks(repo_id, weeks))

        pending = still_pending
        if not pending:
            break

        # Sleeping once per ROUND rather than once per repo: 15 cold repos cost
        # one 2s wait between passes, not 30s of serial stalling.
        remaining_ms = context.get_remaining_time_in_millis() - STATS_WRAP_UP_BUFFER_MS
        if remaining_ms < STATS_RETRY_DELAY_S * 1000:
            break
        time.sleep(STATS_RETRY_DELAY_S)

    if pending:
        # Not fatal. Those repos keep whatever history they already had, and the
        # next run for this handle will find GitHub's cache warm.
        print(f"commit stats still computing for {[n for _, n in pending]}; skipped this run")

    # Same staleness reasoning as the language pass, but scoped to the repos we
    # actually got data for -- a repo stuck on 202 must not have its existing
    # history deleted and then not replaced.
    db.sql_batch(
        "DELETE FROM commit_history WHERE repo_id = :repo_id",
        [{"repo_id": repo_id} for repo_id in fetched_ids],
    )
    db.sql_batch(
        """
        INSERT INTO commit_history (repo_id, commit_date, commit_count)
        VALUES (:repo_id, :commit_date, :commit_count)
        ON DUPLICATE KEY UPDATE commit_count = VALUES(commit_count)
        """,
        rows,
    )

    print(f"ingested {len(rows)} commit-day rows across {len(fetched_ids)} repos for {handle}")
    return len(rows)


def _days_from_weeks(repo_id: int, weeks: list[dict]) -> list[dict]:
    """
    Expand GitHub's weekly buckets into per-day rows.

    Each week is {"week": <unix ts of the Sunday, UTC>, "days": [7 counts]}.
    Days with no commits are dropped rather than stored as zeros: it keeps 52
    weeks x 15 repos from writing ~5,500 mostly-empty rows per run, and the
    consumer only ever asks for days that have commits.
    """
    rows = []
    for week in weeks:
        start = datetime.fromtimestamp(week["week"], tz=timezone.utc).date()
        for offset, count in enumerate(week.get("days") or []):
            if count:
                rows.append(
                    {
                        "repo_id": repo_id,
                        "commit_date": (start + timedelta(days=offset)).isoformat(),
                        "commit_count": count,
                    }
                )
    return rows
