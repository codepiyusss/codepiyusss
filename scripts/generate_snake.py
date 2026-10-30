#!/usr/bin/env python3
"""Generate a pixel-art Snake game played on a GitHub contribution graph.

The output is one self-contained, animated SVG (SMIL, no JavaScript, no
external requests). The contribution grid is real data; the snake is a
deterministic bot whose food only ever spawns on days that have contributions.
Nothing here reads or writes anything except the SVG file.

    python scripts/generate_snake.py --user NAME --output dist/snake-contribution-graph.svg

Data source, in order of preference:
    1. GitHub GraphQL API, when GITHUB_TOKEN is set.
    2. The public contributions page of the user (no token needed).
    3. --from-json FILE, a JSON list of ["YYYY-MM-DD", level 0-4] pairs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import heapq
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------
# Theme: the five colors, nothing else.
# --------------------------------------------------------------------------
PEACH = "#ffe9da"
ORANGE = "#ff6300"
RED = "#ff3d00"
NAVY = "#001930"
DARK = "#000e1b"
PALETTE = {PEACH, ORANGE, RED, NAVY, DARK}

# Peach tint laid over a navy cell. Kept well below food brightness so the
# game pieces always read as distinct from contribution intensity.
LEVEL_OPACITY = {1: 0.18, 2: 0.30, 3: 0.44, 4: 0.58}

# --------------------------------------------------------------------------
# Geometry and timing
# --------------------------------------------------------------------------
CELL = 12
GAP = 3
PITCH = CELL + GAP
PAD = 18
ROWS = 7
MAX_COLS = 53

TICK = 0.16          # seconds per grid step
START_LEN = 3
FOOD_COUNT = 10
HOLD_TICKS = 4       # head rests against the wall
FADE_TICKS = 5       # snake fades out once
PAUSE_TICKS = 6      # empty board before the loop restarts

DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # east, south, west, north
EAST = 0
TURN_COST = 0.3      # makes the bot prefer long straight runs

Cell = tuple[int, int]


# --------------------------------------------------------------------------
# Contribution data
# --------------------------------------------------------------------------
@dataclass
class Grid:
    cols: int
    cells: dict[Cell, int]  # (col, row) -> level 0..4; missing = future day


GRAPHQL_QUERY = """query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionLevel } }
      }
    }
  }
}"""

GRAPHQL_LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def fetch_graphql(user: str, token: str) -> dict[dt.date, int]:
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": GRAPHQL_QUERY, "variables": {"login": user}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "snake-contribution-arcade",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("errors") or not (result.get("data") or {}).get("user"):
        raise RuntimeError(f"GraphQL contribution query failed: {result.get('errors')}")
    weeks = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return {
        dt.date.fromisoformat(day["date"]): GRAPHQL_LEVELS[day["contributionLevel"]]
        for week in weeks
        for day in week["contributionDays"]
    }


class _CalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.days: dict[dt.date, int] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "td":
            return
        a = dict(attrs)
        if a.get("data-date") and a.get("data-level") is not None:
            self.days[dt.date.fromisoformat(a["data-date"])] = int(a["data-level"])


def fetch_public_page(user: str) -> dict[dt.date, int]:
    request = urllib.request.Request(
        f"https://github.com/users/{user}/contributions",
        headers={"User-Agent": "snake-contribution-arcade"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    parser = _CalendarParser()
    parser.feed(html)
    if not parser.days:
        raise RuntimeError("No contribution cells found on the public contributions page.")
    return parser.days


def load_days(user: str, from_json: str | None) -> dict[dt.date, int]:
    if from_json:
        with open(from_json, encoding="utf-8") as fh:
            return {dt.date.fromisoformat(d): int(level) for d, level in json.load(fh)}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        try:
            return fetch_graphql(user, token)
        except Exception as exc:  # fall back to the public page
            print(f"GraphQL unavailable ({exc}); using the public contributions page.", file=sys.stderr)
    return fetch_public_page(user)


def build_grid(days: dict[dt.date, int]) -> Grid:
    if not days:
        raise ValueError("No contribution days supplied.")
    first = min(days)
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # Sunday on or before
    cells = {((d - start).days // 7, (d.weekday() + 1) % 7): level for d, level in days.items()}
    cols = max(c for c, _ in cells) + 1
    if cols > MAX_COLS:
        shift = cols - MAX_COLS
        cells = {(c - shift, r): v for (c, r), v in cells.items() if c >= shift}
        cols = MAX_COLS
    return Grid(cols=cols, cells=cells)


# --------------------------------------------------------------------------
# The game: a deterministic bot
# --------------------------------------------------------------------------
@dataclass
class Game:
    grid: Grid
    route: list[Cell]        # head cell at every tick, starting with the lead-in
    lead: int                # lead-in length; head at tick t is route[lead + t]
    ticks: int               # G: head moves before the crash
    eats: list[tuple[int, Cell]]  # (tick eaten, cell), in order
    crashed: bool

    @property
    def length(self) -> int:
        return START_LEN + len(self.eats)

    @property
    def loop_ticks(self) -> int:
        return self.ticks + HOLD_TICKS + FADE_TICKS + PAUSE_TICKS


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def plan(
    body: list[Cell],
    heading: int,
    target: Cell,
    walk: set[Cell],
    goal_dir: int | None = None,
) -> list[Cell] | None:
    """Cheapest path for the head (straight runs preferred, no reversing).

    The tail retreats as the snake moves, so a body cell is only an obstacle
    while the snake still occupies it at that step.
    """
    n = len(body)
    index = {c: j for j, c in enumerate(body)}
    start = (body[0], heading)
    best = {start: 0.0}
    parent: dict[tuple[Cell, int], tuple[Cell, int]] = {}
    heap = [(0.0, 0, 0, body[0], heading)]
    counter = 0
    while heap:
        cost, steps, _, cell, d = heapq.heappop(heap)
        if best.get((cell, d), float("inf")) < cost:
            continue
        if steps > 0 and cell == target and (goal_dir is None or d == goal_dir):
            path, state = [], (cell, d)
            while state != start:
                path.append(state[0])
                state = parent[state]
            path.reverse()
            return path if len(set(path)) == len(path) else None
        for nd in range(4):
            if nd == (d + 2) % 4:
                continue
            nx = (cell[0] + DIRS[nd][0], cell[1] + DIRS[nd][1])
            if nx not in walk:
                continue
            j = index.get(nx)
            if j is not None and j < n - (steps + 1):
                continue  # still occupied by the body at that step
            ncost = cost + 1 + (TURN_COST if nd != d else 0)
            if ncost < best.get((nx, nd), float("inf")):
                best[(nx, nd)] = ncost
                parent[(nx, nd)] = (cell, d)
                counter += 1
                heapq.heappush(heap, (ncost, steps + 1, counter, nx, nd))
    return None


def advance(body: list[Cell], path: list[Cell], grow: bool) -> list[Cell] | None:
    """Move along path; returns the new body or None on a self-collision."""
    body = list(body)
    for i, cell in enumerate(path):
        growing = grow and i == len(path) - 1
        occupied = body if growing else body[:-1]
        if cell in occupied:
            return None
        body.insert(0, cell)
        if not growing:
            body.pop()
    return body


def survivable(body: list[Cell], walk: set[Cell]) -> bool:
    """The head must still be able to reach the tail, with room to spare."""
    occupied = set(body[:-1])
    seen, stack = {body[0]}, [body[0]]
    while stack:
        cx, cy = stack.pop()
        for dx, dy in DIRS:
            nx = (cx + dx, cy + dy)
            if nx in walk and nx not in occupied and nx not in seen:
                seen.add(nx)
                stack.append(nx)
    return body[-1] in seen and len(seen) >= len(body) + 2


def _heading(a: Cell, b: Cell) -> int:
    return DIRS.index((b[0] - a[0], b[1] - a[1]))


def _start_row(walk: set[Cell]) -> int:
    for row in sorted(range(ROWS), key=lambda r: abs(r - ROWS // 2)):
        if all((c, row) in walk for c in range(START_LEN)):
            return row
    raise ValueError("Grid has no free row for the snake to start in.")


def simulate(grid: Grid) -> Game:
    walk = set(grid.cells)
    row = _start_row(walk)
    body = [(START_LEN - 1 - i, row) for i in range(START_LEN)]  # head first
    route = [(c, row) for c in range(START_LEN)]
    lead = START_LEN - 1
    heading = EAST
    tick = 0
    eats: list[tuple[int, Cell]] = []

    span = grid.cols - 1 - 6 - 4
    targets = [6 + round((k + 1) * span / FOOD_COUNT) for k in range(FOOD_COUNT)]

    for target_col in targets:
        head = body[0]
        occupied = set(body)
        candidates = sorted(
            (c for c in grid.cells if c not in occupied and _manhattan(c, head) >= 3),
            key=lambda c: (
                0 if grid.cells[c] > 0 else 1,
                abs(c[0] - target_col),
                grid.cells[c],
                abs(c[1] - head[1]),
                c,
            ),
        )
        chosen = None
        for cell in candidates[:40]:
            path = plan(body, heading, cell, walk)
            if not path:
                continue
            grown = advance(body, path, grow=True)
            if grown is not None and survivable(grown, walk):
                chosen = (cell, path, grown)
                break
        if chosen is None:
            break
        cell, path, grown = chosen
        route.extend(path)
        tick += len(path)
        heading = _heading(route[-2], route[-1])
        eats.append((tick, cell))
        body = grown

    crashed = _plan_crash(body, heading, walk, route)
    ticks = len(route) - 1 - lead
    return Game(grid=grid, route=route, lead=lead, ticks=ticks, eats=eats, crashed=crashed)


def _plan_crash(body: list[Cell], heading: int, walk: set[Cell], route: list[Cell]) -> bool:
    """Drive into the right-hand boundary. Returns False if no wall run was found."""
    head = body[0]
    body_set = set(body[:-1])
    edge = sorted(
        (c for c in walk if (c[0] + 1, c[1]) not in walk and c not in body_set),
        key=lambda c: (_manhattan(c, head), c),
    )
    if head in edge and heading == EAST:
        return True
    for goal_dir in (EAST, None):
        for cell in edge[:30]:
            path = plan(body, heading, cell, walk, goal_dir)
            if path and advance(body, path, grow=False) is not None:
                route.extend(path)
                return True
    return False


# --------------------------------------------------------------------------
# Timeline helpers shared by the SVG writer and the snapshot renderer
# --------------------------------------------------------------------------
def segment_times(game: Game) -> list[int]:
    """Tick at which each body segment first appears."""
    return [0] * START_LEN + [t for t, _ in game.eats]


def segment_arclength(game: Game, i: int, tau: float) -> float:
    """Distance (in cells) along the route of segment i at loop time tau."""
    return max(0.0, min(tau, game.ticks) + game.lead - i)


def point_on_route(game: Game, a: float) -> tuple[float, float, int]:
    """Cell-space position and heading at arclength a."""
    last = len(game.route) - 1
    a = min(max(a, 0.0), float(last))
    k = min(int(a), last - 1)
    f = a - k
    (x0, y0), (x1, y1) = game.route[k], game.route[k + 1]
    return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, _heading((x0, y0), (x1, y1))


def px(col: float, row: float) -> tuple[float, float]:
    """Pixel center of a (possibly fractional) cell coordinate."""
    return PAD + col * PITCH + CELL / 2, PAD + row * PITCH + CELL / 2


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------
def _n(v: float) -> str:
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _dims(grid: Grid) -> tuple[int, int]:
    return 2 * PAD + grid.cols * PITCH - GAP, 2 * PAD + ROWS * PITCH - GAP


def _cell_path(cells: list[Cell]) -> str:
    return "".join(f"M{PAD + c * PITCH} {PAD + r * PITCH}h{CELL}v{CELL}h-{CELL}z" for c, r in cells)


def _frame(width: int, height: int) -> str:
    a, i, t = 9, 5, 2  # arm length, inset, thickness
    corners = [
        f"M{i} {i + a}V{i}H{i + a}V{i + t}H{i + t}V{i + a}z",
        f"M{width - i - a} {i}H{width - i}V{i + a}H{width - i - t}V{i + t}H{width - i - a}z",
        f"M{i} {height - i - a}h{t}V{height - i - t}H{i + a}v{t}H{i}z",
        f"M{width - i} {height - i - a}v{a}H{width - i - a}v-{t}H{width - i - t}V{height - i - a}z",
    ]
    return f'<path d="{"".join(corners)}" fill="{PEACH}" fill-opacity=".5"/>'


def _board(grid: Grid, width: int, height: int, eaten: set[Cell] | None = None) -> str:
    ordered = sorted(grid.cells, key=lambda c: (c[0], c[1]))
    eaten = set() if eaten is None else eaten
    parts = [
        f'<rect width="{width}" height="{height}" fill="{DARK}"/>',
        _frame(width, height),
        f'<path d="{_cell_path(ordered)}" fill="{NAVY}"/>',
    ]
    for level in (1, 2, 3, 4):
        cells = [c for c in ordered if grid.cells[c] == level]
        if cells:
            rendered = []
            for c in cells:
                if c in eaten:
                    rendered.append((c[0], c[1], NAVY, 0.12))
                else:
                    rendered.append((c[0], c[1], PEACH, LEVEL_OPACITY[level]))
            for c, r, color, opacity in [(c, r, color, opacity) for c, r, color, opacity in [(c[0], c[1], PEACH, LEVEL_OPACITY[level]) for c in []]]:
                pass
            if rendered:
                blocks = [f'M{PAD + c * PITCH} {PAD + r * PITCH}h{CELL}v{CELL}h-{CELL}z' for c, r, _, _ in rendered]
                if blocks:
                    parts.append(f'<path d="{"".join(blocks)}" fill="{PEACH}" fill-opacity="0.12"/>')
                    # Render the remaining active contribution blocks separately.
                    active = [c for c, r, color, opacity in rendered if color != NAVY]
                    if active:
                        active_paths = [f'M{PAD + c * PITCH} {PAD + r * PITCH}h{CELL}v{CELL}h-{CELL}z' for c, r, _, _ in rendered if (c, r, color, opacity) in [(c, r, color, opacity) for c, r, color, opacity in rendered if color != NAVY]]
                        if active_paths:
                            parts.append(f'<path d="{"".join(active_paths)}" fill="{PEACH}" fill-opacity="{LEVEL_OPACITY[level]}"/>')
    return "\n".join(parts)


# Snake shapes are centered on the origin; food is top-left anchored.
_BODY = (
    f'<rect x="-6" y="-6" width="12" height="12" fill="{ORANGE}"/>'
    f'<rect x="-2" y="-2" width="4" height="4" fill="{RED}"/>'
)
_HEAD = (
    f'<rect x="-6" y="-6" width="12" height="12" fill="{RED}"/>'
    f'<rect x="0" y="-5" width="3" height="3" fill="{PEACH}"/>'
    f'<rect x="0" y="2" width="3" height="3" fill="{PEACH}"/>'
    f'<rect x="2" y="-4" width="1" height="1" fill="{NAVY}"/>'
    f'<rect x="2" y="3" width="1" height="1" fill="{NAVY}"/>'
)
_FOOD = (
    f'<rect width="12" height="12" fill="{PEACH}"/>'
    f'<rect x="4" y="4" width="4" height="4" fill="{RED}"/>'
)


def _route_path(game: Game) -> str:
    pts = [px(*c) for c in game.route]
    keep = [pts[0]]
    for prev, cur, nxt in zip(pts, pts[1:], pts[2:]):
        if (cur[0] - prev[0], cur[1] - prev[1]) != (nxt[0] - cur[0], nxt[1] - cur[1]):
            keep.append(cur)
    keep.append(pts[-1])
    d = f"M{_n(keep[0][0])} {_n(keep[0][1])}"
    for (x0, y0), (x1, y1) in zip(keep, keep[1:]):
        d += f"H{_n(x1)}" if y0 == y1 else f"V{_n(y1)}"
    return d


def build_svg(game: Game, user: str = "") -> str:
    grid = game.grid
    width, height = _dims(grid)
    D = game.loop_ticks
    dur = f"{_n(D * TICK)}s"
    N = len(game.route) - 1
    times = segment_times(game)
    fade_a = (game.ticks + HOLD_TICKS) / D
    fade_b = (game.ticks + HOLD_TICKS + FADE_TICKS) / D
    consumed = {cell for _, cell in game.eats}

    def timed(attr: str, values: list[str], keys: list[float], discrete: bool) -> str:
        mode = ' calcMode="discrete"' if discrete else ""
        return (
            f'<animate attributeName="{attr}" dur="{dur}" repeatCount="indefinite"{mode} '
            f'values="{";".join(values)}" keyTimes="{";".join(_n(k) for k in keys)}"/>'
        )

    def motion(i: int, rotate: bool) -> str:
        a0 = segment_arclength(game, i, 0)
        wake = max(0, i - game.lead)
        keys, pts = [0.0], [a0]
        if wake > 0:
            keys.append(wake / D)
            pts.append(0.0)
        keys += [game.ticks / D, 1.0]
        end = segment_arclength(game, i, game.ticks)
        pts += [end, end]
        rot = ' rotate="auto"' if rotate else ""
        return (
            f'<animateMotion dur="{dur}" repeatCount="indefinite" calcMode="linear"{rot} '
            f'keyPoints="{";".join(_n(p / N) for p in pts)}" '
            f'keyTimes="{";".join(_n(k) for k in keys)}">'
            f'<mpath href="#route" xlink:href="#route"/></animateMotion>'
        )

    segments = []
    for i in range(game.length - 1, 0, -1):
        if times[i] == 0:
            vis, start = "", "1"
        else:
            vis, start = timed("opacity", ["0", "1"], [0, times[i] / D], True), "0"
        segments.append(
            f'<use href="#body" xlink:href="#body" opacity="{start}">{vis}{motion(i, False)}</use>'
        )
    head = f'<use href="#head" xlink:href="#head" opacity="1">{motion(0, True)}</use>'

    fade = timed("opacity", ["1", "1", "0", "0"], [0, fade_a, fade_b, 1], False)
    title = "Pixel-art snake playing on a GitHub contribution graph"
    if user:
        title += f" for {user}"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'shape-rendering="crispEdges" role="img" aria-label="{title}">\n'
        f"<title>{title}</title>\n"
        f"<defs>\n"
        f'<path id="route" d="{_route_path(game)}" fill="none"/>\n'
        f'<g id="body">{_BODY}</g>\n<g id="head">{_HEAD}</g>\n<g id="food">{_FOOD}</g>\n'
        f"</defs>\n"
        f"{_board(grid, width, height, eaten=consumed)}\n"
        f"<g>{fade}\n{chr(10).join(segments)}\n{head}</g>\n"
        f"</svg>\n"
    )


def snapshot_svg(game: Game, tau: float) -> str:
    """A static, non-animated frame at loop time tau, for previews and tests."""
    grid = game.grid
    width, height = _dims(grid)
    tau = tau % game.loop_ticks
    fade_start = game.ticks + HOLD_TICKS
    fade = 1.0 if tau <= fade_start else max(0.0, 1 - (tau - fade_start) / FADE_TICKS)
    times = segment_times(game)
    consumed = {cell for _, cell in game.eats if tau >= _ for _ in [0]}

    parts = [_board(grid, width, height, eaten={cell for _, cell in game.eats if tau >= 0})]
    snake = []
    for i in range(game.length - 1, -1, -1):
        if tau < times[i]:
            continue
        x, y, d = point_on_route(game, segment_arclength(game, i, tau))
        cx, cy = px(x, y)
        shape, rot = (_HEAD, f" rotate({d * 90})") if i == 0 else (_BODY, "")
        snake.append(f'<g transform="translate({_n(cx)} {_n(cy)}){rot}">{shape}</g>')
    parts.append(f'<g opacity="{_n(fade)}">{"".join(snake)}</g>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" shape-rendering="crispEdges">\n'
        + "\n".join(parts)
        + "\n</svg>\n"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="GitHub username")
    ap.add_argument("--output", default="dist/snake-contribution-graph.svg")
    ap.add_argument("--from-json", help="offline input: JSON list of [date, level] pairs")
    ap.add_argument("--snapshot", type=float, metavar="TICK", help="write a static frame at TICK instead")
    args = ap.parse_args(argv)

    game = simulate(build_grid(load_days(args.user, args.from_json)))
    text = snapshot_svg(game, args.snapshot) if args.snapshot is not None else build_svg(game, args.user)
    changed = write_if_changed(Path(args.output), text)

    print(
        f"{'wrote' if changed else 'unchanged'} {args.output}: {len(text.encode()) / 1024:.1f} KB, "
        f"{len(game.eats)} food, length {game.length}, loop {game.loop_ticks * TICK:.1f}s, "
        f"wall crash: {'yes' if game.crashed else 'no'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
