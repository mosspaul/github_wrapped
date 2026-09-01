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

# The Data API caps a batch by total payload size rather than a documented row
# count, so this is a conservative chunk that keeps any realistic row well clear
# of the limit rather than a number lifted from the docs.
_BATCH_SIZE = 250


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


def sql_batch(statement: str, param_sets: list[dict[str, Any]]) -> None:
    """
    Run one statement once per parameter set, in a single Data API call.

        sql_batch("DELETE FROM repo_languages WHERE repo_id = :repo_id",
                  [{"repo_id": 1}, {"repo_id": 2}])

    Use this instead of looping over sql() whenever the same statement runs per
    row: ingesting one prolific user's languages is ~150 rows, which is 150
    HTTPS round trips as a loop and one or two as a batch.

    Returns nothing -- batch_execute_statement does not return result rows, only
    generated fields, which no caller here needs.
    """
    for start in range(0, len(param_sets), _BATCH_SIZE):
        chunk = param_sets[start : start + _BATCH_SIZE]
        _client.batch_execute_statement(
            resourceArn=RESOURCE_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql=statement,
            parameterSets=[
                [{"name": name, "value": _to_field(value)} for name, value in ps.items()]
                for ps in chunk
            ],
        )


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
