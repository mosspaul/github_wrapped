"""Slide type definitions, loaded from the shared JSON contract."""

import json
import pathlib

# scripts/build.py copies shared/slide-types.json in next to this module so the
# same file is the source of truth for both the Python and TypeScript sides.
_PATH = pathlib.Path(__file__).with_name("slide-types.json")

_data = json.loads(_PATH.read_text(encoding="utf-8"))

SLIDE_TYPES: list[dict] = _data["slideTypes"]
SLIDE_IDS: list[str] = [s["id"] for s in SLIDE_TYPES]
SLIDE_BY_ID: dict[str, dict] = {s["id"]: s for s in SLIDE_TYPES}
