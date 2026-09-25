"""User settings, and why they are settings rather than constants.

Two things here are matters of taste rather than correctness, and neither can
be settled by argument:

* **How far a push must travel before it counts.** Too low and the pointer
  leaves a display when the user only meant to reach its edge; too high and
  the feature feels unresponsive. Where that line sits depends on the device,
  the acceleration curve and the hand.
* **Whether the redirect is instantaneous or drawn.** Windows warps. A warp
  across a few hundred pixels is something the eye cannot follow, and this
  project has already measured how much difference watching the pointer move
  makes to being able to tell what happened. Which is *better* is a question
  about a person, not about a compositor.

So both are configuration, with defaults that are a starting point rather than
an answer.

INI, parsed with `configparser`, at `$XDG_CONFIG_HOME/wayland-cursor-smoother.conf`
-- the same shape as the `kwinrc` a KDE user already edits.

Validation is deliberately strict. A mistyped key that is silently ignored
leaves someone changing a number, seeing no effect, and concluding the
feature does not work; an unknown key is an error here, and so is a value
outside the range that makes sense.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

CONFIG_BASENAME = "wayland-cursor-smoother.conf"

GLIDE = "glide"
WARP = "warp"
STYLES = (GLIDE, WARP)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DetectConfig:
    """When a push counts. Units are device counts, not pixels."""

    threshold: float = 100.0
    window: float = 0.3
    cooldown: float = 0.5
    #: Seconds a warp stays undoable; ``None`` switches undo off, and is the
    #: default. See `UndoLatch` for why it is off, and why only a warp has it.
    undo_window: Optional[float] = None
    #: Percent. Redirect only where the display beyond faces at least this
    #: much; 0 redirects everywhere. See `geometry.facing_ratio`.
    min_facing: float = 0.0


@dataclass(frozen=True)
class RedirectConfig:
    """What the redirect looks like once it fires."""

    style: str = GLIDE
    duration: float = 0.08
    rate: float = 120.0
    inset: int = 2
    max_slide: Optional[float] = None


@dataclass(frozen=True)
class Config:
    detect: DetectConfig = DetectConfig()
    redirect: RedirectConfig = RedirectConfig()


_FIELDS = {
    "detect": {
        "threshold": ("float", lambda v: v > 0, "must be greater than 0"),
        "window": ("float", lambda v: v > 0, "must be greater than 0"),
        "cooldown": ("float", lambda v: v >= 0, "must not be negative"),
        "undo_window": ("optional_seconds", lambda v: v > 0,
                        "must be greater than 0, or off"),
        "min_facing": ("float", lambda v: 0 <= v <= 100, "must be from 0 to 100"),
    },
    "redirect": {
        "style": ("style", None, None),
        "duration": ("float", lambda v: v >= 0, "must not be negative"),
        "rate": ("float", lambda v: v > 0, "must be greater than 0"),
        "inset": ("int", lambda v: v >= 0, "must not be negative"),
        "max_slide": ("optional_float", lambda v: v > 0, "must be greater than 0"),
    },
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / CONFIG_BASENAME


def _validate(section: str, key: str, value):
    """Apply one field's range rule. Shared so that a value refused in the
    config file cannot be smuggled past on the command line -- which it could,
    until `--threshold 0` was found to be accepted while `threshold = 0` was
    not. Zero means "fire on contact", the behaviour the detector exists to
    prevent, so accepting it anywhere is a bug."""
    kind, check, complaint = _FIELDS[section][key]
    if kind == "style":
        if value not in STYLES:
            raise ConfigError(
                f"[{section}] {key}: {value!r} is not one of {', '.join(STYLES)}"
            )
        return value
    if kind in ("optional_float", "optional_seconds") and value is None:
        return None
    if kind == "optional_seconds":
        # `--undo-window 0` and `undo_window = off` have to mean the same
        # thing, so zero is spelled here rather than refused by the range.
        try:
            if float(value) == 0.0:
                return None
        except (TypeError, ValueError):
            pass

    # Coerce as well as check, so a value means the same thing whichever way
    # it arrived. Without this a flag stores an int where the file stores a
    # float, and two configs that should be equal are not.
    try:
        if kind == "int":
            coerced = int(value)
            if coerced != float(value):
                raise ConfigError(
                    f"[{section}] {key}: {value} must be a whole number"
                )
        else:
            coerced = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"[{section}] {key}: {value!r} is not a number") from None

    if check is not None and not check(coerced):
        raise ConfigError(f"[{section}] {key}: {coerced} {complaint}")
    return coerced


def _convert(section: str, key: str, raw: str):
    kind, check, complaint = _FIELDS[section][key]
    text = raw.strip()

    if kind == "style":
        return _validate(section, key, text)

    if kind == "optional_float" and text in ("", "none", "off", "unlimited"):
        return None

    if kind == "optional_seconds" and text in ("", "none", "off"):
        return None

    try:
        value = int(text) if kind == "int" else float(text)
    except ValueError:
        raise ConfigError(f"[{section}] {key}: {text!r} is not a number") from None

    return _validate(section, key, value)


def parse_config(text: str) -> Config:
    """Parse an INI document into a :class:`Config`.

    Every recognised key is optional; anything unrecognised is an error,
    because a setting that quietly does nothing is worse than one that
    refuses to start.
    """
    # No interpolation: it gives `%` a meaning, so `min_facing = 50%` raised
    # a traceback from configparser instead of an error naming the value.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigError(str(exc)) from None

    values: dict[str, dict] = {"detect": {}, "redirect": {}}
    for section in parser.sections():
        if section not in _FIELDS:
            known = ", ".join(_FIELDS)
            raise ConfigError(f"unknown section [{section}]; expected one of {known}")
        for key in parser[section]:
            if key not in _FIELDS[section]:
                known = ", ".join(_FIELDS[section])
                raise ConfigError(
                    f"unknown key [{section}] {key}; expected one of {known}"
                )
            values[section][key] = _convert(section, key, parser[section][key])

    return Config(
        detect=DetectConfig(**values["detect"]),
        redirect=RedirectConfig(**values["redirect"]),
    )


def load_config(path: Optional[Path] = None) -> Config:
    """Read the config file, or return defaults if there is not one."""
    path = path or config_path()
    if not path.exists():
        return Config()
    return parse_config(path.read_text())


def with_overrides(config: Config, **overrides) -> Config:
    """Apply command line overrides; ``None`` means "not given"."""
    unknown = set(overrides) - set(_FIELDS["detect"]) - set(_FIELDS["redirect"])
    if unknown:
        raise ConfigError(f"not settings: {', '.join(sorted(unknown))}")
    detect = {k: _validate("detect", k, v) for k, v in overrides.items()
              if v is not None and k in _FIELDS["detect"]}
    redirect = {k: _validate("redirect", k, v) for k, v in overrides.items()
                if v is not None and k in _FIELDS["redirect"]}
    return Config(
        detect=replace(config.detect, **detect) if detect else config.detect,
        redirect=replace(config.redirect, **redirect) if redirect else config.redirect,
    )


def default_config_text() -> str:
    """A documented config file holding exactly the current defaults."""
    d, r = DetectConfig(), RedirectConfig()
    return f"""\
# wayland-cursor-smoother
#
# Every value below is the built-in default, so a fresh copy of this file
# changes nothing. Delete any line to go back to the default for it.

[detect]
# How far the pointer device must travel outward, while the pointer is already
# pinned against a dead edge, before a redirect fires.
#
# The unit is DEVICE COUNTS, not pixels: pointer acceleration sits between the
# two, so there is no fixed conversion. What it measures is how far your hand
# moved. The default matches KWin's own EdgeBarrier setting, so it should feel
# like the resistance you are already used to.
#
# Lower it if the feature feels unresponsive. Raise it if the pointer leaves a
# display when you only meant to reach its edge.
threshold = {d.threshold:g}

# Seconds without outward motion after which a part-finished push is
# forgotten. Raising it lets you push in separate shoves; lowering it means
# only one continuous movement counts.
window = {d.window:g}

# Seconds after a redirect during which another cannot fire.
cooldown = {d.cooldown:g}

# Seconds after a warp during which moving `threshold` the other way puts the
# pointer back exactly where it was.
#
# You do not need this to go back. A redirect lands where the two displays
# meet, so there is no wall on the way back. Move the pointer back and it
# crosses over. It comes out at the end of the dead stretch, though, not at the
# exact point it left from. This setting closes that gap.
#
# It is off by default, because it does not check where the pointer is. Any
# movement back counts, anywhere on the screen. So a small correction far from
# the edge can send the pointer back across. If you want to try it, 3 is a
# reasonable value. A button press cancels it, because clicking means you meant
# to be there.
#
# `off` (or 0) disables it. **It applies to style = warp only.**
undo_window = {"off" if d.undo_window is None else format(d.undo_window, "g")}

# Only redirect when the facing ratio is at least this value (0 to 100).
# The facing ratio is how much of the neighbour's edge touches the display
# the pointer is on, in percent. 0 means always redirect. Raise it if the
# pointer jumps to another display when you expected it to stop.
min_facing = {d.min_facing:g}

[redirect]
# glide -- the pointer travels to its new position over `duration` seconds.
# warp  -- it arrives instantly, which is what Windows does.
#
# A warp is honest and costs nothing, but a few hundred pixels is far enough
# that the eye loses the pointer and has to find it again. A glide keeps it in
# view. Which reads better is a matter of taste; try both.
style = {r.style}

# Seconds a glide takes. Ignored when style = warp. Very short values behave
# like a warp; very long ones feel like the pointer is being dragged.
duration = {r.duration:g}

# Positions emitted per second while gliding. There is little point above the
# refresh rate of the display the pointer is crossing.
rate = {r.rate:g}

# How far inside the destination display to land, in pixels. Landing exactly
# on the outer edge is a pixel away from belonging to no display at all, so
# this is a small safety margin rather than a preference.
inset = {r.inset}

# Refuse to redirect when the pointer would have to slide further than this
# many pixels along the edge to reach the neighbour. Blank means no limit.
# Set it if a long slide feels less like a slide and more like a teleport.
max_slide =
"""
