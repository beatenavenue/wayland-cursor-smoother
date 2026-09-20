"""Tests for the parts of the uinput layer that need no device.

Creating the device needs /dev/uinput, so that is left to
tools/probe_uinput.py on real hardware.  What is checked here is the
arithmetic that decides *where* the pointer lands, and the uapi struct
layouts -- both of which would otherwise only fail as a mysterious offset on
the author's desk.
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.uinput import (  # noqa: E402
    ABS_MAX,
    VirtualPointer,
    AxisMapping,
    UinputError,
    _INPUT_EVENT,
    _UINPUT_ABS_SETUP,
    _UINPUT_SETUP,
    ui_get_sysname,
)


def libinput_transform(raw: int, extent: int) -> float:
    """What libinput does to a raw absolute value.

    ``evdev_device_transform_x()`` scales by ``to_range / absinfo_range``,
    where ``absinfo_range`` is ``maximum - minimum + 1``.  The +1 is the whole
    reason this helper exists: dropping it puts the far edge one pixel out.
    """
    return raw * extent / (ABS_MAX + 1)


class StructLayoutTest(unittest.TestCase):
    """Values generated from /usr/include/linux/uinput.h on a 6.x kernel."""

    def test_struct_sizes_match_the_uapi_headers(self):
        self.assertEqual(_INPUT_EVENT.size, 24)
        self.assertEqual(_UINPUT_SETUP.size, 92)
        self.assertEqual(_UINPUT_ABS_SETUP.size, 28)

    def test_abs_setup_puts_absinfo_at_offset_four(self):
        packed = _UINPUT_ABS_SETUP.pack(0x01, 11, 22, 33, 44, 55, 66)
        self.assertEqual(struct.unpack_from("@H", packed, 0)[0], 0x01)
        self.assertEqual(struct.unpack_from("@6i", packed, 4), (11, 22, 33, 44, 55, 66))

    def test_device_name_is_truncated_to_the_kernel_limit(self):
        packed = _UINPUT_SETUP.pack(6, 1, 1, 1, (b"x" * 200)[:79], 0)
        self.assertEqual(_UINPUT_SETUP.size, 92)
        self.assertEqual(struct.unpack_from("@80s", packed, 8)[0].rstrip(b"\0"), b"x" * 79)

    def test_ui_get_sysname_matches_the_compiled_ioctl_number(self):
        self.assertEqual(ui_get_sysname(64), 0x8040552C)


class AxisMappingTest(unittest.TestCase):
    LAYOUT = AxisMapping(0, 0, 7680, 2160)

    def test_origin_maps_to_zero(self):
        self.assertEqual(self.LAYOUT.raw(0, 0), (0, 0))

    def test_values_are_clamped_into_range(self):
        self.assertEqual(self.LAYOUT.raw(-500, 10**6), (0, ABS_MAX))

    def test_round_trip_is_within_one_pixel_everywhere(self):
        for x in range(0, 7680, 13):
            raw_x, _ = self.LAYOUT.raw(x, 0)
            self.assertLessEqual(abs(libinput_transform(raw_x, 7680) - x), 1.0, f"x={x}")
        for y in range(0, 2160, 7):
            _, raw_y = self.LAYOUT.raw(0, y)
            self.assertLessEqual(abs(libinput_transform(raw_y, 2160) - y), 1.0, f"y={y}")

    def test_round_trip_never_overshoots_the_last_pixel(self):
        raw_x, raw_y = self.LAYOUT.raw(7679, 2159)
        self.assertLess(libinput_transform(raw_x, 7680), 7680)
        self.assertLess(libinput_transform(raw_y, 2160), 2160)

    def test_a_non_zero_origin_is_subtracted(self):
        shifted = AxisMapping(-1920, -200, 7680, 2160)
        self.assertEqual(shifted.raw(-1920, -200), (0, 0))
        self.assertEqual(shifted.raw(0, 0), self.LAYOUT.raw(1920, 200))

    def test_a_degenerate_layout_is_rejected(self):
        with self.assertRaises(UinputError):
            AxisMapping(0, 0, 0, 1080).raw(0, 0)


if __name__ == "__main__":
    unittest.main()


class RepeatedPositionTest(unittest.TestCase):
    """The kernel drops an absolute value that repeats this device's own.

    Found on hardware, reported as "crossing the same edge twice stops
    working, another edge is fine". Within one dead band every redirect lands
    on the same corner of the neighbour -- that is the nearest valid point
    from anywhere in the band, and is correct -- so the second redirect asks
    for exactly the position the first one set, and the kernel discards it.
    Nothing moves, and the log still says a redirect happened.

    A glide hides it, because its intermediate positions differ; a warp does
    not, because it has none.
    """

    def emitted(self, moves):
        """Run ``moves`` through a real fd and decode what came out."""
        import os
        read_fd, write_fd = os.pipe()
        pointer = VirtualPointer()
        pointer._fd = write_fd
        try:
            for raw_x, raw_y in moves:
                pointer.move_raw(raw_x, raw_y)
        finally:
            os.close(write_fd)
        blob = b""
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            blob += chunk
        os.close(read_fd)
        count = len(blob) // _INPUT_EVENT.size
        return [_INPUT_EVENT.unpack_from(blob, i * _INPUT_EVENT.size)[2:]
                for i in range(count)]

    def test_a_first_move_is_one_plain_report(self):
        self.assertEqual(
            self.emitted([(1000, 2000)]),
            [(0x03, 0x00, 1000), (0x03, 0x01, 2000), (0x00, 0x00, 0)],
        )

    def test_a_repeat_is_preceded_by_a_nudge_in_its_own_report(self):
        events = self.emitted([(1000, 2000), (1000, 2000)])
        self.assertEqual(events[3:], [
            (0x03, 0x00, 999), (0x00, 0x00, 0),          # nudge, committed
            (0x03, 0x00, 1000), (0x03, 0x01, 2000), (0x00, 0x00, 0),
        ])

    def test_the_nudge_is_below_a_tenth_of_a_pixel(self):
        # One raw unit over a 6840px layout. Invisible, which is the point.
        mapping = AxisMapping(0, 0, 6840, 2160)
        self.assertLess(libinput_transform(1, 6840), 0.11)
        self.assertEqual(mapping.raw(0, 0), (0, 0))  # and it stays in range

    def test_a_different_position_needs_no_nudge(self):
        events = self.emitted([(1000, 2000), (1500, 2000)])
        self.assertEqual(len(events), 6)
        self.assertNotIn(999, [value for _, _, value in events])

    def test_the_nudge_goes_up_when_the_axis_is_already_at_zero(self):
        events = self.emitted([(0, 0), (0, 0)])
        self.assertEqual(events[3], (0x03, 0x00, 1))

    def test_the_position_that_lands_is_always_the_one_asked_for(self):
        for moves in ([(5, 7)], [(5, 7), (5, 7)], [(5, 7), (5, 7), (5, 7)]):
            events = self.emitted(moves)
            axes = {code: value for etype, code, value in events if etype == 0x03}
            self.assertEqual((axes[0x00], axes[0x01]), (5, 7), moves)

    def test_every_report_ends_with_a_sync(self):
        events = self.emitted([(1000, 2000), (1000, 2000), (3, 4)])
        self.assertEqual(events[-1][0], 0x00)
        syncs = sum(1 for etype, _, _ in events if etype == 0x00)
        self.assertEqual(syncs, 4)  # three moves plus one nudge

    def test_a_repeat_after_an_unrelated_move_still_nudges(self):
        # The physical pointer moves the cursor without touching this
        # device's axes, so "we last emitted this" is the only thing that
        # matters, however long ago it was.
        events = self.emitted([(10, 10), (20, 20), (20, 20)])
        self.assertIn((0x03, 0x00, 19), events)
