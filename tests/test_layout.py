"""Tests for parsing the live layout out of kscreen-doctor."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.geometry import Direction, Rect, dead_bands  # noqa: E402
from wcs.layout import LayoutError, parse_kscreen_doctor, parse_spec  # noqa: E402

# Shaped like real `kscreen-doctor -o` output, including the colour escapes it
# emits even when piped, a disabled output that must be ignored, and a rotated
# output whose Geometry is already the rotated (logical) rectangle.
SAMPLE = """Output: 1 \x1b[32mDP-1\x1b[0m
\t\x1b[32menabled\x1b[0m
\tconnected
\tpriority 2
\tModes:  1:1920x1080@60\x1b[32m*!\x1b[0m  2:1920x1080@50
\tGeometry: 0,400 1920x1080
\tScale: 1
\tRotation: 1
\tOverscan: 0
Output: 2 \x1b[32mDP-2\x1b[0m
\t\x1b[32menabled\x1b[0m
\tconnected
\tpriority 1
\tModes:  1:3840x2160@60\x1b[32m*!\x1b[0m
\tGeometry: 1920,0 3840x2160
\tScale: 2
\tRotation: 1
Output: 3 \x1b[32mDP-3\x1b[0m
\t\x1b[32menabled\x1b[0m
\tconnected
\tpriority 3
\tModes:  1:1920x1080@60\x1b[32m*!\x1b[0m
\tGeometry: 5760,-200 1080x1920
\tScale: 1
\tRotation: 8
Output: 4 \x1b[31mHDMI-A-1\x1b[0m
\t\x1b[31mdisabled\x1b[0m
\tdisconnected
\tGeometry: 0,0 1280x1024
\tScale: 1
"""


class ParseKscreenTest(unittest.TestCase):
    def test_enabled_outputs_are_parsed_with_their_logical_geometry(self):
        layout = parse_kscreen_doctor(SAMPLE)
        self.assertEqual([o.name for o in layout.outputs], ["DP-1", "DP-2", "DP-3"])
        self.assertEqual(layout.outputs[1].rect, Rect(1920, 0, 3840, 2160))

    def test_a_rotated_output_keeps_the_rectangle_kscreen_reports(self):
        # Rotation 8 is 270 degrees; kscreen has already applied it, so the
        # rectangle is portrait and must not be transposed a second time.
        layout = parse_kscreen_doctor(SAMPLE)
        portrait = layout.outputs[2].rect
        self.assertEqual((portrait.width, portrait.height), (1080, 1920))

    def test_disabled_outputs_are_dropped(self):
        layout = parse_kscreen_doctor(SAMPLE)
        self.assertNotIn("HDMI-A-1", [o.name for o in layout.outputs])

    def test_negative_origins_survive(self):
        layout = parse_kscreen_doctor(SAMPLE)
        self.assertEqual(layout.outputs[2].rect.y, -200)

    def test_bounding_box_spans_every_output(self):
        self.assertEqual(parse_kscreen_doctor(SAMPLE).bounding_box, Rect(0, -200, 6840, 2360))

    def test_no_enabled_output_is_an_error(self):
        with self.assertRaises(LayoutError):
            parse_kscreen_doctor("Output: 1 DP-1\n\tdisabled\n\tGeometry: 0,0 100x100\n")

    def test_the_parsed_sample_reproduces_the_three_marked_bands(self):
        layout = parse_kscreen_doctor(SAMPLE)
        bands = [
            (b.output, b.direction.value, b.start, b.end)
            for b in dead_bands(layout, Direction.LEFT) + dead_bands(layout, Direction.RIGHT)
            if b.output == "DP-2"
        ]
        self.assertEqual(
            bands,
            [
                ("DP-2", "left", 0, 400),
                ("DP-2", "left", 1480, 2160),
                ("DP-2", "right", 1720, 2160),
            ],
        )


class ParseSpecTest(unittest.TestCase):
    def test_round_trip(self):
        layout = parse_spec("a:0,400,1920x1080; b:1920,0,3840x2160")
        self.assertEqual(layout.outputs[0].rect, Rect(0, 400, 1920, 1080))
        self.assertEqual(layout.outputs[1].rect, Rect(1920, 0, 3840, 2160))

    def test_malformed_chunk_is_rejected(self):
        with self.assertRaises(LayoutError):
            parse_spec("a:0,0,1920")


if __name__ == "__main__":
    unittest.main()
