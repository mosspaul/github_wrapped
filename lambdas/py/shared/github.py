"""
GitHub REST client.

The PAT is fetched once at import time, not per invocation: module scope
persists across warm invocations, so a container handles many requests on one
Secrets Manager call instead of one each.
"""

import os
import time
from typing import Any, Iterator

import boto3
import requests

API = "https://api.github.com"
_TIMEOUT = 20


def _load_token() -> str | None:
    arn = os.environ.get("GITHUB_PAT_SECRET_ARN")
    if not arn:
        return None
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    import json

    token = json.loads(raw).get("token")
    if not token or token == "REPLACE_ME":
        # Unauthenticated still works at 60 req/hr, which is enough to smoke
        # test but will die instantly under real use. Loud warning, not a crash.
        print("WARNING: GitHub PAT is unset; falling back to 60 req/hr anonymous limit")
        return None
    return token


_TOKEN = _load_token()

session = requests.Session()
session.headers.update(
    {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-wrapped-hackathon",
    }
)
if _TOKEN:
    session.headers["Authorization"] = f"Bearer {_TOKEN}"


class GithubError(RuntimeError):
    pass


def get(path: str, **params: Any) -> Any:
    """GET one resource. Raises GithubError with a useful message on failure."""
    url = path if path.startswith("http") else f"{API}{path}"
    res = session.get(url, params=params, timeout=_TIMEOUT)

    if res.status_code == 404:
        raise GithubError(f"not found: {path}")

    # 403 with a zero remaining count is a rate limit, not a permissions
    # problem. Distinguishing them saves a lot of confused debugging.
    if res.status_code in (403, 429):
        remaining = res.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            reset = int(res.headers.get("X-RateLimit-Reset", 0))
            wait = max(0, reset - int(time.time()))
            raise GithubError(
                f"GitHub rate limit exhausted; resets in {wait}s. "
                "Is gh-wrapped/<stage>/github-pat populated?"
            )
        raise GithubError(f"forbidden: {path} ({res.text[:200]})")

    if not res.ok:
        raise GithubError(f"{res.status_code} on {path}: {res.text[:200]}")

    return res.json()


def paginate(path: str, per_page: int = 100, max_pages: int = 10, **params: Any) -> Iterator[dict]:
    """
    Walk a paginated collection, following Link headers.

    max_pages is a deliberate safety valve: a user with thousands of repos would
    otherwise blow the Lambda timeout and the rate limit budget in one call.
    """
    url: str | None = f"{API}{path}"
    page_params = {**params, "per_page": per_page}
    pages = 0

    while url and pages < max_pages:
        res = session.get(url, params=page_params if pages == 0 else None, timeout=_TIMEOUT)
        if not res.ok:
            raise GithubError(f"{res.status_code} on {url}: {res.text[:200]}")

        for item in res.json():
            yield item

        url = res.links.get("next", {}).get("url")
        pages += 1
