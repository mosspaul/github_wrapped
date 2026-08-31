"""
Pipeline step 1: pull raw data from GitHub into Aurora.

STUB. Fetches the profile and the repo list only, which is enough to make the
whole pipeline run end to end. The extension points below are the real work and
belong to whoever owns ingestion.
"""

from shared import db, github
from shared.pipeline import step


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

    count = 0
    for repo in github.paginate(f"/users/{handle}/repos", sort="pushed"):
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
        count += 1

    print(f"ingested {count} repos for {handle}")

    # ---------------------------------------------------------------------
    # EXTENSION POINT 1 -- repo_languages
    #   GET /repos/{handle}/{repo}/languages returns {"Python": 12345, ...}.
    #   One call per repo, so consider skipping forks and capping to the top N
    #   repos by pushed_at to stay inside the rate limit.
    #
    # EXTENSION POINT 2 -- commit_history
    #   GET /repos/{handle}/{repo}/stats/commit_activity gives 52 weeks of
    #   counts in a single call, which is far cheaper than walking /commits.
    #   Note it returns HTTP 202 with an empty body while GitHub computes the
    #   stats -- retry after a second or two on the first request for a repo.
    # ---------------------------------------------------------------------

    return {"handle": handle, "repos": count}
