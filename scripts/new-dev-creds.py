"""
Create an IAM user for a collaborator and print their credentials once.

    python scripts/new-dev-creds.py paul

Run this as the account owner. It creates the user, puts them in the
gh-wrapped-<stage>-developers group (whose permissions are defined in
infra/04-access.yaml), issues an access key, and sets a console password.

The secret access key is shown ONCE and cannot be retrieved again -- AWS does
not store it. Send the output over a private channel, never in the repo or a
group chat.

Deliberately not CloudFormation: AWS::IAM::AccessKey writes the secret into
stack outputs, where anyone with console read can see it and where it lives in
CloudFormation state indefinitely.
"""

import argparse
import secrets
import string
import sys

import boto3
from botocore.exceptions import ClientError


def temp_password() -> str:
    """Meets the default IAM password policy without any ambiguous characters."""
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(20))
    return f"GhW!{body}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("username")
    p.add_argument("--stage", default="dev")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--no-console", action="store_true",
                   help="skip console access (they lose the RDS Query Editor)")
    args = p.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    iam = session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]
    group = f"gh-wrapped-{args.stage}-developers"

    try:
        iam.get_group(GroupName=group)
    except ClientError:
        sys.exit(
            f"group {group} does not exist.\n"
            f"Deploy it first: python scripts/deploy.py --only access --stage {args.stage}"
        )

    try:
        iam.create_user(UserName=args.username)
        print(f"created user {args.username}")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"user {args.username} already exists, reusing it")

    iam.add_user_to_group(GroupName=group, UserName=args.username)

    # An IAM user is capped at two access keys; clearing old ones keeps repeat
    # runs of this script from failing with LimitExceeded.
    for key in iam.list_access_keys(UserName=args.username)["AccessKeyMetadata"]:
        iam.delete_access_key(UserName=args.username, AccessKeyId=key["AccessKeyId"])
        print(f"revoked previous key {key['AccessKeyId']}")

    key = iam.create_access_key(UserName=args.username)["AccessKey"]

    password = None
    if not args.no_console:
        password = temp_password()
        try:
            iam.create_login_profile(
                UserName=args.username,
                Password=password,
                PasswordResetRequired=True,
            )
        except iam.exceptions.EntityAlreadyExistsException:
            iam.update_login_profile(
                UserName=args.username,
                Password=password,
                PasswordResetRequired=True,
            )

    region = session.region_name or "us-east-1"

    print("\n" + "=" * 68)
    print(f"  SEND THIS TO {args.username} PRIVATELY -- shown only once")
    print("=" * 68)
    print(f"""
Run this once:

    aws configure --profile gh-wrapped

      AWS Access Key ID:     {key['AccessKeyId']}
      AWS Secret Access Key: {key['SecretAccessKey']}
      Default region name:   {region}
      Default output format: json

Then everything takes --profile gh-wrapped, or set it for the session:

    export AWS_PROFILE=gh-wrapped        # bash
    $env:AWS_PROFILE = "gh-wrapped"      # powershell

Check it works:

    aws sts get-caller-identity
    python scripts/push-fn.py compute-stats
""")

    if password:
        print(f"""Console (needed for the RDS Query Editor):

    https://{account}.signin.aws.amazon.com/console
      Account:  {account}
      Username: {args.username}
      Password: {password}   (you will be asked to change it)
""")

    print("=" * 68)
    print("\nWhen the hackathon ends, revoke with:")
    print(f"  aws iam delete-access-key --user-name {args.username} --access-key-id {key['AccessKeyId']}")


if __name__ == "__main__":
    main()
