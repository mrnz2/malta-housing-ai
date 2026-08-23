"""Serve a lightweight Malta outline SVG for the locality tooltip.

The repo `map.svg` is an Adobe Illustrator export with a large PGF blob.
The tooltip only needs the sea fill + island silhouettes.
"""

from __future__ import annotations

import re
from functools import lru_cache

from malta_housing.paths import PROJECT_ROOT

MAP_SVG_PATH = PROJECT_ROOT / "map.svg"
FALLBACK_VIEWBOX = "0 0 800 600"


def _extract_group(svg_text: str, group_id: str) -> str:
    start = svg_text.find(f'<g id="{group_id}">')
    if start < 0:
        return ""
    depth = 0
    i = start
    while i < len(svg_text):
        next_open = svg_text.find("<g", i)
        next_close = svg_text.find("</g>", i)
        if next_close < 0:
            break
        if next_open >= 0 and next_open < next_close:
            depth += 1
            i = next_open + 2
            continue
        depth -= 1
        i = next_close + 4
        if depth == 0:
            return svg_text[start:i]
    return ""


@lru_cache(maxsize=1)
def cleaned_map_svg() -> bytes | None:
    if not MAP_SVG_PATH.is_file():
        return None
    text = MAP_SVG_PATH.read_text(encoding="utf-8", errors="ignore")
    vb_match = re.search(r'viewBox="([^"]+)"', text)
    viewbox = vb_match.group(1).strip() if vb_match else FALLBACK_VIEWBOX
    parts = viewbox.replace(",", " ").split()
    try:
        width = parts[2]
        height = parts[3]
    except IndexError:
        width, height = "800", "600"
        viewbox = FALLBACK_VIEWBOX

    islands = _extract_group(text, "islands")
    if not islands:
        return None

    # Compact path data for faster parse in the tooltip.
    islands = re.sub(r"\s+", " ", islands).strip()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
        f'width="{width}" height="{height}" role="img" aria-hidden="true">'
        f'<rect fill="#C6ECFF" width="{width}" height="{height}"/>'
        f"{islands}"
        f"</svg>\n"
    )
    return svg.encode("utf-8")
