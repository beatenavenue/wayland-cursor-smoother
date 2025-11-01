# wayland-cursor-smoother
KDE/Wayland Screen-Edge Cursor Smoother

## Motivation

![motivation](img/motivation.png)

When building a multi-display setup, the usual best practice is to line up identical monitors so the overall desktop area forms a perfect rectangle, avoiding “dead corners” where the pointer cannot pass. In reality—because of desk space constraints, reuse of older hardware, and other reasons—complex, non-rectangular desktop geometries are sometimes unavoidable.

**wayland-cursor-smoother** aims to let the pointer glide smoothly across discontinuities at display corners, similar to Windows 11’s “Ease cursor movement between displays” option. Ideally this should be a baseline capability of Wayland itself; I hope this program becomes unnecessary sooner rather than later.

## Approach

This project uses two components:

- A KWin script, **Cursor Feed**, which publishes pointer positions over D-Bus.
- A Python helper, **glide_cursor**, which consumes those positions and, when the pointer is blocked by a display edge, “slides” it onto the adjacent screen.

To the best of my knowledge there is currently no easy way to implement the same approach on GNOME. This is KDE/Wayland-only.
