"""Shared helpers for the build/deploy scripts."""

import json
import os
import pathlib
import shutil
import subprocess
import time
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

TS_FUNCTIONS = ["api-start-ingest", "api-get-status", "api-get-wrapped"]
PY_FUNCTIONS = ["ingest-github", "compute-stats", "generate-slides"]
ALL_FUNCTIONS = TS_FUNCTIONS + PY_FUNCTIONS

# Lambda's Python 3.12 runtime is x86_64 Linux. Building on Windows or macOS
# without pinning this silently produces wheels for the wrong platform, and the
# function then fails at import with a cryptic ELF/dylib error.
PIP_PLATFORM = ["--platform", "manylinux2014_x86_64", "--only-binary=:all:"]
PY_VERSION = "3.12"


def run(
    cmd: list[str],
    cwd: pathlib.Path | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    """
    Run a command, echoing it first. Exits the script on failure.

    `env` is merged over the current environment rather than replacing it --
    a bare env would strip PATH and break npm/aws entirely.
    """
    printable = " ".join(cmd)
    print(f"  $ {printable}")
    merged = {**os.environ, **env} if env else None
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd or ROOT,
            env=merged,
            check=True,
            text=True,
            capture_output=capture,
            # npm/npx/aws are .cmd shims on Windows and are not directly
            # executable without this.
            shell=(sys.platform == "win32"),
        )
    except subprocess.CalledProcessError as exc:
        if capture and exc.stdout:
            print(exc.stdout)
        if capture and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(f"\ncommand failed ({exc.returncode}): {printable}")
    return (res.stdout or "").strip() if capture else ""


def aws(args: list[str], profile: str | None = None, region: str | None = None) -> str:
    cmd = ["aws", *args]
    if profile:
        cmd += ["--profile", profile]
    if region:
        cmd += ["--region", region]
    return run(cmd, capture=True)


def stack_name(stage: str, suffix: str) -> str:
    return f"gh-wrapped-{stage}-{suffix}"


def stack_outputs(stage: str, suffix: str, profile: str | None = None,
                  region: str | None = None) -> dict[str, str]:
    """Read a deployed stack's outputs as a plain dict."""
    raw = aws(
        [
            "cloudformation", "describe-stacks",
            "--stack-name", stack_name(stage, suffix),
            "--query", "Stacks[0].Outputs",
            "--output", "json",
        ],
        profile=profile,
        region=region,
    )
    outputs = json.loads(raw or "[]") or []
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def clean(path: pathlib.Path) -> None:
    """
    Empty a build directory.

    Windows locks a directory that any process has open as its working
    directory (a stray shell, an editor, an antivirus scan), and rmtree then
    fails with WinError 32. Retrying clears the transient cases; if the lock
    persists, say which directory is stuck rather than dumping a shutil
    traceback that does not name the real problem.
    """
    for attempt in range(3):
        try:
            if path.exists():
                shutil.rmtree(path)
            break
        except PermissionError:
            if attempt == 2:
                sys.exit(
                    f"cannot delete {path} -- something is holding it open.\n"
                    "On Windows this is usually a terminal cd'd into that "
                    "directory, or an editor with a file from it open."
                )
            time.sleep(1)
    path.mkdir(parents=True, exist_ok=True)
