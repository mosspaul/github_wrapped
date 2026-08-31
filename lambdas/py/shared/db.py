"""
RDS Data API access, mirroring lambdas/ts/shared/dataApi.py in behaviour.

formatRecordsAs="JSON" makes Aurora serialise rows server-side into plain
objects, which avoids unwrapping the Data API's {"stringValue": ...} field
tagging by hand at every call site.
"""

import json
import os
from typing import Any

import boto3

_client = boto3.client("rds-data")

RESOURCE_ARN = os.environ["DB_CLUSTER_ARN"]
SECRET_ARN = os.environ["DB_SECRET_ARN"]
DATABASE = os.environ.get("DB_NAME", "gh_wrapped")


def _to_field(value: Any) -> dict:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    # dicts and lists are almost always meant for a JSON column; serialising
    # them here is friendlier than making every caller remember json.dumps.
    if isinstance(value, (dict, list)):
        return {"stringValue": json.dumps(value)}
    raise TypeError(f"cannot bind {type(value).__name__} to a SQL parameter")


def sql(statement: str, params: dict[str, Any] | None = None) -> list[dict]:
    """
    Run one statement with named parameters.

        sql("SELECT * FROM users WHERE handle = :handle", {"handle": "octocat"})

    Always use named parameters -- string interpolation is an injection risk and
    defeats server-side plan reuse.
    """
    parameters = [
        {"name": name, "value": _to_field(value)}
        for name, value in (params or {}).items()
    ]

    res = _client.execute_statement(
        resourceArn=RESOURCE_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=statement,
        parameters=parameters,
        formatRecordsAs="JSON",
    )

    # Absent for statements that return no rows (INSERT/UPDATE/DDL). Not an error.
    raw = res.get("formattedRecords")
    return json.loads(raw) if raw else []


def set_status(handle: str, status: str, error: str | None = None) -> None:
    """
    Move a job to a new phase. Every pipeline step calls this on entry, and the
    error path calls it with status='error' -- that is the only way a failure
    becomes visible to the front end, since these Lambdas are invoked async and
    nobody is listening for the exception.
    """
    sql(
        """
        INSERT INTO wrapped_jobs (handle, status, error)
        VALUES (:handle, :status, :error)
        ON DUPLICATE KEY UPDATE status = :status, error = :error
        """,
        {"handle": handle, "status": status, "error": error},
    )
