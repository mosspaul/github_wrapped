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


def _languages(handle: str) -> dict:
    rows = db.sql(
        """
        SELECT r.primary_language AS language, COUNT(*) AS repo_count,
               SUM(r.stars) AS stars
          FROM repos r
         WHERE r.handle = :handle
           AND r.primary_language IS NOT NULL
           AND r.is_fork = 0
         GROUP BY r.primary_language
         ORDER BY repo_count DESC
         LIMIT 8
        """,
        {"handle": handle},
    )
    # NOTE: this counts repos per language, not bytes. Once ingest populates
    # repo_languages, switch to SUM(bytes) -- that is the number the slide
    # copy actually promises.
    return {"top": rows, "basis": "repo_count"}


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
    # PLACEHOLDER -- needs commit_history, which ingest does not populate yet.
    rows = db.sql(
        "SELECT COUNT(*) AS repo_count FROM repos WHERE handle = :handle",
        {"handle": handle},
    )
    return {
        "placeholder": True,
        "repo_count": rows[0]["repo_count"] if rows else 0,
        "busiest_day": None,
        "longest_streak_days": None,
    }


def _coding_personality(handle: str) -> dict:
    # PLACEHOLDER -- the interesting version reads commit timestamps and
    # derives an archetype (night owl, weekend warrior, ...).
    return {"placeholder": True, "archetype": "Unknown", "traits": []}


def _year_in_code(handle: str) -> dict:
    # PLACEHOLDER -- headline totals for the year.
    rows = db.sql(
        """
        SELECT COUNT(*) AS repos_created
          FROM repos
         WHERE handle = :handle AND YEAR(created_at) = YEAR(CURDATE())
        """,
        {"handle": handle},
    )
    return {
        "placeholder": True,
        "repos_created_this_year": rows[0]["repos_created"] if rows else 0,
        "total_commits": None,
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
