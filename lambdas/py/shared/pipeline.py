"""Shared plumbing for the three async pipeline steps."""

import functools
import inspect
import os
import json
from typing import Callable

import boto3

from . import db

_lambda = boto3.client("lambda")


def invoke_next(handle: str) -> None:
    """Hand off to the next step, if there is one."""
    next_fn = os.environ.get("NEXT_FN")
    if not next_fn:
        return
    _lambda.invoke(
        FunctionName=next_fn,
        InvocationType="Event",
        Payload=json.dumps({"handle": handle}).encode(),
    )


def step(phase: str, chain: bool = True) -> Callable:
    """
    Wrap a pipeline handler so every step behaves consistently:

      * set status to `phase` on entry
      * on success, invoke the next function
      * on failure, record status='error' with the message and STOP the chain

    That last part matters. These functions are invoked asynchronously, so a
    raised exception goes nowhere a user can see it -- writing it to
    wrapped_jobs is the only way the front end ever learns the run failed.
    """

    def decorator(fn: Callable) -> Callable:
        # Most steps only need the handle. generate-slides needs the Lambda
        # context too, to watch its own remaining execution time -- inspect
        # once at decoration time so ingest-github and compute-stats don't
        # have to change shape.
        wants_context = len(inspect.signature(fn).parameters) >= 2

        @functools.wraps(fn)
        def wrapper(event, context):
            handle = (event or {}).get("handle")
            if not handle:
                raise ValueError("event is missing 'handle'")

            db.set_status(handle, phase)
            try:
                result = fn(handle, context) if wants_context else fn(handle)
            except Exception as exc:
                print(f"{phase} failed for {handle}: {exc!r}")
                db.set_status(handle, "error", f"{phase}: {exc}"[:1000])
                raise

            if chain:
                invoke_next(handle)
            return result or {"handle": handle, "phase": phase, "ok": True}

        return wrapper

    return decorator
