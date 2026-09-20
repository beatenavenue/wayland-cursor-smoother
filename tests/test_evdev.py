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
    GRANT_ACL,
    GRANT_EXPLANATIONS,
    GRANT_GROUP,
    GRANT_NONE,
    GRANT_WORLD,
    EV_KEY,
    EV_REL,
    REL_X,
    REL_Y,
    InputDevice,
    _event_number,
    _INPUT_EVENT,
    access_verdict,
    classify,
    classify_grant,
    decode_events,
    motion_magnitude,
    parse_capability_bitmask,
    parse_udev_properties,
    text_keys,
    udev_rule_text,
)


def device(path, name, roles, readable, grant=None):
    if grant is None:
        grant = GRANT_ACL if readable else GRANT_NONE
    return InputDevice(path=path, name=name, roles=frozenset(roles),
                       readable=readable, grant=grant)


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
        self.assertEqual(verdict.keyboards_we_granted, ())

    def test_a_keyboard_granted_by_an_acl_fails(self):
        # Everything the detector needs is present, and the rule has still
        # failed at its actual job.
        verdict = access_verdict([MOUSE, device(KEYBOARD.path, KEYBOARD.name,
                                                KEYBOARD.roles, True, GRANT_ACL)])
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.keyboards_we_granted, (KEYBOARD.path,))

    def test_a_keyboard_left_world_readable_by_somebody_else_does_not_fail_us(self):
        # The first real run met exactly this: a tablet vendor's udev rule had
        # set mode 0666 across its own nodes, keyboard interface included.
        # It is reported, but it is not this rule's doing and not its to fix.
        vendor = device("/dev/input/event7", "XP-PEN DECO 03 Keyboard",
                        {"keyboard", "keys"}, True, GRANT_WORLD)
        verdict = access_verdict([MOUSE, vendor])
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.keyboards_we_granted, ())
        self.assertEqual(verdict.keyboards_already_open, (vendor.path,))

    def test_the_input_group_shows_up_as_already_open_too(self):
        verdict = access_verdict([MOUSE, device(KEYBOARD.path, KEYBOARD.name,
                                                KEYBOARD.roles, True, GRANT_GROUP)])
        self.assertEqual(verdict.keyboards_already_open, (KEYBOARD.path,))

    def test_a_combined_keyboard_and_pointer_node_granted_by_acl_fails(self):
        # A single node carrying both is not something a rule can split:
        # granting its motion grants its keystrokes.
        verdict = access_verdict([COMBO])
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.keyboards_we_granted, (COMBO.path,))

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


class GrantSourceTest(unittest.TestCase):
    """Why a node is readable, not merely whether it is.

    The first real run found a keyboard node at mode 0666 -- granted by a
    tablet vendor's own udev rule, long before this project existed. Reporting
    that as "our rule is too wide" would have sent the reader to edit a rule
    that was not responsible.
    """

    def test_world_readable_is_not_ours(self):
        self.assertEqual(classify_grant(0o666, 104, [1000], True), GRANT_WORLD)

    def test_group_readable_when_we_are_in_that_group(self):
        self.assertEqual(classify_grant(0o660, 104, [1000, 104], True), GRANT_GROUP)

    def test_readable_despite_mode_bits_means_an_acl(self):
        self.assertEqual(classify_grant(0o660, 104, [1000], True), GRANT_ACL)

    def test_world_beats_acl_because_it_is_the_one_doing_the_work(self):
        self.assertEqual(classify_grant(0o666, 104, [1000, 104], True), GRANT_WORLD)

    def test_unreadable_is_reported_as_such_whatever_the_mode(self):
        self.assertEqual(classify_grant(0o666, 104, [1000], False), GRANT_NONE)

    def test_every_source_has_an_explanation(self):
        for source in (GRANT_WORLD, GRANT_GROUP, GRANT_ACL, GRANT_NONE):
            self.assertIn(source, GRANT_EXPLANATIONS)


class CapabilityBitmaskTest(unittest.TestCase):
    def test_mouse_buttons_decode_to_their_codes(self):
        bits = parse_capability_bitmask("0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 70000 0 0 0 0")
        self.assertEqual(bits, {0x110, 0x111, 0x112})

    def test_mouse_buttons_are_not_text_keys(self):
        self.assertEqual(text_keys({0x110, 0x111, 0x112}), set())

    def test_letters_and_digits_are_text_keys(self):
        # KEY_A=30, KEY_Z=44, KEY_1=2, KEY_SPACE=57
        self.assertEqual(text_keys({30, 44, 2, 57}), {30, 44, 2, 57})

    def test_media_keys_are_not_text_keys(self):
        # KEY_MUTE=113, KEY_VOLUMEDOWN=114, KEY_VOLUMEUP=115, KEY_PLAYPAUSE=164
        self.assertEqual(text_keys({113, 114, 115, 164}), set())

    def test_a_node_with_both_is_reported_by_its_text_keys_only(self):
        self.assertEqual(text_keys({0x110, 113, 30}), {30})

    def test_malformed_groups_are_skipped_rather_than_raising(self):
        self.assertEqual(parse_capability_bitmask("zzz 3"), {0, 1})

    def test_empty_bitmask(self):
        self.assertEqual(parse_capability_bitmask(""), set())
