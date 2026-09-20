"""Tests for device classification and the access rule.

The rule's job is not "let us read the mouse"; it is "let us read the mouse
and never the keyboard". These tests are mostly about the second half, because
a rule that is too wide still makes the feature work and is still a failure.
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.evdev import (  # noqa: E402
    EV_ABS,
    EV_KEY,
    EV_REL,
    REL_X,
    REL_Y,
    InputDevice,
    _event_number,
    _INPUT_EVENT,
    access_verdict,
    classify,
    decode_events,
    motion_magnitude,
    parse_udev_properties,
    udev_rule_text,
)


def device(path, name, roles, readable):
    return InputDevice(path=path, name=name, roles=frozenset(roles), readable=readable)


MOUSE = device("/dev/input/event3", "Logitech MX Master", {"mouse"}, True)
TRACKPOINT = device("/dev/input/event5", "TPPS/2 Elan TrackPoint",
                    {"mouse", "pointingstick"}, True)
KEYBOARD = device("/dev/input/event0", "AT Translated Set 2 keyboard",
                  {"keyboard", "keys"}, False)
COMBO = device("/dev/input/event7", "Keyboard with stick",
               {"keyboard", "keys", "mouse"}, True)


class ParsePropertiesTest(unittest.TestCase):
    def test_key_value_lines(self):
        text = "ID_INPUT=1\nID_INPUT_MOUSE=1\nNAME=\"A Mouse\"\nJUNK\n"
        self.assertEqual(
            parse_udev_properties(text),
            {"ID_INPUT": "1", "ID_INPUT_MOUSE": "1", "NAME": '"A Mouse"'},
        )

    def test_values_containing_equals_survive(self):
        self.assertEqual(
            parse_udev_properties("DEVLINKS=/dev/input/by-id/usb-a=b"),
            {"DEVLINKS": "/dev/input/by-id/usb-a=b"},
        )

    def test_empty_input(self):
        self.assertEqual(parse_udev_properties(""), {})


class ClassifyTest(unittest.TestCase):
    def test_a_plain_mouse(self):
        self.assertEqual(classify({"ID_INPUT": "1", "ID_INPUT_MOUSE": "1"}), {"mouse"})

    def test_a_trackpoint_is_a_mouse_and_a_pointing_stick(self):
        self.assertEqual(
            classify({"ID_INPUT_MOUSE": "1", "ID_INPUT_POINTINGSTICK": "1"}),
            {"mouse", "pointingstick"},
        )

    def test_a_value_other_than_one_does_not_count(self):
        self.assertEqual(classify({"ID_INPUT_MOUSE": "0"}), frozenset())

    def test_unknown_properties_are_ignored(self):
        self.assertEqual(classify({"ID_SERIAL": "x", "ID_INPUT_KEYBOARD": "1"}), {"keyboard"})


class VerdictTest(unittest.TestCase):
    def test_pointers_readable_and_no_keyboard_readable_passes(self):
        verdict = access_verdict([MOUSE, TRACKPOINT, KEYBOARD])
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.readable_pointing, (MOUSE.path, TRACKPOINT.path))
        self.assertEqual(verdict.readable_keyboards, ())

    def test_a_readable_keyboard_fails_even_though_the_feature_would_work(self):
        # This is the `input` group outcome: everything the detector needs is
        # present, and the rule has still failed at its actual job.
        verdict = access_verdict([MOUSE, device(KEYBOARD.path, KEYBOARD.name,
                                                KEYBOARD.roles, True)])
        self.assertFalse(verdict.ok)
        self.assertIn("too wide", verdict.reason)

    def test_a_combined_keyboard_and_pointer_node_fails(self):
        # A single node carrying both is not something a rule can split:
        # granting its motion grants its keystrokes.
        verdict = access_verdict([COMBO])
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.readable_keyboards, (COMBO.path,))

    def test_nothing_readable_is_reported_as_not_in_effect(self):
        verdict = access_verdict([device(MOUSE.path, MOUSE.name, MOUSE.roles, False),
                                  KEYBOARD])
        self.assertFalse(verdict.ok)
        self.assertIn("not in effect", verdict.reason)

    def test_a_touchpad_counts_as_a_pointing_device(self):
        self.assertTrue(access_verdict([device("/dev/input/event9", "Touchpad",
                                               {"touchpad"}, True)]).ok)

    def test_a_readable_joystick_is_neither_required_nor_disqualifying(self):
        verdict = access_verdict([MOUSE, device("/dev/input/event8", "Gamepad",
                                                {"joystick"}, True)])
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.readable_pointing, (MOUSE.path,))


class RuleTest(unittest.TestCase):
    RULE = udev_rule_text()

    def test_keyboards_are_refused_before_anything_is_granted(self):
        refuse = self.RULE.index('ENV{ID_INPUT_KEYBOARD}=="1", GOTO="wcs_end"')
        grant = self.RULE.index('ENV{ID_INPUT_MOUSE}=="1", TAG+="uaccess"')
        self.assertLess(refuse, grant)

    def test_it_grants_every_pointing_role(self):
        for prop in ("ID_INPUT_MOUSE", "ID_INPUT_POINTINGSTICK", "ID_INPUT_TOUCHPAD"):
            self.assertIn(f'ENV{{{prop}}}=="1", TAG+="uaccess"', self.RULE)

    def test_it_never_matches_on_vendor_or_product(self):
        # Matching those would also match the keyboard interface of a device
        # that has both, which is the failure this rule exists to avoid.
        self.assertNotIn("idVendor", self.RULE)
        self.assertNotIn("idProduct", self.RULE)

    def test_the_goto_label_exists(self):
        self.assertIn('LABEL="wcs_end"', self.RULE)

    def test_it_grants_by_acl_rather_than_by_group(self):
        self.assertIn('TAG+="uaccess"', self.RULE)
        self.assertNotIn('GROUP=', self.RULE)


class EventDecodingTest(unittest.TestCase):
    @staticmethod
    def pack(*events):
        return b"".join(_INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events)

    def test_a_partial_trailing_event_is_ignored(self):
        blob = self.pack((EV_REL, REL_X, 5)) + b"\x00" * 7
        self.assertEqual(decode_events(blob), [(EV_REL, REL_X, 5)])

    def test_relative_motion_is_summed_by_magnitude(self):
        events = [(EV_REL, REL_X, -4), (EV_REL, REL_Y, 3), (EV_KEY, 0x110, 1)]
        self.assertEqual(motion_magnitude(events), 7)

    def test_absolute_motion_counts_too(self):
        # An assistive pointer may report positions, not deltas. Counting only
        # REL_* would report such a device as silent.
        self.assertEqual(motion_magnitude([(EV_ABS, 0, 30000), (EV_ABS, 1, 100)]), 2)

    def test_button_presses_alone_are_not_motion(self):
        self.assertEqual(motion_magnitude([(EV_KEY, 0x110, 1), (EV_KEY, 0x110, 0)]), 0)

    def test_round_trip_through_the_struct(self):
        self.assertEqual(
            decode_events(self.pack((EV_REL, REL_X, 1), (EV_REL, REL_Y, -1))),
            [(EV_REL, REL_X, 1), (EV_REL, REL_Y, -1)],
        )


class SortOrderTest(unittest.TestCase):
    def test_event_nodes_sort_numerically_not_lexically(self):
        paths = ["/dev/input/event10", "/dev/input/event2", "/dev/input/event1"]
        self.assertEqual(
            sorted(paths, key=_event_number),
            ["/dev/input/event1", "/dev/input/event2", "/dev/input/event10"],
        )

    def test_an_unnumbered_node_sorts_last_instead_of_crashing(self):
        paths = ["/dev/input/event-odd", "/dev/input/event3"]
        self.assertEqual(sorted(paths, key=_event_number)[0], "/dev/input/event3")


if __name__ == "__main__":
    unittest.main()
