#!/usr/bin/env python3

import os
import sys
import json
import math
import textwrap
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple

USER_AGENT = "Mozilla/5.0"

# GitHub contribution colors:
# Keep the real GitHub intensity scale and overlay the Spider-Man layer on top.
PALETTE_LIGHT = {
    "bg": "#0d1117",
    "empty": "#161b22",
    "level1": "#0e4429",
    "level2": "#006d32",
    "level3": "#26a641",
    "level4": "#39d353",
    "web": "#f3f6fb",
    "web_soft": "#b5c1cf",
    "red": "#ff1e27",
    "red_dark": "#bd1018",
    "shadow": "#111111",
}

PALETTE_DARK = {
    "bg": "#0d1117",
    "empty": "#161b22",
    "level1": "#0e4429",
    "level2": "#006d32",
    "level3": "#26a641",
    "level4": "#39d353",
    "web": "#f3f6fb",
    "web_soft": "#b5c1cf",
    "red": "#ff1e27",
    "red_dark": "#bd1018",
    "shadow": "#111111",
}

def gql_query(query: str, token: str) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_contribution_data(username: str) -> List[List[int]]:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        end = datetime.now(timezone.utc)
        start = end - timedelta(weeks=52)
        query = """
        query($user: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $user) {
            contributionsCollection(from: $from, to: $to) {
              contributionCalendar {
                weeks {
                  contributionDays {
                    contributionCount
                    date
                  }
                }
              }
            }
          }
        }
        """
        payload = gql_query(query.replace("$user", '"%s"' % username).replace("$from", '"%s"' % start.isoformat()).replace("$to", '"%s"' % end.isoformat()), token)
        weeks = payload.get("data", {}).get("user", {}).get("contributionsCollection", {}).get("contributionCalendar", {}).get("weeks", [])
        grid = []
        for week in weeks:
            row = []
            for day in week.get("contributionDays", []):
                row.append(int(day.get("contributionCount", 0) or 0))
            grid.append(row)
        if grid:
            return grid
    raise RuntimeError("No contribution data returned. Ensure GITHUB_TOKEN is available or use a token-enabled run.")

def contribution_intensity(v: int) -> str:
    if v <= 0:
        return "empty"
    if v <= 3:
        return "level1"
    if v <= 6:
        return "level2"
    if v <= 12:
        return "level3"
    return "level4"

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def svg_rect(x: float, y: float, w: float, h: float, fill: str, stroke: str = "none", stroke_width: str = "0", opacity: str = "1.0") -> str:
    return f'<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" fill=\"{fill}\" stroke=\"{stroke}\" stroke-width=\"{stroke_width}\" opacity=\"{opacity}\"/>'

def svg_circle(cx: float, cy: float, r: float, fill: str, stroke: str = "none", stroke_width: str = "0", opacity: str = "1.0") -> str:
    return f'<circle cx=\"{cx}\" cy=\"{cy}\" r=\"{r}\" fill=\"{fill}\" stroke=\"{stroke}\" stroke-width=\"{stroke_width}\" opacity=\"{opacity}\"/>'

def svg_line(x1: float, y1: float, x2: float, y2: float, stroke: str, stroke_width: str = "1", opacity: str = "1.0", dash: str = "") -> str:
    dash_attr = f' stroke-dasharray=\"{dash}\"' if dash else ""
    return f'<line x1=\"{x1}\" y1=\"{y1}\" x2=\"{x2}\" y2=\"{y2}\" stroke=\"{stroke}\" stroke-width=\"{stroke_width}\" opacity=\"{opacity}\"{dash_attr}/>'


def build_grid(grid: List[List[int]], palette: Dict[str, str]) -> str:
    # A 52-week GitHub-like contribution graph.
    # Each week is a column, each day is a row.
    cell = 10
    gap = 2
    left = 28
    top = 38
    out = []

    for week_index, week in enumerate(grid):
        for day_index, value in enumerate(week):
            x = left + week_index * (cell + gap)
            y = top + day_index * (cell + gap)
            fill = {
                "empty": palette["empty"],
                "level1": palette["level1"],
                "level2": palette["level2"],
                "level3": palette["level3"],
                "level4": palette["level4"],
            }[contribution_intensity(value)]
            out.append(svg_rect(x, y, cell, cell, fill, palette["bg"], "0.35"))

    return "\n".join(out)

def build_spider_sprite(x: float, y: float, palette: Dict[str, str], phase: float = 0.0) -> str:
    # Small retro pixel-art Spider-Man silhouette.
    # Not a copied external sprite; a custom minimal original build.
    swing = math.sin(phase)
    head_y = y + swing * 2
    body_y = y + 8 + swing * 2
    arm_lift = swing * 2

    parts = []
    # Head
    parts.append(svg_circle(x, head_y, 4.5, palette["red"]))
    parts.append(svg_circle(x - 1.5, head_y - 0.6, 0.9, palette["shadow"]))
    parts.append(svg_circle(x + 1.5, head_y - 0.6, 0.9, palette["shadow"]))
    parts.append(svg_rect(x - 3.5, head_y - 1.5, 7, 2.5, palette["red"]))

    # Body
    parts.append(svg_rect(x - 3.5, body_y - 2, 7, 9, palette["red"], palette["red_dark"], "0.8"))
    # black limbs
    parts.append(svg_rect(x - 4.5, body_y + 2, 2, 5, palette["shadow"]))
    parts.append(svg_rect(x + 2.5, body_y + 2, 2, 5, palette["shadow"]))
    # legs
    parts.append(svg_rect(x - 2.5, body_y + 6, 2.5, 4.5, palette["red"]))
    parts.append(svg_rect(x + 0.5, body_y + 6, 2.5, 4.5, palette["red"]))

    # Web shooting hand
    parts.append(svg_line(x + 5, body_y + 1, x + 12, body_y - 2 + arm_lift, palette["web"], "1.1", "1.0"))
    parts.append(svg_line(x + 5, body_y + 1, x + 12, body_y + 3 + arm_lift, palette["web"], "1.1", "1.0"))

    return "\n".join(parts)

def build_web_arc(x1: float, y1: float, x2: float, y2: float, opacity: float = 0.9) -> str:
    # Thin web line with a tiny impact pulse.
    return svg_line(x1, y1, x2, y2, "#f3f6fb", "0.9", str(opacity), "2 2")

def build_spider_animation(grid: List[List[int]], palette: Dict[str, str]) -> str:
    # Deterministic loop path across the contribution graph.
    # Keep the motion lightweight and readable.
    parts = []

    # Web swings as a sequence across the grid.
    # The exact positions can be computed from the contribution graph layout.
    start_x = 42
    start_y = 62
    end_x = 42 + len(grid) * 12 + 50
    end_y = 68

    # A small set of deterministic swing anchors
    anchors = []
    for i in range(0, len(grid), 12):
        anchors.append((start_x + i * 10, 52 + ((i % 5) * 4) - 8))

    # Draw a few swinging paths and moving spider
    for idx, (ax, ay) in enumerate(anchors):
        bx = ax + 26 + idx * 3
        by = ay - 8 + ((idx % 2) * 5)
        parts.append(build_web_arc(ax, ay, bx, by, 0.75))
        parts.append(svg_circle(bx, by, 1.3, palette["web"], palette["web"], "0.5", "0.7"))
        # A tiny web impact pulse on some cells
        if idx % 3 == 0:
            parts.append(svg_circle(bx - 2, by + 2, 2.2, "none", palette["web"], "0.6", "0.9"))

    # Add animated spider group
    spider_x = 60
    spider_y = 78
    for phase in [0.0, 0.75, 1.5, 2.25, 3.0]:
        dx = 22 + phase * 22
        dy = 26 + (math.sin(phase) * 6)
        parts.append(
            f'<g opacity=\"{0.9 - phase * 0.12}\">'
            + build_spider_sprite(spider_x + dx, spider_y + dy, palette, phase)
            + "</g>"
        )

    # A final looping swing line
    parts.append(svg_line(40, 60, 150, 42, palette["web"], "1.0", "0.9"))
    parts.append(svg_line(150, 42, 250, 72, palette["web"], "1.0", "0.8"))
    parts.append(svg_line(250, 72, 340, 48, palette["web"], "1.0", "0.85"))

    return "\n".join(parts)

def build_svg(grid: List[List[int]], palette: Dict[str, str]) -> str:
    width = 52 * 12 + 80
    height = 56 + 7 * 12 + 26
    grid_svg = build_grid(grid, palette)
    spider_svg = build_spider_animation(grid, palette)

    css = """
    <style>
      .pulse {
        animation: pulse 3s ease-in-out infinite;
      }
      @keyframes pulse {
        0%, 100% { opacity: 0.4; }
        50% { opacity: 1; }
      }
      .web {
        opacity: 0.9;
      }
    </style>
    """.strip()

    return f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  {css}
  <rect width="{width}" height="{height}" fill="{palette['bg']}"/>
  <text x="26" y="22" fill="{palette['web']}" font-family="monospace" font-size="12" font-weight="700">GitHub Activity</text>
  <g>
    {grid_svg}
  </g>
  <g class="pulse">
    {spider_svg}
  </g>
</svg>
""".strip()

def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/generate_spiderman_activity.py <github-user> <output-light> <output-dark>")
        sys.exit(1)

    username = sys.argv[1]
    out_light = sys.argv[2]
    out_dark = sys.argv[3]

    try:
        grid = fetch_contribution_data(username)
    except Exception as exc:
        print(f"Warning: could not load contribution data: {exc}")
        grid = [[0 for _ in range(7)] for _ in range(52)]
        # Fallback to a deterministic low-activity graph so the page still renders
        for i, row in enumerate(grid):
            for j in range(7):
                if (i + j) % 11 == 0:
                    row[j] = (i * 2 + j) % 5

    # Generate light and dark versions
    light_svg = build_svg(grid, PALETTE_LIGHT)
    dark_svg = build_svg(grid, PALETTE_DARK)

    for path, content in [(out_light, light_svg), (out_dark, dark_svg)]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    print(f"Generated {out_light} and {out_dark}")

if __name__ == "__main__":
    main()
