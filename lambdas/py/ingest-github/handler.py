"""
Pipeline step 1: pull raw data from GitHub into Aurora.

Fetches the profile + repos (capped at MAX_REPOS, most recently pushed first)
and the per-repo language byte counts, and writes all three to Aurora.

This step derives nothing. Anything computed here would have to be written to a
table to survive anyway -- steps are invoked async and hand off only
{"handle": ...}, so a return value goes nowhere -- and `compute-stats` is where
raw rows become slide stats. Keep this function about fetching and storing.
"""

import itertools

from shared import db, github
from shared.pipeline import step

MAX_REPOS = 15  # keeps DB writes cheap and bounds the language pass below,
                # which costs one GitHub call per non-fork repo.


@step("ingesting")
def handler(handle: str) -> dict:
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

    languages = _ingest_languages(handle, repos)

    # -----------------------------------------------------------------------
    # EXTENSION POINT -- commit_history
    #   GET /repos/{handle}/{repo}/stats/commit_activity gives 52 weeks of
    #   counts in a single call, which is far cheaper than walking /commits.
    #   Note it returns HTTP 202 with an empty body while GitHub computes the
    #   stats -- retry after a second or two on the first request for a repo.
    #   Iterate `repos` above rather than re-paginating, same as the language
    #   pass does.
    #
    #   That endpoint is weekly, so it cannot answer "what hour do they commit
    #   at" -- the `coding_personality` slide needs hour-of-day, and the only
    #   public source for it is GET /users/{handle}/events/public (last ~90
    #   days, 100 events). Whoever builds that will need somewhere to put it;
    #   there is no column for an hour histogram today.
    # -----------------------------------------------------------------------

    return {"handle": handle, "repos": len(repos), "languages": languages}


def _ingest_languages(handle: str, repos: list[dict]) -> int:
    """
    Populate repo_languages: one row per (repo, language) with bytes written.

    Costs one GitHub call per repo, which is why MAX_REPOS matters. Forks are
    skipped -- their language bytes are someone else's work, and every consumer
    of this table filters `is_fork = 0` anyway, so fetching them would spend
    rate limit on rows nothing reads.
    """
    # repo_languages keys on repos.id, which the upsert above does not hand
    # back (ON DUPLICATE KEY UPDATE makes LAST_INSERT_ID unreliable on the
    # update path). One SELECT for the whole handle beats one per repo.
    id_by_name = {
        row["name"]: row["id"]
        for row in db.sql(
            "SELECT id, name FROM repos WHERE handle = :handle", {"handle": handle}
        )
    }

    owned = [r for r in repos if not r.get("fork") and r["name"] in id_by_name]

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
