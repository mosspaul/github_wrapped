"""
Build the front end and push it to Amplify Hosting.

    python scripts/deploy-web.py
    python scripts/deploy-web.py --skip-build

This uses Amplify's *manual deployment* API rather than Amplify's own
git integration, because the repository is owned by another GitHub account.
Installing the Amplify GitHub App (or minting a token with `admin:repo_hook`)
requires repository admin, which on a personal-account repo only the owner has.
Manual deployment needs no GitHub access at all -- we hand Amplify a zip.

The same script runs locally and in GitHub Actions, so the two cannot drift.
"""

import argparse
import pathlib
import sys
import time
import urllib.request
import zipfile

from common import BUILD, ROOT, aws, run, stack_outputs

WEB = ROOT / "web"
DIST = WEB / "dist"


def build_site(api_base: str) -> None:
    """
    Build the site with VITE_API_BASE injected.

    This has to happen here. The Amplify app carries VITE_API_BASE as an
    environment variable, but that only applies to builds Amplify itself runs
    from a connected repository. We build the bundle ourselves and hand Amplify
    the finished artifact, so nothing on the Amplify side participates -- and
    Vite inlines env vars at build time, so a missing value silently ships a
    site whose every request goes to "undefined/wrapped/...".
    """
    print(f"=== build (VITE_API_BASE={api_base}) ===")
    if not (WEB / "node_modules").exists():
        run(["npm", "ci"], cwd=WEB)
    run(["npm", "run", "build"], cwd=WEB, env={"VITE_API_BASE": api_base})


def zip_dist() -> pathlib.Path:
    if not DIST.exists():
        sys.exit(f"{DIST} does not exist -- run without --skip-build")
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / "web-dist.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))
    print(f"packed {archive.stat().st_size // 1024}KB from {DIST}")
    return archive


def verify_api_base(archive: pathlib.Path, api_base: str) -> None:
    """
    Fail loudly if the API URL did not make it into the bundle.

    A site missing it still loads and looks fine -- it just cannot talk to the
    backend, which is a miserable thing to discover from the browser console
    during a demo. Cheap to check, so check.
    """
    host = api_base.replace("https://", "").split(".")[0]
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.endswith(".js") and host.encode() in zf.read(name):
                print(f"verified: {host} is baked into {name}")
                return
    sys.exit(
        f"VITE_API_BASE ({api_base}) is not present in the built bundle. "
        "The site would deploy but could not reach the API."
    )


def upload(url: str, archive: pathlib.Path) -> None:
    # A plain PUT to the presigned URL. Deliberately not boto3: the URL already
    # carries its own auth, and signing it again would be rejected.
    data = archive.read_bytes()
    request = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Content-Type": "application/zip", "Content-Length": str(len(data))},
    )
    with urllib.request.urlopen(request) as response:
        if response.status not in (200, 204):
            sys.exit(f"upload failed: HTTP {response.status}")
    print("uploaded")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", default="dev")
    p.add_argument("--branch", default="main")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    args = p.parse_args()

    web = stack_outputs(args.stage, "web", args.profile, args.region)
    app_id = web["AmplifyAppId"]
    site_url = web["AmplifyUrl"]

    app = stack_outputs(args.stage, "app", args.profile, args.region)
    api_base = app["ApiEndpoint"]

    if not args.skip_build:
        build_site(api_base)

    archive = zip_dist()
    verify_api_base(archive, api_base)

    print(f"\n=== deploy to {app_id}/{args.branch} ===")
    import json

    deployment = json.loads(
        aws(
            ["amplify", "create-deployment", "--app-id", app_id,
             "--branch-name", args.branch, "--output", "json"],
            profile=args.profile, region=args.region,
        )
    )
    job_id = deployment["jobId"]

    upload(deployment["zipUploadUrl"], archive)

    aws(
        ["amplify", "start-deployment", "--app-id", app_id,
         "--branch-name", args.branch, "--job-id", job_id, "--output", "json"],
        profile=args.profile, region=args.region,
    )

    if args.no_wait:
        print(f"started job {job_id}")
        return

    print("waiting for the build...")
    for _ in range(60):
        status = aws(
            ["amplify", "get-job", "--app-id", app_id, "--branch-name", args.branch,
             "--job-id", job_id, "--query", "job.summary.status", "--output", "text"],
            profile=args.profile, region=args.region,
        )
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            break
        time.sleep(5)
    else:
        sys.exit(f"job {job_id} did not finish in time")

    if status != "SUCCEED":
        sys.exit(f"deployment {job_id} finished as {status}")

    print(f"\ndeployed: {site_url}")


if __name__ == "__main__":
    main()
