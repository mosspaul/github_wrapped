"""
Pipeline step 3: ask Claude for one self-contained HTML fragment per slide.

This is the last step, so it sets status='ready' and chains to nothing.
"""

import json

from shared import db
from shared.ai import complete
from shared.html import extract_fragment
from shared.pipeline import step
from shared.slides import SLIDE_BY_ID, SLIDE_IDS

SYSTEM = """You design single slides for "GitHub Wrapped", a Spotify-Wrapped-style \
recap of a developer's year on GitHub.

Return ONE self-contained HTML fragment and nothing else. No explanation, no \
markdown fences, no <html>, <head> or <body>.

Hard requirements:
- Exactly one root element.
- All styling inline via style="..." or in a single <style> inside the fragment. \
Scope every selector under your root element's class so slides cannot bleed into \
each other.
- No <script>, no external URLs, no fonts or images fetched from the network. \
Use system font stacks and CSS-drawn shapes.
- Must look right at 400x700 (a portrait phone screen) on a dark background.
- Big, confident typography. One striking number or phrase should dominate.
- Use the real values from the supplied stats. Never invent numbers.

If a stat is null or the payload is marked "placeholder": true, design around \
what IS present -- say something playful about the missing data rather than \
fabricating it."""


@step("generating", chain=False)
def handler(handle: str) -> dict:
    rows = db.sql(
        "SELECT slide_type, stats_json FROM slides WHERE handle = :handle",
        {"handle": handle},
    )
    stats_by_type = {r["slide_type"]: r["stats_json"] for r in rows}

    generated = 0
    failed = []

    for slide_id in SLIDE_IDS:
        meta = SLIDE_BY_ID[slide_id]
        stats = stats_by_type.get(slide_id) or "{}"

        prompt = (
            f"Slide: {meta['title']}\n"
            f"Purpose: {meta['blurb']}\n"
            f"GitHub handle: {handle}\n\n"
            f"Stats (JSON):\n{_pretty(stats)}\n\n"
            "Design this slide."
        )

        # One slide failing should not lose the other four. A slide with
        # html=NULL is a documented state the front end already handles.
        try:
            html = extract_fragment(complete(SYSTEM, prompt))
            db.sql(
                """
                INSERT INTO slides (handle, slide_type, html)
                VALUES (:handle, :slide_type, :html)
                ON DUPLICATE KEY UPDATE html = VALUES(html)
                """,
                {"handle": handle, "slide_type": slide_id, "html": html},
            )
            generated += 1
        except Exception as exc:
            print(f"slide {slide_id} failed for {handle}: {exc!r}")
            failed.append(slide_id)

    if generated == 0:
        # Nothing rendered at all -- that is a real failure, so let @step record
        # it as status='error' rather than showing an empty deck.
        raise RuntimeError(f"every slide failed to generate: {failed}")

    db.set_status(handle, "ready")
    print(f"generated {generated}/{len(SLIDE_IDS)} slides for {handle}")
    return {"handle": handle, "generated": generated, "failed": failed}


def _pretty(raw: str) -> str:
    """stats_json arrives as a string from MySQL; pretty-print it if we can."""
    try:
        return json.dumps(json.loads(raw), indent=2, default=str)
    except (ValueError, TypeError):
        return str(raw)
