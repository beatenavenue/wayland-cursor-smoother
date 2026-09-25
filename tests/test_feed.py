"""Tests for the watch strips and the generated feed script.

The strips are how the dead-band semantic reaches the compositor *without*
being reimplemented there. So these check two things: that the strips are the
dead bands, and that the script knows nothing else.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.feed import (  # noqa: E402
    WatchBand,
    bands_as_json,
    feed_script,
    watch_bands,
)
from wcs.geometry import Direction, Layout, Output, Rect  # noqa: E402
from wcs.layout import parse_kscreen_doctor  # noqa: E402

FIXTURE = Path(__file__).parent / "data" / "kscreen-doctor-plasma-6.3.6.txt"
REAL = parse_kscreen_doctor(FIXTURE.read_text())

# The facing ratio figure: two equal displays, offset so that only 30 of 150
# rows touch. Both directions face 20%.
OFFSET = Layout.of([
    Output("target", Rect(0, 0, 90, 150)),
    Output("current", Rect(90, 120, 120, 150)),
])


class WatchBandTest(unittest.TestCase):
    def test_the_real_layout_yields_exactly_its_four_dead_bands(self):
        bands = watch_bands(REAL)
        self.assertEqual(
            [(b.output, b.direction.value, b.rect) for b in bands],
            [
                ("DP-4", "left", Rect(1920, 0, 2, 512)),
                ("DP-4", "left", Rect(1920, 1592, 2, 568)),
                ("DP-4", "right", Rect(5758, 0, 2, 92)),
                ("DP-4", "right", Rect(5758, 2012, 2, 148)),
            ],
        )

    def test_perimeter_bands_are_left_out(self):
        # The outside edges of the desktop are dead bands too, and arming
        # there would teach the user that pushing sometimes does nothing.
        outputs = {b.output for b in watch_bands(REAL)}
        self.assertEqual(outputs, {"DP-4"})

    def test_indices_are_contiguous_and_match_position(self):
        bands = watch_bands(REAL)
        self.assertEqual([b.index for b in bands], list(range(len(bands))))

    def test_a_strip_hugs_the_edge_it_watches(self):
        for band in watch_bands(REAL, strip=2):
            rect = next(o.rect for o in REAL.outputs if o.name == band.output)
            if band.direction is Direction.LEFT:
                self.assertEqual(band.rect.left, rect.left)
            else:
                self.assertEqual(band.rect.right, rect.right)
            self.assertEqual(band.rect.width, 2)

    def test_vertical_strips_hug_the_top_and_bottom(self):
        stacked = Layout.of([
            Output("top", Rect(0, 0, 500, 500)),
            Output("bottom", Rect(200, 500, 1000, 500)),
        ])
        by_direction = {b.direction: b for b in watch_bands(stacked, strip=3)}
        down = by_direction[Direction.DOWN]
        self.assertEqual(down.output, "top")
        self.assertEqual(down.rect, Rect(0, 497, 200, 3))
        up = by_direction[Direction.UP]
        self.assertEqual(up.output, "bottom")
        self.assertEqual(up.rect, Rect(500, 500, 700, 3))

    def test_a_strip_is_never_thinner_than_one_pixel(self):
        self.assertTrue(all(b.rect.width >= 1 and b.rect.height >= 1
                            for b in watch_bands(REAL, strip=0)))

    def test_max_slide_removes_the_bands_it_would_refuse(self):
        # Every band on this layout needs a slide of at least 48px.
        self.assertEqual(watch_bands(REAL, max_slide=10), [])
        self.assertEqual(len(watch_bands(REAL, max_slide=1000)), 4)

    def test_each_band_knows_where_it_goes_and_its_facing_ratio(self):
        # On the author's desk the side displays are fully backed by the
        # centre one, so every band faces 100%.
        self.assertEqual(
            [(b.to_output, b.facing) for b in watch_bands(REAL)],
            [("DP-3", 100.0), ("DP-3", 100.0), ("DP-1", 100.0), ("DP-1", 100.0)],
        )

    def test_min_facing_removes_the_bands_it_would_refuse(self):
        self.assertEqual(len(watch_bands(REAL, min_facing=100)), 4)
        self.assertEqual(len(watch_bands(OFFSET, min_facing=20)), 2)
        self.assertEqual(len(watch_bands(OFFSET, min_facing=20.1)), 0)

    def test_a_band_is_split_where_its_target_changes(self):
        # Two displays stacked on the left, with a gap between them. The part
        # of the centre's left edge in the gap goes up to "upper" nearer the
        # top and down to "lower" nearer the bottom. Those two have different
        # facing ratios, so they must be separate strips.
        centre = Output("centre", Rect(1000, 0, 1000, 1000))
        upper = Output("upper", Rect(0, 0, 1000, 400))       # touches 400 of 400
        lower = Output("lower", Rect(0, 600, 1000, 800))     # touches 400 of 800
        layout = Layout.of([centre, upper, lower])
        left = [b for b in watch_bands(layout, inset=0)
                if b.output == "centre" and b.direction is Direction.LEFT]
        self.assertEqual(
            [(b.rect.top, b.rect.bottom, b.to_output, b.facing) for b in left],
            [(400, 500, "upper", 100.0), (500, 600, "lower", 50.0)],
        )
        kept = [b for b in watch_bands(layout, inset=0, min_facing=60)
                if b.output == "centre" and b.direction is Direction.LEFT]
        self.assertEqual([(b.rect.top, b.rect.bottom) for b in kept], [(400, 500)])

    def test_a_rectangular_layout_has_nothing_to_watch(self):
        tidy = Layout.of([
            Output("a", Rect(0, 0, 1920, 1080)),
            Output("b", Rect(1920, 0, 1920, 1080)),
        ])
        self.assertEqual(watch_bands(tidy), [])


class JsonTest(unittest.TestCase):
    def test_the_table_carries_only_what_the_script_needs(self):
        band = WatchBand(3, Rect(10, 20, 2, 30), "DP-9", Direction.LEFT,
                         to_output="DP-8", facing=20.0)
        self.assertEqual(
            json.loads(bands_as_json([band])),
            [{"i": 3, "x": 10, "y": 20, "w": 2, "h": 30}],
        )

    def test_the_output_name_and_direction_stay_in_python(self):
        # The script gets rectangles, not meanings.
        text = bands_as_json(watch_bands(REAL))
        for word in ("DP-4", "DP-3", "DP-1", "left", "facing", "100"):
            self.assertNotIn(word, text)


class ScriptTest(unittest.TestCase):
    BANDS = watch_bands(REAL)
    SCRIPT = feed_script(BANDS, bus_name="a.b.C", object_path="/a/b/C",
                         interface="a.b.C.Feed")

    def test_it_is_syntactically_plausible(self):
        self.assertEqual(self.SCRIPT.count("{"), self.SCRIPT.count("}"))
        self.assertEqual(self.SCRIPT.count("("), self.SCRIPT.count(")"))

    def test_it_carries_the_strips_and_the_address(self):
        self.assertIn(bands_as_json(self.BANDS), self.SCRIPT)
        for piece in ('"a.b.C"', '"/a/b/C"', '"a.b.C.Feed"'):
            self.assertIn(piece, self.SCRIPT)

    def test_it_reports_through_one_method(self):
        # Count call sites, not the word: the error message mentions it too.
        self.assertEqual(self.SCRIPT.count("callDBus("), 1)
        self.assertIn("'Edge'", self.SCRIPT)

    def test_it_stays_silent_away_from_every_strip(self):
        # The early return is what keeps this from being 84 calls a second.
        self.assertIn("if (band < 0 && current < 0)", self.SCRIPT)

    def test_it_contains_no_layout_knowledge(self):
        # If any of these appear, the semantic has started to leak into JS.
        for word in ("DP-4", "dead", "neighbour", "redirect", "slide"):
            self.assertNotIn(word, self.SCRIPT.replace("dead band", ""))

    def test_an_empty_band_list_still_produces_a_valid_script(self):
        script = feed_script([], bus_name="a.b.C", object_path="/a/b/C",
                             interface="a.b.C.Feed")
        self.assertIn("var BANDS = [];", script)
        self.assertEqual(script.count("{"), script.count("}"))


if __name__ == "__main__":
    unittest.main()


class TraceScriptTest(unittest.TestCase):
    """The tracing variant exists because a silence had to be broken open.

    The first daemon run loaded a script that ran, logged nothing and called
    nothing. Reading the code cannot distinguish "the rectangle test never
    matched" from "the call failed", so the script says what it sees.
    """

    BANDS = watch_bands(REAL)

    def script(self, **kwargs):
        return feed_script(self.BANDS, bus_name="a.b.C", object_path="/a/b/C",
                           interface="a.b.C.Feed", marker="mark", **kwargs)

    def test_tracing_reports_the_position_and_the_computed_band(self):
        script = self.script(trace=True)
        self.assertIn("' sees ' + p.x + ',' + p.y + ' -> band ' + band", script)

    def test_tracing_is_off_by_default(self):
        self.assertNotIn(" sees ", self.script())

    def test_a_failing_call_is_reported_in_both_modes(self):
        # Until this existed, a callDBus that threw looked exactly like one
        # that was never reached.
        for script in (self.script(), self.script(trace=True)):
            self.assertIn("catch (e)", script)
            self.assertIn("callDBus threw", script)

    def test_both_modes_stay_syntactically_balanced(self):
        for script in (self.script(), self.script(trace=True)):
            self.assertEqual(script.count("{"), script.count("}"))
            self.assertEqual(script.count("("), script.count(")"))

    def test_no_placeholder_survives_into_either_script(self):
        for script in (self.script(), self.script(trace=True)):
            self.assertNotIn("__", script)
