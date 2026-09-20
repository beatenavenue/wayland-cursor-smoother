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

## What this is *not*: KWin's edge and corner barriers

KWin has settings with confusingly adjacent names that do **not** solve this
problem, and it is worth being explicit about them.

Since Plasma 6.1 KWin applies a deliberate *edge barrier* when the pointer
crosses between screens (`EdgeBarrier`, default 100px of resistance) and a much
stronger *corner barrier* within 15px of an output corner (`CornerBarrier`,
default on, 2000px — effectively impassable). The corner barrier exists to make
screen-edge and hot-corner actions reliably hittable on multi-monitor setups.

**These are the opposite of what this project wants.** They add resistance on
purpose. Turning them off:

```ini
# ~/.config/kwinrc
[EdgeBarrier]
CornerBarrier=false
EdgeBarrier=0
```

removes resistance where the pointer already has somewhere to go. It does not
create screen area where there is none. The problem in the screenshot above is
the vertical bands along an output edge that no neighbouring output covers —
genuine geometry, not added stickiness.

Worth doing anyway if the barriers annoy you, since it is free. It is not a
substitute for this project.

For contrast, Windows' *Ease cursor movement between displays* actively
redirects: on hitting a dead band it slides the pointer along the edge and into
the adjacent display. No KWin setting does that. That redirection is what this
project is for.

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
