"""
Claude via Amazon Bedrock.

Auth is pure IAM -- the Lambda's execution role carries bedrock:InvokeModel, so
there is no API key and nothing in Secrets Manager for this. If calls fail with
AccessDeniedException, the usual cause is that model access has not been
enabled by hand in the Bedrock console for this region.
"""

import os

from anthropic import AnthropicBedrockMantle

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-opus-5")
REGION = os.environ.get("BEDROCK_REGION") or os.environ["AWS_REGION"]

# Module scope so the client and its connection pool survive warm invocations.
client = AnthropicBedrockMantle(aws_region=REGION)


def complete(system: str, prompt: str, max_tokens: int = 16000) -> str:
    """
    One request, returning the concatenated text output.

    Streaming is used because max_tokens is high enough that a non-streaming
    request can hit the SDK's HTTP timeout. get_final_message() collapses the
    stream back to a single response once it completes.
    """
    with client.messages.stream(
        model=MODEL_ID,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        # Adaptive lets the model decide how much to think per slide; a
        # languages breakdown is trivial, a "coding personality" is not.
        thinking={"type": "adaptive"},
    ) as stream:
        message = stream.get_final_message()

    print(
        f"bedrock usage: in={message.usage.input_tokens} "
        f"out={message.usage.output_tokens} model={MODEL_ID}"
    )

    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
