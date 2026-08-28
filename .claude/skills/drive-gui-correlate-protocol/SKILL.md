---
name: drive-gui-correlate-protocol
description: Drive a native macOS app's GUI (screenshot + click + type) from the agent and correlate each action with the protocol traffic it produces, for scenario testing and reversing GUI operations you can't guess. Use when you have a working capture log (e.g. a DYLD interposer) and need to learn what messages a specific button/gesture emits, or to automate exploration of an app whose actions you must observe on the wire.
---

# Drive a macOS app's GUI and correlate it with captured protocol

Once you can *capture* an app's device/network traffic (see `macos-dylib-interpose`),
the fastest way to reverse a GUI operation ("delete a split", "load preset", "assign
to scene") is to **perform exactly that one action and diff the messages**. Automating
the GUI lets the agent do this itself: screenshot → decide → click → screenshot →
decode the log delta. The agent reasons over the image it just captured and clicks in
that image's pixel space.

## Architecture
Three permission-free-to-mostly-free primitives plus your existing capture log:
1. **Locate the window** — `CGWindowListCopyWindowInfo` (pyobjc/Quartz) returns each
   window's owner name + bounds **in points**. No permission needed.
2. **Screenshot the window region** — `screencapture -x -o -R x,y,w,h out.png`.
   Needs **Screen Recording**.
3. **Click / type / keys** — `cliclick c:x,y` / `t:text` / `kp:key` (`brew install
   cliclick`). Needs **Accessibility**.
4. **Correlate** — before each action, record the capture log's byte offset; after,
   read the appended bytes, reassemble frames, and name the commands. The action's
   protocol footprint is the delta. (Reuse your framing/decoder — see
   `reverse-framed-protocol`.)

Wrap these in one CLI so the agent runs `act <px> <py>`: mark log → click → settle →
screenshot → return decoded messages. This repo's implementation is `tools/gui/gui.py`.

## Coordinate model (the part that bites)
CGWindowList bounds are **points**; on a Retina display `screencapture` writes the
region at the backing scale (2x → the png is 2x the point size). So map a target the
agent picked at **png-pixel** (px,py) back to a screen **point** for cliclick:

    scale     = png_width_px / window_width_pt          # 2.0 on Retina
    screen_x  = window.x + px / scale
    screen_y  = window.y + py / scale

Have the click helper take png-pixel coords and convert, so the agent always reasons in
the pixel space of the screenshot it just looked at. (If the agent's image viewer
downscales the png, multiply its coords by png_width / shown_width first.)

## macOS gotchas
- **TCC is attributed to the *responsible* app**, not the CLI. If the agent runs under
  a desktop app (e.g. Claude.app), grant Screen Recording + Accessibility to **that
  app**, not Terminal. They are **separate toggles** — enabling one doesn't enable the
  other. **Screen Recording needs an app restart** to take effect; Accessibility is
  usually immediate. Verify: `screencapture` succeeds; `cliclick p:.` prints coords
  with no "Accessibility privileges not enabled" warning.
- **Signature of denial:** `screencapture` prints "could not create image from
  rect/display" and exits non-zero when Screen Recording is off.
- **Multi-display scaling (silent mis-clicks):** clicks only map reliably when the
  window is on the **main** display. `screencapture -R` samples *any* region at the
  main display's scale, so a window parked on a 1x external monitor yields a 2x png
  while cliclick clicks in the monitor's 1x point space → the click lands in the wrong
  place while the screenshot still looks right. Detect (is the window origin inside
  `CGDisplayBounds(CGMainDisplayID())`?) and **re-home** the window to the main display
  before driving. `CGDisplayPixelsWide/High` can report the scaled (logical) size, so
  don't trust it for backing scale — derive scale from `png_width / window_width_pt`.
- **Move a window without fragile clicks** via the Accessibility API: set `AXPosition`
  on the app's `AXWindows[0]` (`AXUIElementCreateApplication(pid)` →
  `AXUIElementSetAttributeValue(win, "AXPosition", AXValueCreate(kAXValueCGPointType,
  CGPoint(x,y)))`). Needs `pyobjc-framework-ApplicationServices` + Accessibility.
- **Let the app breathe:** sleep ~0.5s after a click before screenshotting/reading the
  log, so the app has emitted its messages.

## Method for reversing one operation
1. Re-home the window; screenshot; confirm the agent sees the target control.
2. `act` the control. Prefer a **single isolated action** — one click that maps to one
   logical operation → an unambiguous message diff.
3. Read the decoded delta: the new host→device message(s) are the operation. Confirm by
   repeating with a different value and diffing (e.g. removing a split was just
   `split_control_points=(-1,-1)`).
4. Use **non-destructive** probes first (select/hover/open-a-menu, Escape to back out)
   before actions that change device or file state.

## Gotchas
- Capture logs may contain session ids / auth tokens — keep them out of version control.
- Screenshots of the app can contain personal data (preset names, accounts) — treat as
  private.
- This is for interop/debugging on software you're licensed to run and hardware you own.

Reference: `tools/gui/gui.py` (`bounds`/`home`/`shot`/`click`/`type`/`key`/`act`/
`decode`) drives Cortex Control and decodes the interposer log via
`qc_mcp.protocol.COMMANDS`. See PROTOCOL.md and `macos-dylib-interpose`.

## Staying out of the user's way

Driving a GUI normally means hijacking the screen — fronting the app over the
user's windows, moving their cursor, stealing focus. Most of that is avoidable.
Three channels, cheapest first; only the last one touches the screen at all.

**1. The protocol.** If the goal is device state, skip the app entirely and read
or write it over the bridge. Several ops (per-scene bypass, momentary flags,
unassign) were reversed purely by sending variants and observing state — no GUI.
Reach for the GUI only for ops that exist *only* in the app.

**2. The accessibility tree** — `gui.py ax [query]`. JUCE publishes a real AX
tree: labelled controls (`AXHelp` "Scene A: Undefined", "Save preset", "Next
preset"), live values (`AXValue` On/Off, tempo, CPU), and exact screen frames.
It needs **no capture, no focus, and no fronting**, works with the window buried
or parked off-screen, and mirrors device state live — so it is usually a better
verification channel than pixels, and it gives click targets by name instead of
hand-read coordinates.

**3. Window-id capture** — `gui.py shot`. `screencapture -l <winid>` renders that
window alone straight from the window server: occluding windows don't appear,
focus doesn't move, and it works when the window is behind everything or parked
off every display. The `-R x,y,w,h` form people reach for first grabs a screen
*region* instead, so whatever is on top lands in the image and swallows the
click — that failure looks exactly like a broken permission.

**Clicking is the exception.** JUCE ignores `AXUIElementPerformAction(kAXPress)`
— it returns success and can even move the widget visually while the device
never hears about it, desyncing the app from the hardware — and it ignores
`CGEventPostToPid` too. It wants a real HID click with the app active. So
`gui.py press "<name>"` finds the control via AX, activates the app, clicks its
frame, then returns the cursor and focus to whatever had them. Under two seconds.

**Practical pattern:** `park` the window off-screen and work over the protocol +
`ax` + `shot`. When an app-only op must be captured, batch the clicks into one
short session: `home`, mark the log, click, decode, `park` again. And when even
that is unwelcome, capture *passively* — ask the user to perform the action once
whenever convenient, then mine the interposer log for what it emitted.
