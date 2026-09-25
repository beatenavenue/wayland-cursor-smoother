"""Tests for user settings.

The point of most of these is not that a good config is accepted -- it is
that a bad one is *refused*, loudly. A mistyped key that is silently ignored
leaves someone changing a number, seeing no effect, and concluding the feature
is broken.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.config import (  # noqa: E402
    GLIDE,
    WARP,
    Config,
    ConfigError,
    DetectConfig,
    RedirectConfig,
    config_path,
    default_config_text,
    load_config,
    parse_config,
    with_overrides,
)


class DefaultsTest(unittest.TestCase):
    def test_the_shipped_file_is_exactly_the_defaults(self):
        # If these drift apart, someone copies the documented file and gets
        # different behaviour from someone who has no file at all.
        self.assertEqual(parse_config(default_config_text()), Config())

    def test_an_empty_document_is_the_defaults(self):
        self.assertEqual(parse_config(""), Config())

    def test_every_setting_is_documented_in_the_shipped_file(self):
        text = default_config_text()
        for name in list(DetectConfig.__dataclass_fields__) + \
                list(RedirectConfig.__dataclass_fields__):
            self.assertIn(name, text, f"{name} is not mentioned in the config file")


class ParseTest(unittest.TestCase):
    def test_a_partial_document_leaves_the_rest_alone(self):
        config = parse_config("[detect]\nthreshold = 60\n")
        self.assertEqual(config.detect.threshold, 60.0)
        self.assertEqual(config.detect.window, DetectConfig().window)
        self.assertEqual(config.redirect, RedirectConfig())

    def test_both_styles_are_accepted(self):
        self.assertEqual(parse_config(f"[redirect]\nstyle={GLIDE}\n").redirect.style, GLIDE)
        self.assertEqual(parse_config(f"[redirect]\nstyle={WARP}\n").redirect.style, WARP)

    def test_max_slide_can_be_switched_off_by_several_spellings(self):
        for text in ("", "none", "off", "unlimited", "  "):
            config = parse_config(f"[redirect]\nmax_slide = {text}\n")
            self.assertIsNone(config.redirect.max_slide, repr(text))

    def test_max_slide_takes_a_number(self):
        self.assertEqual(parse_config("[redirect]\nmax_slide = 400\n").redirect.max_slide, 400.0)

    def test_min_facing_defaults_to_redirecting_everywhere(self):
        self.assertEqual(Config().detect.min_facing, 0.0)

    def test_min_facing_takes_a_percentage_including_both_ends(self):
        for text, value in (("0", 0.0), ("50", 50.0), ("33.3", 33.3), ("100", 100.0)):
            config = parse_config(f"[detect]\nmin_facing = {text}\n")
            self.assertEqual(config.detect.min_facing, value, text)


class RefusalTest(unittest.TestCase):
    def assert_refused(self, text, *expected_words):
        with self.assertRaises(ConfigError) as caught:
            parse_config(text)
        message = str(caught.exception).lower()
        for word in expected_words:
            self.assertIn(word.lower(), message, message)

    def test_an_unknown_section_names_the_known_ones(self):
        self.assert_refused("[detction]\nthreshold=1\n", "unknown section", "detect")

    def test_an_unknown_key_names_the_known_ones(self):
        self.assert_refused("[detect]\nthreshhold = 60\n",
                            "unknown key", "threshhold", "threshold")

    def test_a_non_number_is_refused_with_the_offending_text(self):
        self.assert_refused("[detect]\nthreshold = soon\n", "threshold", "soon")

    def test_an_unknown_style_lists_the_real_ones(self):
        self.assert_refused("[redirect]\nstyle = teleport\n", "teleport", "glide", "warp")

    def test_a_zero_threshold_is_refused(self):
        # It would fire on the first event, which is the contact-triggered
        # behaviour the detector exists to avoid.
        self.assert_refused("[detect]\nthreshold = 0\n", "greater than 0")

    def test_negative_values_are_refused(self):
        self.assert_refused("[detect]\nwindow = -1\n", "greater than 0")
        self.assert_refused("[detect]\ncooldown = -0.5\n", "not be negative")
        self.assert_refused("[redirect]\nduration = -1\n", "not be negative")
        self.assert_refused("[redirect]\ninset = -3\n", "not be negative")

    def test_a_zero_rate_is_refused(self):
        self.assert_refused("[redirect]\nrate = 0\n", "greater than 0")

    def test_min_facing_outside_0_to_100_is_refused(self):
        self.assert_refused("[detect]\nmin_facing = -1\n", "from 0 to 100")
        self.assert_refused("[detect]\nmin_facing = 101\n", "from 0 to 100")

    def test_min_facing_is_a_plain_number_not_a_percent_sign(self):
        self.assert_refused("[detect]\nmin_facing = 50%\n", "not a number")

    def test_a_zero_max_slide_is_refused_rather_than_read_as_off(self):
        # "off" has spellings; 0 would mean "never redirect", which nobody
        # means to write.
        self.assert_refused("[redirect]\nmax_slide = 0\n", "greater than 0")

    def test_malformed_ini_is_refused(self):
        self.assert_refused("this is not ini", "")

    def test_zero_duration_is_allowed_because_it_is_just_a_warp(self):
        self.assertEqual(parse_config("[redirect]\nduration = 0\n").redirect.duration, 0.0)


class OverrideTest(unittest.TestCase):
    def test_a_given_value_wins_over_the_file(self):
        config = parse_config("[detect]\nthreshold = 60\n")
        self.assertEqual(with_overrides(config, threshold=200).detect.threshold, 200)

    def test_none_means_not_given(self):
        config = parse_config("[detect]\nthreshold = 60\n")
        self.assertEqual(with_overrides(config, threshold=None).detect.threshold, 60.0)

    def test_overrides_reach_both_sections(self):
        config = with_overrides(Config(), threshold=50, style=WARP)
        self.assertEqual(config.detect.threshold, 50)
        self.assertEqual(config.redirect.style, WARP)

    def test_no_overrides_returns_an_equal_config(self):
        self.assertEqual(with_overrides(Config()), Config())

    def test_an_override_that_is_not_a_setting_is_refused(self):
        with self.assertRaises(ConfigError):
            with_overrides(Config(), colour="blue")


class LoadTest(unittest.TestCase):
    def test_a_missing_file_gives_the_defaults(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(load_config(Path(tmp) / "absent.conf"), Config())

    def test_an_existing_file_is_read(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "wcs.conf"
            path.write_text("[redirect]\nstyle = warp\n")
            self.assertEqual(load_config(path).redirect.style, WARP)

    def test_the_path_follows_xdg_config_home(self):
        import os
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg-test"
        try:
            self.assertEqual(str(config_path()),
                             "/tmp/xdg-test/wayland-cursor-smoother.conf")
        finally:
            if previous is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = previous


if __name__ == "__main__":
    unittest.main()


class OverrideValidationTest(unittest.TestCase):
    """A command line flag must not accept what the config file refuses.

    Found by running `wcsd --threshold 0 --check`: it was accepted, while
    `threshold = 0` in the file was not. Zero means fire on contact, which is
    the behaviour the detector exists to prevent, so the two paths share one
    set of rules now.
    """

    def test_a_zero_threshold_is_refused_on_the_command_line_too(self):
        with self.assertRaises(ConfigError):
            with_overrides(Config(), threshold=0)

    def test_every_range_rule_applies_to_overrides(self):
        for field, bad in (
            ("threshold", 0), ("threshold", -5),
            ("window", 0), ("cooldown", -1),
            ("duration", -0.1), ("rate", 0), ("inset", -1), ("max_slide", 0),
            ("min_facing", -1), ("min_facing", 100.5),
        ):
            with self.assertRaises(ConfigError, msg=f"{field}={bad}"):
                with_overrides(Config(), **{field: bad})

    def test_an_unknown_style_is_refused_on_the_command_line_too(self):
        with self.assertRaises(ConfigError):
            with_overrides(Config(), style="teleport")

    def test_valid_overrides_still_apply(self):
        config = with_overrides(Config(), threshold=250, style=WARP, max_slide=500)
        self.assertEqual(config.detect.threshold, 250)
        self.assertEqual(config.redirect.style, WARP)
        self.assertEqual(config.redirect.max_slide, 500)

    def test_the_file_and_the_flag_refuse_with_the_same_message(self):
        with self.assertRaises(ConfigError) as from_file:
            parse_config("[detect]\nthreshold = 0\n")
        with self.assertRaises(ConfigError) as from_flag:
            with_overrides(Config(), threshold=0)
        self.assertEqual(str(from_file.exception), str(from_flag.exception))

    def test_an_override_is_coerced_to_the_type_the_file_would_produce(self):
        # Otherwise a flag stores an int where the file stores a float, and
        # two configs that should be equal are not.
        self.assertEqual(with_overrides(Config(), threshold=250),
                         parse_config("[detect]\nthreshold = 250\n"))
        self.assertIsInstance(with_overrides(Config(), threshold=250).detect.threshold,
                              float)
        self.assertIsInstance(with_overrides(Config(), inset=4).redirect.inset, int)

    def test_a_fractional_pixel_inset_is_refused_rather_than_truncated(self):
        with self.assertRaises(ConfigError):
            with_overrides(Config(), inset=2.7)

    def test_a_non_number_override_is_refused(self):
        with self.assertRaises(ConfigError):
            with_overrides(Config(), threshold="soon")
