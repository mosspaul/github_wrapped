"""
Pipeline step 2: turn raw rows into one stats blob per slide.

STUB. `languages` and `standout_projects` are genuinely computed from what the
ingest step already stores; the other three return placeholder shapes so the
pipeline completes. Each builder is independent -- claim one and fill it in.

To add a slide: add it to shared/slide-types.json, then add a builder here with
a matching key. A missing builder is a loud KeyError, not a silent skip.
"""

from shared import db
from shared.pipeline import step
from shared.slides import SLIDE_IDS
from datetime import date


def _languages(handle: str) -> dict:
    # Bytes, not repo count -- "language breakdown by bytes written" is what
    # slide-types.json promises and what CONTRACTS.md documents. Forks are
    # excluded here as well as in ingest, so this stays right even if an older
    # run left fork rows in repo_languages.
    rows = db.sql(
        """
        SELECT rl.language, CAST(SUM(rl.bytes) AS SIGNED) AS bytes,
               COUNT(*) AS repo_count
          FROM repo_languages rl
          JOIN repos r ON r.id = rl.repo_id
         WHERE r.handle = :handle AND r.is_fork = 0
         GROUP BY rl.language
         ORDER BY bytes DESC
         LIMIT 8
        """,
        {"handle": handle},
    )
    return {"top": rows, "basis": "bytes"}


def _standout_projects(handle: str) -> dict:
    rows = db.sql(
        """
        SELECT name, description, primary_language, stars, forks
          FROM repos
         WHERE handle = :handle AND is_fork = 0
         ORDER BY stars DESC, forks DESC
         LIMIT 3
        """,
        {"handle": handle},
    )
    return {"projects": rows}


def _commit_activity(handle: str) -> dict:
    # Requires ingest's EXTENSION POINT 2 (commit_history) to be populated.
    rows = db.sql(
        """
        -- CAST, because MySQL's SUM() is a DECIMAL and the Data API serialises
        -- DECIMAL as a JSON *string* to keep precision. Without it `commits`
        -- arrives as "42" and sum() raises int + str. See CLAUDE.md.
        SELECT ch.commit_date AS commit_date,
               CAST(SUM(ch.commit_count) AS SIGNED) AS commits
          FROM commit_history ch
          JOIN repos r ON r.id = ch.repo_id
         WHERE r.handle = :handle
         GROUP BY ch.commit_date
        HAVING SUM(ch.commit_count) > 0
         ORDER BY ch.commit_date
        """,
        {"handle": handle},
    )

    if not rows:
        return {"total_commits": 0, "busiest_day": None, "longest_streak_days": 0}

    total_commits = sum(r["commits"]for r in rows)
    busiest = max(rows, key=lambda r: r["commits"])

    longest_streak = current_streak = 1
    prev_date = date.fromisoformat(rows[0]["commit_date"])
    for row in rows[1:]:
        current_date = date.fromisoformat(row["commit_date"])
        if (current_date - prev_date).days == 1:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 1
        prev_date = current_date

    
    return {
        "total_commits": total_commits,
        "busiest_day": {"date": busiest["commit_date"], "commits": busiest["commits"]},
        "longest_streak_days": longest_streak,
    }


def _coding_personality(handle: str) -> dict:
    # PLACEHOLDER -- the interesting version reads commit timestamps and
    # derives an archetype (night owl, weekend warrior, ...).
    return {"placeholder": True, "archetype": "Unknown", "traits": []}


def _year_in_code(handle: str) -> dict:
    repo_rows = db.sql(
        """
        SELECT COUNT(*) AS repos_created
          FROM repos
         WHERE handle = :handle AND YEAR(created_at) = YEAR(CURDATE())
        """,
        {"handle": handle},
    )

    activity_rows = db.sql(
        """
        SELECT CAST(SUM(ch.commit_count) AS SIGNED) AS total_commits,
               COUNT(DISTINCT ch.repo_id) AS repos_edited
          FROM commit_history ch
          JOIN repos r ON r.id = ch.repo_id
         WHERE r.handle = :handle
           AND YEAR(ch.commit_date) = YEAR(CURDATE())
        """,
        {"handle": handle},
    )

    activity = activity_rows[0] if activity_rows else {}

    # Only repo_rows is worth guarding: an aggregate SELECT always returns one
    # row, so `activity_rows` is never empty even with no commit_history at all
    # (it comes back as {"total_commits": null, "repos_edited": 0}).
    if not repo_rows:
        return {"repos_created_this_year": 0, "repos_edited_this_year": 0, "total_commits": 0}

    return {
        "repos_created_this_year": repo_rows[0]["repos_created"],
        "repos_edited_this_year": activity.get("repos_edited"),
        "total_commits": activity.get("total_commits"),
    }


STAT_BUILDERS = {
    "commit_activity": _commit_activity,
    "languages": _languages,
    "coding_personality": _coding_personality,
    "year_in_code": _year_in_code,
    "standout_projects": _standout_projects,
}


@step("computing")
def handler(handle: str) -> dict:
    missing = set(SLIDE_IDS) - set(STAT_BUILDERS)
    if missing:
        raise KeyError(f"slide-types.json declares slides with no builder: {sorted(missing)}")

    for slide_id in SLIDE_IDS:
        stats = STAT_BUILDERS[slide_id](handle)
        db.sql(
            """
            INSERT INTO slides (handle, slide_type, stats_json)
            VALUES (:handle, :slide_type, :stats)
            ON DUPLICATE KEY UPDATE stats_json = VALUES(stats_json)
            """,
            {"handle": handle, "slide_type": slide_id, "stats": stats},
        )

    print(f"computed {len(SLIDE_IDS)} slides for {handle}")
    return {"handle": handle, "slides": len(SLIDE_IDS)}
