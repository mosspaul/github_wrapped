"""
Claude via Amazon Bedrock, using boto3's bedrock-runtime client.

Auth is pure IAM -- the Lambda's execution role carries bedrock:InvokeModel, so
there is no API key and nothing in Secrets Manager for this.

WHY boto3 AND NOT THE `anthropic` SDK
-------------------------------------
The anthropic SDK is the nicer API, and it was the first thing tried here. Its
HTTP transport (httpx) cannot open a socket inside this Lambda runtime -- every
request dies with:

    APIConnectionError -> ConnectError(OSError(16, 'Device or resource busy'))

The same request from botocore (the `aws bedrock-runtime invoke-model` CLI)
reaches AWS and returns a normal API response, so it is the HTTP client, not
the network, credentials, or the endpoint. boto3 is already in the Lambda
runtime and its transport is known-good here.

Two side benefits: this bundle drops from ~46MB to ~30KB, and it no longer
pulls a second copy of boto3 in as an `anthropic[bedrock]` dependency.

If you want the SDK back, first prove a plain httpx request works from inside
a deployed Lambda -- don't assume the runtime changed.
"""

import json
import os

import boto3
from botocore.config import Config

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-6-v1")
REGION = os.environ.get("BEDROCK_REGION") or os.environ["AWS_REGION"]

# Module scope so the client and its connection pool survive warm invocations.
client = boto3.client(
    "bedrock-runtime",
    region_name=REGION,
    config=Config(
        # Generating a slide with adaptive thinking is slow; the default 60s
        # read timeout will cut it off mid-response.
        read_timeout=240,
        connect_timeout=10,
        retries={"max_attempts": 3, "mode": "adaptive"},
    ),
)

# Bedrock's own versioning of the Anthropic Messages API request shape. It is
# not the model version and does not change when the model does.
ANTHROPIC_VERSION = "bedrock-2023-05-31"


class BedrockError(RuntimeError):
    pass


def complete(system: str, prompt: str, max_tokens: int = 16000) -> str:
    """
    One request, returning the concatenated text output.

    Streams the response: at max_tokens this large a single buffered response
    can exceed the read timeout, and streaming also lets us drop thinking
    deltas as they arrive instead of holding the whole message in memory.
    """
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        # Adaptive lets the model decide how hard to think per slide -- a
        # language breakdown is trivial, a "coding personality" is not.
        "thinking": {"type": "adaptive"},
    }

    try:
        response = client.invoke_model_with_response_stream(
            modelId=MODEL_ID,
            body=json.dumps(body),
        )
    except client.exceptions.AccessDeniedException as exc:
        # By far the most common first-run failure, and the raw message does
        # not say which of the two causes applies.
        raise BedrockError(
            f"Bedrock denied access to {MODEL_ID} in {REGION}. Either model "
            "access is not enabled for this account/region in the Bedrock "
            "console, or the AWS account is still under new-account "
            f"verification. Original error: {exc}"
        ) from exc

    chunks: list[str] = []
    usage: dict = {}

    for event in response["body"]:
        if "chunk" not in event:
            continue
        payload = json.loads(event["chunk"]["bytes"])
        kind = payload.get("type")

        if kind == "content_block_delta":
            delta = payload.get("delta", {})
            # thinking_deltas also arrive here; we only want the answer text.
            if delta.get("type") == "text_delta":
                chunks.append(delta.get("text", ""))

        elif kind == "message_delta":
            usage = payload.get("usage", usage)
            stop = payload.get("delta", {}).get("stop_reason")
            if stop == "max_tokens":
                print(f"WARNING: hit max_tokens ({max_tokens}); output is truncated")

        elif kind == "error":
            raise BedrockError(f"stream error: {payload}")

    print(f"bedrock usage: {usage} model={MODEL_ID}")

    text = "".join(chunks).strip()
    if not text:
        raise BedrockError("model returned no text content")
    return text
