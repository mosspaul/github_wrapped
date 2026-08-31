"""
Rebuild one Lambda and push its code straight to AWS. ~10 seconds.

    python scripts/push-fn.py compute-stats
    python scripts/push-fn.py api-get-wrapped --stage dev

This is the inner loop -- use it while you are actively working on a function.
It updates code ONLY. If you changed anything in infra/ (env vars, timeout,
IAM, routes), you need `python scripts/deploy.py --only app` instead.
"""

import argparse
import pathlib
import sys
import zipfile

from common import ALL_FUNCTIONS, BUILD, ROOT, aws, run


def zip_dir(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src))
    return dest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("function", help=f"one of: {', '.join(ALL_FUNCTIONS)}")
    p.add_argument("--stage", default="dev")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--skip-build", action="store_true")
    args = p.parse_args()

    if args.function not in ALL_FUNCTIONS:
        sys.exit(f"unknown function '{args.function}'\nvalid: {ALL_FUNCTIONS}")

    if not args.skip_build:
        run([sys.executable, str(ROOT / "scripts" / "build.py"), args.function])

    src = BUILD / args.function
    if not src.exists():
        sys.exit(f"{src} does not exist -- run without --skip-build")

    archive = zip_dir(src, BUILD / f"{args.function}.zip")
    fn_name = f"gh-wrapped-{args.stage}-{args.function}"

    print(f"\nuploading {archive.stat().st_size // 1024}KB to {fn_name}")
    aws(
        [
            "lambda", "update-function-code",
            "--function-name", fn_name,
            "--zip-file", f"fileb://{archive}",
            "--no-cli-pager",
            "--output", "text",
            "--query", "LastModified",
        ],
        profile=args.profile,
        region=args.region,
    )

    print(f"\n{fn_name} updated. Tail it with:")
    print(f"  aws logs tail /aws/lambda/{fn_name} --follow")


if __name__ == "__main__":
    main()
