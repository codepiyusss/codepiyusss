"""Offline tests for the Snake generator. Run: python -m unittest discover -s tests -v

These use synthetic contribution grids only to exercise the generator; the
real graph is fetched by the workflow at run time.
"""

import datetime as dt
import random
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_snake as g  # noqa: E402


def synthetic_days(seed=1, density=0.3, weeks=53, today=dt.date(2026, 10, 29)):
    """A full year of days ending today (Thursday), like GitHub's own graph."""
    rng = random.Random(seed)
    first = today - dt.timedelta(days=(today.weekday() + 1) % 7 + 7 * (weeks - 1))
    days, d = {}, first
    while d <= today:
        days[d] = rng.choice([1, 2, 3, 4]) if rng.random() < density else 0
        d += dt.timedelta(days=1)
    return days


class GridTests(unittest.TestCase):
    def test_shape_and_future_cells(self):
        grid = g.build_grid(synthetic_days())
        self.assertEqual(grid.cols, 53)
        self.assertTrue(all(0 <= r < 7 for _, r in grid.cells))
        # Thursday today -> Fri/Sat of the last week do not exist yet.
        self.assertNotIn((52, 5), grid.cells)
        self.assertIn((52, 4), grid.cells)


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.grid = g.build_grid(synthetic_days())
        self.game = g.simulate(self.grid)

    def test_deterministic(self):
        again = g.simulate(self.grid)
        self.assertEqual(self.game.route, again.route)
        self.assertEqual(self.game.eats, again.eats)

    def test_route_is_unit_steps_on_real_cells_without_reversing(self):
        r = self.game.route
        for a, b in zip(r, r[1:]):
            self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
            self.assertIn(b, self.grid.cells)
        for a, b, c in zip(r, r[1:], r[2:]):
            self.assertNotEqual(a, c, "snake reversed onto itself")

    def test_replay_has_no_self_collision_and_grows_on_food(self):
        r, lead = self.game.route, self.game.lead
        eat_at = dict(self.game.eats)
        length = g.START_LEN
        for t in range(1, self.game.ticks + 1):
            head = r[lead + t]
            if t in eat_at:
                self.assertEqual(head, eat_at[t])
                length += 1
            body = [r[lead + t - i] for i in range(length)]
            self.assertEqual(len(set(body)), length, f"self-collision at tick {t}")

    def test_food_only_on_contribution_days(self):
        for _, cell in self.game.eats:
            self.assertGreater(self.grid.cells[cell], 0)

    def test_ends_in_wall_collision(self):
        self.assertTrue(self.game.crashed)
        x, y = self.game.route[-1]
        self.assertNotIn((x + 1, y), self.grid.cells)

    def test_sparse_graph_still_plays(self):
        game = g.simulate(g.build_grid(synthetic_days(density=0.0)))
        self.assertGreater(len(game.eats), 0)  # falls back to empty cells
        self.assertTrue(game.crashed)


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.game = g.simulate(g.build_grid(synthetic_days()))
        self.svg = g.build_svg(self.game, "someone")
        self.root = ET.fromstring(self.svg)

    def test_well_formed_and_deterministic(self):
        self.assertEqual(self.svg, g.build_svg(self.game, "someone"))

    def test_lightweight(self):
        self.assertLess(len(self.svg.encode()), 40 * 1024)

    def test_no_scripts_events_or_external_references(self):
        self.assertNotIn("<script", self.svg)
        self.assertNotIn("<image", self.svg)
        self.assertNotIn("<foreignObject", self.svg)
        self.assertIsNone(re.search(r'\son[a-z]+="', self.svg))
        self.assertNotIn("<style", self.svg)
        urls = re.findall(r'https?://[^"\s)]+', self.svg)
        self.assertEqual(set(urls), {"http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"})

    def test_only_theme_colors(self):
        used = set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', self.svg))
        self.assertTrue(used <= g.PALETTE, used - g.PALETTE)
        self.assertEqual(used, g.PALETTE)  # all five are actually used

    def test_animation_loops_in_sync(self):
        durs = set(re.findall(r'dur="([^"]+)"', self.svg))
        self.assertEqual(len(durs), 1)
        self.assertEqual(self.svg.count("repeatCount"), self.svg.count("<animate"))
        self.assertTrue(all('repeatCount="indefinite"' in m for m in re.findall(r"<animate[^>]*>", self.svg)))

    def test_references_resolve(self):
        ids = set(re.findall(r'id="([^"]+)"', self.svg))
        refs = set(re.findall(r'href="#([^"]+)"', self.svg))
        self.assertTrue(refs <= ids, refs - ids)

    def test_key_times_valid(self):
        for kt in re.findall(r'keyTimes="([^"]+)"', self.svg):
            vals = [float(v) for v in kt.split(";")]
            self.assertEqual(vals[0], 0.0)
            self.assertEqual(vals, sorted(vals))
            self.assertLessEqual(vals[-1], 1.0)

    def test_snapshot_is_valid_xml(self):
        for tau in (0, 5.5, self.game.ticks + 2, self.game.loop_ticks - 1):
            ET.fromstring(g.snapshot_svg(self.game, tau))


class OutputTests(unittest.TestCase):
    def test_write_if_changed_avoids_git_diffs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a" / "out.svg"
            self.assertTrue(g.write_if_changed(p, "x"))
            self.assertFalse(g.write_if_changed(p, "x"))
            self.assertTrue(g.write_if_changed(p, "y"))


if __name__ == "__main__":
    unittest.main()
