"""Tests for the generated systemd user unit.

A unit file has no runtime signal when it is subtly wrong: systemd reads
`%h` as a specifier, silently does nothing for a `WantedBy` nobody reaches,
and a relative `ExecStart` fails with a message about the working directory
that sends the reader looking in the wrong place. So the properties that
matter are pinned here rather than left to whoever reads the output.

What is *not* pinned is the prose. The comments in the unit explain why the
readiness wait exists, and they are free to change.
"""

import configparser
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.kwinscript import KWIN_SERVICE, SCRIPTING_PATH  # noqa: E402
from wcs.service import (  # noqa: E402
    KWIN_UNIT,
    UNIT_NAME,
    default_unit_path,
    readiness_command,
    service_text,
)

EXEC = ["/usr/bin/python3", "/home/someone/src/wayland-cursor-smoother/bin/wcsd"]


def parse(text):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    return parser


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.text = service_text(EXEC, ready=["/usr/bin/qdbus6", "org.kde.KWin"])
        self.unit = parse(self.text)

    def test_it_parses_as_an_ini_document(self):
        self.assertEqual(set(self.unit.sections()), {"Unit", "Service", "Install"})

    def test_exec_start_runs_the_checkout_it_was_generated_from(self):
        self.assertEqual(self.unit["Service"]["ExecStart"], " ".join(EXEC))

    def test_every_executable_is_an_absolute_path(self):
        # systemd refuses a relative one, and a unit that fails to load is
        # read as "the service does not work" rather than "the file is wrong".
        for key in ("ExecStart", "ExecStartPre", "ExecReload"):
            first = self.unit["Service"][key].split()[0]
            self.assertTrue(Path(first).is_absolute(), f"{key}: {first}")

    def test_no_systemd_specifier_appears(self):
        # Paths are baked in, so a stray % can only be a mistake -- and an
        # unknown specifier makes systemd refuse the whole unit.
        self.assertNotIn("%", self.text)

    def test_it_starts_after_kwin_and_stops_with_the_session(self):
        self.assertEqual(self.unit["Unit"]["After"], KWIN_UNIT)
        self.assertEqual(self.unit["Unit"]["PartOf"], "graphical-session.target")
        self.assertEqual(self.unit["Install"]["WantedBy"], "graphical-session.target")

    def test_it_waits_for_kwin_before_starting(self):
        # The daemon logs a failed script load and carries on, so losing this
        # race produces a healthy-looking process that receives nothing.
        pre = self.unit["Service"]["ExecStartPre"]
        self.assertIn("/usr/bin/qdbus6 org.kde.KWin", pre)
        self.assertIn("timeout", pre)

    def test_reload_sends_sighup_rather_than_restarting(self):
        # SIGHUP re-reads the layout in place; a restart would drop the
        # virtual pointer and the feed script to achieve the same thing.
        self.assertIn("-HUP", self.unit["Service"]["ExecReload"])
        self.assertIn("$MAINPID", self.unit["Service"]["ExecReload"])

    def test_a_failed_start_is_retried_but_not_spun(self):
        self.assertEqual(self.unit["Service"]["Restart"], "on-failure")
        self.assertGreaterEqual(float(self.unit["Service"]["RestartSec"]), 1)


class ReadinessCommandTest(unittest.TestCase):
    """Whichever D-Bus tool the daemon will use to load the script is the one
    the gate should ask with: a machine where the gate passes is then a
    machine where the load works."""

    def test_qdbus_takes_a_service_and_a_path(self):
        self.assertEqual(
            readiness_command("/usr/bin/qdbus6"),
            ["/usr/bin/qdbus6", KWIN_SERVICE, SCRIPTING_PATH])

    def test_gdbus_needs_its_own_spelling(self):
        command = readiness_command("/usr/bin/gdbus")
        self.assertEqual(command[:2], ["/usr/bin/gdbus", "introspect"])
        self.assertIn(KWIN_SERVICE, command)
        self.assertIn(SCRIPTING_PATH, command)

    def test_busctl_is_the_fallback(self):
        # systemd ships it, and we are writing a systemd unit, so it is the
        # one tool that cannot be missing here.
        command = readiness_command(None)
        self.assertTrue(command[0].endswith("busctl"), command)
        self.assertIn(KWIN_SERVICE, command)


class UnitPathTest(unittest.TestCase):
    def test_it_lands_where_systemd_looks_for_user_units(self):
        path = default_unit_path()
        self.assertEqual(path.name, UNIT_NAME)
        self.assertEqual(path.parent.parts[-2:], ("systemd", "user"))


if __name__ == "__main__":
    unittest.main()
