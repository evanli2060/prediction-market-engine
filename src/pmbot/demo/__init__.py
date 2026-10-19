"""Build a self-contained HTML page that replays a simulated run.

python -m pmbot.demo                 # writes demo.html
python -m pmbot.demo --seeds 10      # adds the 10-world comparison (a few minutes)
"""

from __future__ import annotations

import json
from importlib.resources import files

from .build import build

__all__ = ["build", "render"]

_MARKER = "/*__DEMO_DATA__*/null"


def render(data: dict, standalone: bool = True) -> str:
    """Embed `data` in the page template.

    `standalone=True` returns a full HTML document for opening locally.
    `standalone=False` returns the fragment (head content + body content) for
    hosts that supply their own document skeleton.
    """
    template = files("pmbot.demo").joinpath("page.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    if _MARKER not in template:
        raise RuntimeError("demo template is missing its data marker")
    page = template.replace(_MARKER, payload)
    head, body = page.split("<!--BODY-->", 1)
    if not standalone:
        return head + body
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{head}</head>\n<body>\n{body}</body>\n</html>\n"
    )
