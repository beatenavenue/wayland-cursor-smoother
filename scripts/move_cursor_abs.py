#!/usr/bin/env python3
"""
Usage:
  sudo apt install python3-evdev
  sudo ./scripts/move_cursor_abs.py --width 5120 --height 2160 2560 1080

This script creates a temporary uinput absolute-pointer device and
teleports the cursor to the requested coordinates.
"""

import argparse
import sys
import time

from evdev import UInput, ecodes, AbsInfo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Absolute cursor mover via python-evdev/uinput.",
    )
    parser.add_argument(
        "x",
        type=int,
        help="Absolute X coordinate in the virtual device range.",
    )
    parser.add_argument(
        "y",
        type=int,
        help="Absolute Y coordinate in the virtual device range.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=65535,
        help="Maximum X value exported by the virtual device (default: 65535).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=65535,
        help="Maximum Y value exported by the virtual device (default: 65535).",
    )
    parser.add_argument(
        "--name",
        default="wayland-cursor-smoother virtual pen",
        help="uinput device name (default: %(default)s).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not (0 <= args.x <= args.width and 0 <= args.y <= args.height):
        print(
            f"座標 ({args.x}, {args.y}) が指定範囲 "
            f"0..{args.width} x 0..{args.height} を超えています。",
            file=sys.stderr,
        )
        return 1

    events = {
        ecodes.EV_KEY: [ecodes.BTN_LEFT],
        ecodes.EV_ABS: [
            (
                ecodes.ABS_X,
                AbsInfo(
                    value=0,
                    min=0,
                    max=args.width,
                    fuzz=0,
                    flat=0,
                    resolution=1,
                ),
            ),
            (
                ecodes.ABS_Y,
                AbsInfo(
                    value=0,
                    min=0,
                    max=args.height,
                    fuzz=0,
                    flat=0,
                    resolution=1,
                ),
            ),
        ],
    }

    ui = UInput(events=events, name=args.name)
    try:
        time.sleep(0.1)
        ui.write(ecodes.EV_ABS, ecodes.ABS_X, args.x)
        ui.write(ecodes.EV_ABS, ecodes.ABS_Y, args.y)
        ui.syn()
        time.sleep(0.5)
    finally:
        ui.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
