#!/usr/bin/env python3
"""
Spider-Man Web Swing Activity Graph Generator
Converts GitHub contribution data into a pixel-art Spider-Man web-swinging animation.
"""

import os
import sys
import json
import math
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import List, Dict

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# GitHub contribution color palettes
COLORS_DARK = {
    "bg": "#0d1117",
    "empty": "#161b22",
    "level1": "#0e4429",
    "level2": "#006d32",
    "level3": "#26a641",
    "level4": "#39d353",
    "web": "#f3f6fb",
    "red": "#ff1e27",
    "red_dark": "#bd1018",
    "shadow": "#111111",
}

COLORS_LIGHT = {
    "bg": "#ffffff",
    "empty": "#ebedf0",
    "level1": "#c6e48b",
    "level2": "#7ee787",
    "level3": "#30a14e",
    "level4": "#216e39",
    "web": "#333333",
    "red": "#ff1e27",
    "red_dark": "#bd1018",
    "shadow": "#111111",
}


def fetch_contribution_data(username: str, token: str = None) -> List[List[int]]:
    """Fetch GitHub contribution calendar using GraphQL API."""
    if not token:
        token = os.environ.get("GITHUB_TOKEN")
    
    if not token:
        print("Warning: GITHUB_TOKEN not found, using fallback data")
        return generate_fallback_data()

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
    
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=364)  # 52 weeks
    
    payload = {
        "query": query,
        "variables": {
            "user": username,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
    }
    
    try:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        
        if "errors" in result:
            print(f"GraphQL error: {result['errors']}")
            return generate_fallback_data()
        
        weeks = (
            result.get("data", {})
            .get("user", {})
            .get("contributionsCollection", {})
            .get("contributionCalendar", {})
            .get("weeks", [])
        )
        
        if not weeks:
            print("No contribution data returned")
            return generate_fallback_data()
        
        grid = []
        for week in weeks:
            week_data = []
            for day in week.get("contributionDays", []):
                count = int(day.get("contributionCount", 0) or 0)
                week_data.append(count)
            if week_data:
                grid.append(week_data)
        
        return grid if grid else generate_fallback_data()
    
    except Exception as e:
        print(f"Error fetching data: {e}")
        return generate_fallback_data()


def generate_fallback_data() -> List[List[int]]:
    """Generate deterministic fallback contribution data."""
    grid = []
    for week in range(52):
        week_data = []
        for day in range(7):
            # Deterministic pattern based on week/day indices
            value = ((week * 7 + day) * 13) % 25
            week_data.append(value)
        grid.append(week_data)
    return grid


def get_color_for_level(level: int, palette: Dict[str, str]) -> str:
    """Map contribution level to color."""
    colors = [
        palette["empty"],
        palette["level1"],
        palette["level2"],
        palette["level3"],
        palette["level4"],
    ]
    return colors[min(level, 4)]


def contribution_level(count: int) -> int:
    """Map contribution count to intensity level (0-4)."""
    if count == 0:
        return 0
    elif count <= 3:
        return 1
    elif count <= 6:
        return 2
    elif count <= 12:
        return 3
    else:
        return 4


def build_grid_svg(grid: List[List[int]], palette: Dict[str, str]) -> str:
    """Build SVG for the contribution grid."""
    cell_size = 10
    gap = 2
    start_x = 28
    start_y = 38
    
    svg_parts = []
    
    for week_idx, week in enumerate(grid):
        for day_idx, count in enumerate(week):
            x = start_x + week_idx * (cell_size + gap)
            y = start_y + day_idx * (cell_size + gap)
            level = contribution_level(count)
            color = get_color_for_level(level, palette)
            
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                f'fill="{color}" stroke="{palette["bg"]}" stroke-width="0.35"/>'
            )
    
    return "\n".join(svg_parts)


def build_spiderman_svg(grid: List[List[int]], palette: Dict[str, str]) -> str:
    """Build animated Spider-Man overlay."""
    svg_parts = []
    
    # Calculate grid dimensions
    num_weeks = len(grid)
    cell_size = 10
    gap = 2
    start_x = 28
    start_y = 38
    
    # Create multiple spider positions for animation effect
    positions = []
    for i in range(0, num_weeks, 8):
        week_idx = i
        day_idx = (i * 2) % 7
        x = start_x + week_idx * (cell_size + gap) + cell_size / 2
        y = start_y + day_idx * (cell_size + gap) + cell_size / 2
        positions.append((x, y, i))
    
    # Draw spider at each position with decreasing opacity
    for idx, (x, y, pos_idx) in enumerate(positions):
        opacity = 1.0 - (idx * 0.15)
        if opacity <= 0:
            break
        
        # Simple pixel-art spider
        svg_parts.append(f'<g opacity="{opacity}">')
        
        # Head
        svg_parts.append(
            f'<circle cx="{x}" cy="{y - 5}" r="4.5" fill="{palette["red"]}"/>'
        )
        
        # Eyes
        svg_parts.append(
            f'<circle cx="{x - 1.5}" cy="{y - 6.5}" r="0.9" fill="{palette["shadow"]}"/>'
        )
        svg_parts.append(
            f'<circle cx="{x + 1.5}" cy="{y - 6.5}" r="0.9" fill="{palette["shadow"]}"/>'
        )
        
        # Body
        svg_parts.append(
            f'<rect x="{x - 3.5}" y="{y - 2}" width="7" height="9" '
            f'fill="{palette["red"]}" stroke="{palette["red_dark"]}" stroke-width="0.8"/>'
        )
        
        # Legs
        leg_positions = [
            (x - 3, y + 4),
            (x + 3, y + 4),
            (x - 2, y + 7),
            (x + 2, y + 7),
        ]
        
        for leg_x, leg_y in leg_positions:
            svg_parts.append(
                f'<line x1="{leg_x}" y1="{y + 2}" x2="{leg_x}" y2="{leg_y}" '
                f'stroke="{palette["red"]}" stroke-width="0.8"/>'
            )
        
        # Web strand
        if idx < len(positions) - 1:
            next_x, next_y, _ = positions[idx + 1]
            svg_parts.append(
                f'<line x1="{x}" y1="{y}" x2="{next_x}" y2="{next_y}" '
                f'stroke="{palette["web"]}" stroke-width="0.8" opacity="0.8"/>'
            )
        
        svg_parts.append("</g>")
    
    # Add some web impact circles
    for idx, (x, y, _) in enumerate(positions[::2]):
        opacity = 0.6 - (idx * 0.1)
        if opacity > 0:
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="2.5" fill="none" '
                f'stroke="{palette["web"]}" stroke-width="0.6" opacity="{opacity}"/>'
            )
    
    return "\n".join(svg_parts)


def build_svg(grid: List[List[int]], palette: Dict[str, str]) -> str:
    """Build complete SVG document."""
    width = len(grid) * 12 + 80
    height = 120
    
    grid_svg = build_grid_svg(grid, palette)
    spider_svg = build_spiderman_svg(grid, palette)
    
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="auto">
  <style>
    .pulse {{ animation: pulse 4s ease-in-out infinite; }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 0.7; }}
      50% {{ opacity: 1; }}
    }}
  </style>
  <rect width="{width}" height="{height}" fill="{palette['bg']}"/>
  <text x="26" y="22" fill="{palette['web']}" font-family="'Courier New', monospace" font-size="12" font-weight="700">GitHub Activity</text>
  <g id="contribution-grid">
{grid_svg}
  </g>
  <g id="spider-animation" class="pulse">
{spider_svg}
  </g>
</svg>"""
    
    return svg


def main():
    if len(sys.argv) < 4:
        print("Usage: python scripts/generate_spiderman_activity.py <github-user> <output-light> <output-dark>")
        sys.exit(1)

    username = sys.argv[1]
    out_light = sys.argv[2]
    out_dark = sys.argv[3]

    print(f"Fetching contribution data for {username}...")
    grid = fetch_contribution_data(username)

    print(f"Generating SVGs (grid: {len(grid)}x{len(grid[0]) if grid else 0})...")

    # Generate light and dark versions
    light_svg = build_svg(grid, COLORS_LIGHT)
    dark_svg = build_svg(grid, COLORS_DARK)

    # Write outputs
    for path, content in [(out_light, light_svg), (out_dark, dark_svg)]:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✓ Generated {path}")

    print("Done!")


if __name__ == "__main__":
    main()
