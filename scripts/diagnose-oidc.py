"""
Explain why GitHub Actions cannot assume the deploy role.

    python scripts/diagnose-oidc.py

`Not authorized to perform sts:AssumeRoleWithWebIdentity` is a single opaque
message covering every possible mismatch, and STS deliberately will not say
which one -- naming the cause would let a stranger enumerate your roles.

So do not reason forward from the template about what GitHub *should* be
sending. Read what it actually sent: every rejected attempt is recorded in
CloudTrail, and the userIdentity carries the real `sub` claim. That is the only
authoritative source here, and it is what this script compares against.

Checked:
    1. the deploy role's trust policy   (infra/04-access.yaml deploys it)
    2. the OIDC provider it points at   (account-global, possibly NOT ours)
    3. the sub claims GitHub really presented   (CloudTrail)
    4. the workflow's role ARN           (.github/workflows/deploy.yml)
"""

import argparse
import json
import re
import sys

from common import ROOT, aws, stack_outputs

WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def as_list(value) -> list[str]:
    """An IAM condition value is a bare string or a list; callers want a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def observed_subs(role_arn: str, region: str, profile: str | None) -> list[tuple[str, str]]:
    """
    The (sub, outcome) pairs GitHub actually presented for this role.

    CloudTrail's Event history covers 90 days with no trail configured, and
    records failed calls -- which is the whole point, since a failure is what
    we are here to explain.
    """
    raw = aws(
        ["cloudtrail", "lookup-events",
         "--lookup-attributes", "AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity",
         "--max-results", "25", "--output", "json"],
        profile=profile, region=region,
    )
    seen: dict[str, str] = {}
    for event in json.loads(raw or "{}").get("Events", []):
        detail = json.loads(event["CloudTrailEvent"])
        resources = [r.get("ARN") for r in detail.get("resources", [])]
        if role_arn not in resources:
            continue
        sub = detail.get("userIdentity", {}).get("userName")
        if sub:
            seen.setdefault(sub, detail.get("errorCode") or "SUCCESS")
    return list(seen.items())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", default="dev")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default="us-west-1")
    args = p.parse_args()

    problems: list[str] = []

    access = stack_outputs(args.stage, "access", args.profile, args.region)
    role_arn = access.get("DeployRoleArn")
    if not role_arn:
        sys.exit("The access stack has no DeployRoleArn output -- is it deployed?")
    role_name = role_arn.rsplit("/", 1)[-1]
    print(f"deploy role : {role_arn}")

    role = json.loads(aws(
        ["iam", "get-role", "--role-name", role_name, "--output", "json"],
        profile=args.profile,
    ))["Role"]

    statement = role["AssumeRolePolicyDocument"]["Statement"][0]
    conditions = statement.get("Condition", {}).get("StringEquals", {})
    provider_arn = statement["Principal"]["Federated"]
    trusted_auds = as_list(conditions.get("token.actions.githubusercontent.com:aud"))
    trusted_subs = as_list(conditions.get("token.actions.githubusercontent.com:sub"))

    print(f"trusts      : {provider_arn}")
    print(f"  aud       : {trusted_auds}")
    print("  sub       :")
    for sub in trusted_subs:
        print(f"              {sub}")

    # ---- 2. the provider itself -------------------------------------------
    provider = json.loads(aws(
        ["iam", "get-open-id-connect-provider",
         "--open-id-connect-provider-arn", provider_arn, "--output", "json"],
        profile=args.profile,
    ))
    client_ids = provider.get("ClientIDList", [])
    print(f"\nprovider url: {provider.get('Url')}")
    print(f"  audiences : {client_ids or '(none)'}")

    if provider.get("Url") != "token.actions.githubusercontent.com":
        problems.append(
            f"The provider URL is {provider.get('Url')!r}, not GitHub's. The role "
            "trusts the wrong identity provider entirely."
        )

    for aud in trusted_auds:
        if aud not in client_ids:
            problems.append(
                f"The provider does NOT list {aud!r} as an audience (it has "
                f"{client_ids}). STS rejects a token whose audience the provider "
                "was never configured to accept, before the trust policy is even "
                "consulted.\n"
                "    Fix (additive, safe for anything else using this provider):\n"
                "      aws iam add-client-id-to-open-id-connect-provider \\n"
                f"        --open-id-connect-provider-arn {provider_arn} \\n"
                f"        --client-id {aud}"
            )

    # ---- 3. what GitHub actually presented --------------------------------
    # Deliberately NOT derived from the trust policy. An earlier version of this
    # script rebuilt the "expected" sub out of the trust policy's own sub and
    # compared the two, which is circular -- it always agreed, and it reported
    # all-clear on a role that had never once been assumed.
    # The outcome column is the result recorded at the time of that attempt.
    # After a fix it will read "[ok] ... -> AccessDenied": the claim is
    # trusted now, but that historical attempt still failed. Only a fresh
    # run clears it.
    print('\nsubs GitHub presented (CloudTrail, 90d; outcome is historical):')
    actual = observed_subs(role_arn, args.region, args.profile)
    if not actual:
        print("  (none -- this role has never been attempted)")
    for sub, outcome in actual:
        mark = "ok " if sub in trusted_subs else "MISS"
        print(f"  [{mark}] {sub}   -> {outcome}")

    for sub, outcome in actual:
        if sub not in trusted_subs:
            problems.append(
                f"GitHub presented sub={sub!r} and the trust policy does not "
                f"list it (result: {outcome}).\n"
                "    If that value carries @<numbers>, GitHub is issuing "
                "immutable subject claims -- name@id rather than name -- and the\n"
                "    trust policy needs the id form. infra/04-access.yaml takes "
                "GithubOwnerId and GithubRepoId for exactly this; set them and\n"
                "      python scripts/deploy.py --only access"
            )

    # ---- 4. the workflow's hardcoded ARN -----------------------------------
    wf_arn = None
    if WORKFLOW.exists():
        found = re.search(r"AWS_DEPLOY_ROLE_ARN:\s*(\S+)", WORKFLOW.read_text(encoding="utf-8"))
        wf_arn = found.group(1) if found else None
    print(f"\nworkflow arn: {wf_arn}")
    if wf_arn and wf_arn != role_arn:
        problems.append(
            f"The workflow assumes {wf_arn}, but the access stack deployed "
            f"{role_arn}. The hardcoded ARN in deploy.yml is stale."
        )

    print()
    if problems:
        print("=== PROBLEMS ===\n")
        for i, problem in enumerate(problems, 1):
            print(f"{i}. {problem}\n")
        sys.exit(1)

    if not actual:
        print("Nothing is provably wrong, but nothing has been proven right")
        print("either -- no attempt reached STS. Push to the trusted branch and")
        print("re-run this.")
        return

    print("Trust policy matches what GitHub actually presented.")


if __name__ == "__main__":
    main()
