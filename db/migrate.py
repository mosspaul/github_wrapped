"""
Apply db/schema.sql to the Aurora cluster over the RDS Data API.

    python db/migrate.py
    python db/migrate.py --stage dev --dry-run

Safe to run as often as you like -- every statement in schema.sql is written to
be idempotent. This is the command to run after you edit the schema; you do not
need to redeploy CloudFormation.

Needs no MySQL client and no VPC access: the Data API is plain HTTPS + IAM.
"""

import argparse
import pathlib
import re
import sys

import boto3

SCHEMA = pathlib.Path(__file__).with_name("schema.sql")


def split_statements(sql_text: str) -> list[str]:
    """
    Split a script into individual statements.

    The Data API executes exactly one statement per call -- sending a script
    with semicolons fails. Line comments are stripped first so a ';' inside a
    comment cannot split a statement in half.
    """
    without_comments = re.sub(r"--[^\n]*", "", sql_text)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", default="dev")
    p.add_argument("--profile", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--dry-run", action="store_true", help="print statements, run nothing")
    args = p.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cfn = session.client("cloudformation")

    stack = f"gh-wrapped-{args.stage}-data"
    try:
        outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    except Exception as exc:
        sys.exit(f"could not read stack {stack}: {exc}\nHas the data stack been deployed?")

    out = {o["OutputKey"]: o["OutputValue"] for o in outputs}
    cluster_arn = out["DbClusterArn"]
    secret_arn = out["DbSecretArn"]
    database = out["DbName"]

    statements = split_statements(SCHEMA.read_text(encoding="utf-8"))
    print(f"{len(statements)} statement(s) from {SCHEMA.name} -> {database}\n")

    if args.dry_run:
        for i, stmt in enumerate(statements, 1):
            print(f"--- {i} ---\n{stmt}\n")
        return

    rds = session.client("rds-data")

    for i, stmt in enumerate(statements, 1):
        label = " ".join(stmt.split())[:70]
        print(f"[{i}/{len(statements)}] {label}...")
        try:
            rds.execute_statement(
                resourceArn=cluster_arn,
                secretArn=secret_arn,
                database=database,
                sql=stmt,
            )
        except rds.exceptions.BadRequestException as exc:
            # The most common first-run failure by a wide margin, and the AWS
            # message does not mention the actual cause.
            if "HttpEndpoint" in str(exc) or "not enabled" in str(exc):
                sys.exit(
                    f"\nData API is not enabled on {cluster_arn}.\n"
                    "Check EnableHttpEndpoint: true in infra/01-data.yaml, "
                    "then redeploy the data stack."
                )
            sys.exit(f"\nstatement {i} failed:\n{stmt}\n\n{exc}")

    print(f"\nschema applied to {database}")

    tables = rds.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql="SHOW TABLES",
        formatRecordsAs="JSON",
    )
    print(f"tables now: {tables.get('formattedRecords')}")


if __name__ == "__main__":
    main()
