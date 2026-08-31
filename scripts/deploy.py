"""
Build and deploy the whole project.

    python scripts/deploy.py                        # dev, everything
    python scripts/deploy.py --stage dev --only app # just the app stack
    python scripts/deploy.py --skip-build

Stack order matters: app imports exports from data, and web imports the API
endpoint from app. The first run takes ~15 minutes because Aurora has to
provision; later runs are ~2 minutes.
"""

import argparse
import pathlib
import sys

from common import BUILD, ROOT, aws, clean, run, stack_name, stack_outputs

INFRA = ROOT / "infra"

STACKS = {
    "bootstrap": "00-bootstrap.yaml",
    "data": "01-data.yaml",
    "app": "02-app.yaml",
    "web": "03-web.yaml",
    "access": "04-access.yaml",
}

# access creates named IAM users/roles/policies, which CloudFormation refuses
# to touch without the stronger acknowledgement.
NAMED_IAM = {"app", "access"}

PACKAGED = ROOT / "build" / "packaged"


def deploy_stack(
    key: str,
    stage: str,
    profile: str | None,
    region: str | None,
    params: list[str],
) -> None:
    template = INFRA / STACKS[key]
    name = stack_name(stage, key)
    print(f"\n=== {name} ===")

    to_deploy = template

    # Only the app stack has local Code: paths that need uploading.
    if key == "app":
        bucket = stack_outputs(stage, "bootstrap", profile, region)["ArtifactsBucketName"]
        PACKAGED.mkdir(parents=True, exist_ok=True)
        to_deploy = PACKAGED / STACKS[key]
        aws(
            [
                "cloudformation", "package",
                "--template-file", str(template),
                "--s3-bucket", bucket,
                "--s3-prefix", f"{stage}/lambdas",
                "--output-template-file", str(to_deploy),
            ],
            profile=profile,
            region=region,
        )

    cmd = [
        "cloudformation", "deploy",
        "--template-file", str(to_deploy),
        "--stack-name", name,
        "--no-fail-on-empty-changeset",
        "--capabilities",
        "CAPABILITY_NAMED_IAM" if key in NAMED_IAM else "CAPABILITY_IAM",
        "--parameter-overrides", f"Stage={stage}", *params,
    ]
    aws(cmd, profile=profile, region=region)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", default="dev")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default=None)
    p.add_argument(
        "--only",
        choices=list(STACKS),
        action="append",
        help="deploy only these stacks (repeatable)",
    )
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--skip-migrate", action="store_true")
    p.add_argument(
        "--github-token",
        default=None,
        help=(
            "Optional. Only for connecting the repo to Amplify through "
            "CloudFormation instead of the console. Must be a CLASSIC GitHub "
            "token (ghp_...) with repo + admin:repo_hook -- fine-grained "
            "tokens do not work with Amplify."
        ),
    )
    p.add_argument(
        "--oidc-provider-arn",
        default=None,
        help=(
            "Reuse an existing GitHub OIDC provider instead of creating one. "
            "Find it with: aws iam list-open-id-connect-providers"
        ),
    )
    p.add_argument(
        "--repository-url",
        default="https://github.com/mosspaul/github_wrapped",
        help="HTTPS repo URL, used only alongside --github-token",
    )
    p.add_argument(
        "--bedrock-model",
        default=None,
        help=(
            "Inference profile id for generate-slides, e.g. "
            "us.anthropic.claude-opus-4-6-v1. NOTE: editing the Default in "
            "02-app.yaml does NOT change an existing stack -- CloudFormation "
            "reuses the value a parameter was last deployed with, so you have "
            "to pass it here to actually change it."
        ),
    )
    args = p.parse_args()

    # `access` is deliberately not in the default set: it creates account-wide
    # IAM and only the account owner should run it, deliberately.
    order = args.only or ["bootstrap", "data", "app", "web"]

    if not args.skip_build and "app" in order:
        print("=== build ===")
        run([sys.executable, str(ROOT / "scripts" / "build.py")])

    for key in ["bootstrap", "data", "app", "web", "access"]:
        if key not in order:
            continue

        params: list[str] = []
        if key == "app" and args.bedrock_model:
            params.append(f"BedrockModelId={args.bedrock_model}")
        if key == "access" and args.oidc_provider_arn:
            # The GitHub OIDC provider is account-global. If another project
            # already created one, creating a second fails with AlreadyExists
            # -- reuse it instead.
            params.append("CreateOidcProvider=false")
            params.append(f"ExistingOidcProviderArn={args.oidc_provider_arn}")
        if key == "web" and args.github_token:
            # Optional. The default path connects the repository in the Amplify
            # console instead, which needs no token at all -- see 03-web.yaml.
            # Passing nothing (rather than an empty string) matters on the token
            # path: `cloudformation deploy` keeps the stored value for any
            # parameter it is not given, so an empty string would wipe a real
            # token and break Amplify's repo access.
            params.append(f"GithubAccessToken={args.github_token}")
            params.append(f"RepositoryUrl={args.repository_url}")

        deploy_stack(key, args.stage, args.profile, args.region, params)

        # Tables must exist before any Lambda runs, and the data stack is what
        # creates the cluster they live in.
        if key == "data" and not args.skip_migrate:
            print("\n=== migrate ===")
            migrate = [sys.executable, str(ROOT / "db" / "migrate.py"), "--stage", args.stage]
            if args.profile:
                migrate += ["--profile", args.profile]
            if args.region:
                migrate += ["--region", args.region]
            run(migrate)

    print("\n=== done ===")
    # Only report stacks this run actually touched -- probing the others just
    # prints a scary ValidationError for something that was never deployed.
    if "app" in order:
        app = stack_outputs(args.stage, "app", args.profile, args.region)
        print(f"API      {app.get('ApiEndpoint')}")
    if "web" in order:
        web = stack_outputs(args.stage, "web", args.profile, args.region)
        print(f"Web      {web.get('AmplifyUrl')}")


if __name__ == "__main__":
    main()
