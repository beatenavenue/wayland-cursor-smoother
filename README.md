# wayland-cursor-smoother
KDE/Wayland Screen-Edge Cursor Smoother

> [!WARNING]
> **Status: WIP — research only. No implementation exists yet.**
>
> This repository contains no code. What follows is a problem statement and a
> record of which approaches have been investigated and ruled out. Nothing here
> runs.

## Motivation

![motivation](img/motivation.png)

When building a multi-display setup, the usual best practice is to line up identical monitors so the overall desktop area forms a perfect rectangle, avoiding “dead corners” where the pointer cannot pass. In reality—because of desk space constraints, reuse of older hardware, and other reasons—complex, non-rectangular desktop geometries are sometimes unavoidable.

**wayland-cursor-smoother** aims to let the pointer glide smoothly across discontinuities at display corners, similar to Windows 11’s “Ease cursor movement between displays” option. Ideally this should be a baseline capability of Wayland itself; I hope this program becomes unnecessary sooner rather than later.

## Check your KWin settings first

Some of this pain may not be geometry at all. Since Plasma 6.1 KWin applies a
deliberate *edge barrier* when the pointer crosses between screens, and a much
stronger *corner barrier* near the corners of an output — within 15px
(Manhattan) of a corner the resistance is **2000px**, which is effectively
impassable.

From `src/pointer_input.cpp`:

```cpp
} else if (options->cornerBarrier() && onCorner) {
    return EdgeBarrierType::CornerBarrier;
}
...
case EdgeBarrierType::CornerBarrier:
    return 2000;
```

Both are configurable, and both are on by default (`CornerBarrier=true`,
`EdgeBarrier=100`). In `~/.config/kwinrc`:

```ini
[EdgeBarrier]
CornerBarrier=false
EdgeBarrier=0
```

or via System Settings → Mouse & Touchpad → Screen Edges.

This cannot conjure up screen area that does not exist — where an output edge
has no neighbouring output opposite it, the pointer still has nowhere to go.
But it does remove resistance that KWin is adding on purpose, and the awkward
spots in the screenshot above are all output *corners*. Try this before
reaching for any of the approaches below.

## Approaches investigated and rejected

### The InputCapture portal — rejected

`org.freedesktop.portal.InputCapture` can place the pointer at an arbitrary
position: `Release()` accepts a `cursor_position` option, which
xdg-desktop-portal-kde forwards to KWin, which warps the pointer. Its pointer
barriers even trigger on exactly the right condition — the pointer pinned
against an edge while the user keeps pushing.

It is unusable here anyway, because xdg-desktop-portal-kde raises a
notification on *every* activation:

```ini
[Event/inputcapturestarted]
Comment=Input is now being managed by an application
Action=Popup
Urgency=High
```

> **Input Capture Started** — *&lt;app&gt; has taken over the pointer and keyboard.
> Press Meta+Shift+Esc to stop this.*

KWin additionally hides the cursor for the duration of each capture. Here
"activation" means "the user bumped a corner", so normal desktop use would
produce a high-urgency popup and a cursor blink every few seconds. That is a
far worse experience than the problem it sets out to fix.

### The RemoteDesktop portal — rejected

`org.freedesktop.portal.RemoteDesktop` can position the pointer absolutely, but
doing so requires an associated ScreenCast stream, which raises the
screen-sharing indicator for as long as the session lives. Running a permanent
"your screen is being shared" state in exchange for smoother pointer movement
is not a reasonable trade.

Note that the screen-sharing indicator belongs to ScreenCast/RemoteDesktop, not
to InputCapture — the two portals are rejected for different reasons.

## Scope

KDE Plasma on Wayland only. To the best of my knowledge there is no comparable
way to do this on GNOME.
