# wayland-cursor-smoother
KDE/Wayland Screen-Edge Cursor Smoother

> [!WARNING]
> **Status: WIP — research only. No implementation exists yet.**
>
> This repository contains no code. What follows is a problem statement and a
> record of which approaches have been investigated and ruled out.
>
> The findings are written to be useful on their own. If this project is never
> finished, the section below on KWin's corner barrier should still save
> somebody the day it cost me.

## Motivation

![motivation](img/motivation.png)

When building a multi-display setup, the usual best practice is to line up identical monitors so the overall desktop area forms a perfect rectangle, avoiding “dead corners” where the pointer cannot pass. In reality—because of desk space constraints, reuse of older hardware, and other reasons—complex, non-rectangular desktop geometries are sometimes unavoidable.

**wayland-cursor-smoother** aims to let the pointer glide smoothly across discontinuities at display corners, similar to Windows 11’s “Ease cursor movement between displays” option. Ideally this should be a baseline capability of Wayland itself; I hope this program becomes unnecessary sooner rather than later.

## If you found `CornerBarrier` and thought that was the fix

It is not. This trips up nearly everyone who searches for this problem,
including me, and it is convincing enough that people announce it as the
solution before testing it properly.

### Why it looks like the answer

Search for a stuck cursor between KDE monitors and you will find this:

```ini
# ~/.config/kwinrc
[EdgeBarrier]
CornerBarrier=false
EdgeBarrier=0
```

The names match the symptom. Plasma 6.1 did introduce these. They genuinely do
govern pointer movement between screens. And when you apply them, **the
behaviour visibly changes** — which is exactly what makes the trap work.

### What they actually do

Both add resistance *on purpose*:

- `EdgeBarrier` (default 100) — the pointer must be pushed this far past a
  screen edge before it crosses to the adjacent screen. Intended to stop you
  overshooting onto the next monitor by accident.
- `CornerBarrier` (default on) — within 15px (Manhattan) of an output corner
  the resistance becomes 2000px, effectively impassable. Intended to make
  hot-corner and screen-edge actions reliably hittable, instead of sliding onto
  the neighbouring screen before you can trigger them.

Turning them off removes resistance **where the pointer already had somewhere
to go**. That is a real improvement, and it is why people believe they have
solved it. Then they hit the dead band again.

These settings cannot create screen area that does not exist.

### Telling the two problems apart

Run the pointer slowly along the edge shared with the next monitor and push
outward at each height:

- **It crosses once you push firmly** → that is the barrier. The settings above
  will fix it, and you are done.
- **There is a stretch of that edge where it never crosses, no matter how hard
  or how slowly you push** → that is geometry. Your monitors do not overlap at
  those heights, so there is no destination to move to. No KWin setting will
  help, because nothing is in the way — there is simply nothing on the other
  side.

The red bands in the screenshot above are the second kind. They mark the
stretches of the centre display's edges that the neighbouring displays do not
span.

### What would actually solve it

Windows' *Ease cursor movement between displays* does not remove resistance —
it **redirects**. On reaching a stretch of edge with no neighbour, it slides the
pointer along that edge and into the nearest valid point of the adjacent
display.

Removing resistance is not the same as adding redirection. Nothing in KWin
currently does the latter. That is what this project is for.

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
