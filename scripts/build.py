"""
Bundle every Lambda into build/<name>/.

    python scripts/build.py                 # everything
    python scripts/build.py compute-stats   # just one

Written in Python rather than shell so it behaves identically on Windows,
macOS and the GitHub Actions runner. No Docker and no SAM CLI required.
"""

import argparse
import pathlib
import shutil
import sys

from common import (
    ALL_FUNCTIONS,
    BUILD,
    PIP_PLATFORM,
    PY_FUNCTIONS,
    PY_VERSION,
    ROOT,
    TS_FUNCTIONS,
    clean,
    run,
)

TS_DIR = ROOT / "lambdas" / "ts"
PY_DIR = ROOT / "lambdas" / "py"
SLIDE_TYPES = ROOT / "shared" / "slide-types.json"


def ensure_node_modules() -> None:
    if not (TS_DIR / "node_modules").exists():
        print("installing TypeScript dependencies (first run only)")
        run(["npm", "install"], cwd=TS_DIR)


def build_ts(name: str) -> None:
    print(f"\n[ts] {name}")
    out = BUILD / name
    clean(out)
    run(
        [
            "npx", "esbuild", f"{name}/index.ts",
            "--bundle",
            "--platform=node",
            "--target=node22",
            "--format=cjs",
            # Bundling the AWS SDK rather than relying on the runtime's copy
            # keeps behaviour stable when AWS updates the runtime underneath us.
            # Costs ~2MB per function, which is irrelevant here.
            f"--outfile={out / 'index.js'}",
            "--sourcemap=inline",
        ],
        cwd=TS_DIR,
    )


def _has_packages(requirements: pathlib.Path) -> bool:
    """
    True if a requirements file requests anything beyond comments and -r
    includes. pip errors on a file that resolves to nothing, so check first.
    """
    if not requirements.exists():
        return False
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-r"):
            return True
    return False


def build_py(name: str) -> None:
    print(f"\n[py] {name}")
    out = BUILD / name
    clean(out)

    # Requirements are per-function on purpose. compute-stats only talks to the
    # Data API via the runtime's boto3 and needs nothing vendored; putting
    # anthropic in a shared file made its bundle 46MB for no reason.
    reqs = PY_DIR / name / "requirements.txt"
    if _has_packages(reqs):
        run(
            [
                sys.executable, "-m", "pip", "install",
                "-r", str(reqs),
                "--target", str(out),
                "--python-version", PY_VERSION,
                *PIP_PLATFORM,
                "--upgrade",
                "--quiet",
            ]
        )
    else:
        print("  (no third-party dependencies)")

    # The handler itself, then the shared package beside it.
    shutil.copy2(PY_DIR / name / "handler.py", out / "handler.py")
    shutil.copytree(
        PY_DIR / "shared",
        out / "shared",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    # shared/slides.py reads this at import time; it lives outside lambdas/ so
    # the TypeScript side can import the same file.
    shutil.copy2(SLIDE_TYPES, out / "shared" / "slide-types.json")

    # __pycache__ is dead weight -- Lambda's filesystem is read-only, so these
    # are never reused. dist-info directories are deliberately KEPT: several
    # libraries resolve their own version through importlib.metadata at import
    # time and raise PackageNotFoundError if the metadata is missing.
    for cache in out.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "functions",
        nargs="*",
        default=None,
        help=f"one or more of: {', '.join(ALL_FUNCTIONS)} (default: all)",
    )
    args = parser.parse_args()

    targets = args.functions or ALL_FUNCTIONS
    unknown = [t for t in targets if t not in ALL_FUNCTIONS]
    if unknown:
        sys.exit(f"unknown function(s): {unknown}\nvalid: {ALL_FUNCTIONS}")

    BUILD.mkdir(exist_ok=True)

    if any(t in TS_FUNCTIONS for t in targets):
        ensure_node_modules()

    for name in targets:
        if name in TS_FUNCTIONS:
            build_ts(name)
        else:
            build_py(name)

    print(f"\nbuilt {len(targets)} function(s) into {BUILD}")


if __name__ == "__main__":
    main()
