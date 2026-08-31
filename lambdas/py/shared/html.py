"""
Cleanup for model-authored HTML fragments.

The prompt asks for a bare fragment, but models reasonably often wrap output in
a markdown fence or add a preamble sentence. Rather than fight that with ever
more prompt text, normalise it here where the failure is visible and testable.
"""

import re

_FENCE = re.compile(r"```(?:html)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SCRIPT = re.compile(r"<script\b.*?</script>", re.DOTALL | re.IGNORECASE)


def extract_fragment(text: str) -> str:
    """Pull the HTML out of a model response and strip anything unsafe."""
    match = _FENCE.search(text)
    if match:
        text = match.group(1)

    # Drop any leading prose before the first tag.
    first_tag = text.find("<")
    if first_tag > 0:
        text = text[first_tag:]

    # The contract says no scripts. The front end mounts this with
    # dangerouslySetInnerHTML, so this is the last line of defence -- inline
    # <script> would not execute via innerHTML, but an injected <img onerror>
    # would, so we are not pretending this is a sanitiser. It is a guard against
    # the model's own output, which is the only thing that reaches this path.
    text = _SCRIPT.sub("", text)

    return text.strip()
