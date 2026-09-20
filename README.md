# wayland-cursor-smoother
KDE/Wayland Screen-Edge Cursor Smoother

> [!WARNING]
> **Status: WIP — nothing here has been confirmed on real hardware yet.**
>
> There is no working tool. What exists is a problem statement, a record of
> which approaches have been ruled out, the geometry that decides where the
> pointer should land, and a probe that answers whether the one remaining
> approach is possible on a given machine.
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

## The approach being tried

A virtual **absolute** pointer on `/dev/uinput`, driven from outside the
compositor.

Absolute pointer motion is the part that makes this possible. KWin scales an
absolute pointer event against the geometry of the *whole* layout, so a single
event can place the pointer anywhere on the desktop; it skips the edge barrier
outright ("edge barriers are counter-productive for absolute motion"); and it
is validated by its destination rather than by the path taken to get there.
Unlike XTest, a uinput device is an ordinary evdev device as far as libinput
and KWin are concerned, so nothing treats its events as untrusted. No portal
is involved, so there is no permission dialog, no notification and no hidden
cursor.

The whole approach rests on one thing: **libinput has to classify the virtual
device as a pointer.** Absolute axes alone are not enough — a device that also
looks like a tablet or a touchscreen gets bound to a single display instead,
and then it can never reach the display the pointer is supposed to move to.
Declaring `ABS_X`/`ABS_Y` and a mouse button, and nothing else, is what puts
it on the pointer path. This is the same shape as the absolute USB pointers
that VMware and QEMU present to their guests.

Note that this only concerns a *virtual* device. An ordinary graphics tablet
is confined to one display because it is a tablet, not because Wayland's
coordinates are per-display — a distinction that is easy to get backwards, and
discouraging in the wrong direction if you do.

### Checking your own layout

This needs no special permissions, creates no device and moves nothing:

```console
$ python3 tools/probe_uinput.py --layout-only
```

It reads your layout with `kscreen-doctor` and lists every stretch of every
display edge that has no display behind it, along with where the pointer
*would* be sent. If that list does not match the places your pointer actually
gets stuck, the problem is not the one described above.

### Checking whether the approach can work at all

```console
$ python3 tools/probe_uinput.py
```

This additionally creates the virtual pointer, reports how udev and libinput
classified it, and — if it passed — demonstrates the intended behaviour by
placing the pointer in each dead band and moving it to the computed landing
point. Watch what it does: arriving at the nearest point along the edge is the
goal, arriving at the centre of a display is a failure.

Writing to `/dev/uinput` requires membership of the group that owns it, which
is `input` on most distributions.

### Tests

```console
$ python3 -m unittest discover -s tests
```

Standard library only. These cover the layout maths and the landing
semantics — the parts that can be decided without a display attached.

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
