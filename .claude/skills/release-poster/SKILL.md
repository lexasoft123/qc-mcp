---
name: release-poster
description: Make the Patchbay release poster and the channel post text for a version — a vertical collage of real app interface highlights plus short EN/RU copy. Use this whenever the user asks for a release poster, a Telegram/WhatsApp post or announcement for a release, an "announce v0.x" image, channel copy for a new version, or a visual to go with release notes — and whenever a release is being cut and the channel needs telling. Also use it to redo or restyle an existing poster.
---

# Patchbay release poster

Adapted from the SingZ skill of the same name. The direction and the rules are
that skill's; what changed is that Patchbay is a **desktop app only**, so every
fragment is an app window rather than a phone, and the capture is driven over
CDP rather than by a simulator harness.

Two deliverables, and they carry different loads:

1. **The poster** — a 4:5 image, real interface fragments collaged, read at the
   size of a phone chat column.
2. **The post text** — English and Russian, short, in the release-notes voice.

The split matters. The poster is seen at ~400 px wide and carries **very few,
very large words**; the prose lives in the caption, where the reader gets it at
native size. Trying to fit the release notes onto the image is the single
easiest way to produce something nobody can read.

## Before anything else: what release is this?

Read `docs/release-notes/v<version>.md`. It is the source of truth for what
shipped, and its first line is the release's own framing. Pick the **two or
three** changes a guitarist would actually notice — not the longest section, not
the cleverest engineering. If the notes do not exist yet, they get written
first; a poster for a release nobody has described is guesswork.

## Capture: real UI only

Every fragment is a screenshot of the running app. Never mock up, redraw or
"clean" a screen — this repo verifies by driving the real app, and a poster of
invented UI is a claim about the product that no test covers.

**Stage a real session first.** The app has to be genuinely connected, or the
poster shows an empty state: Quad Cortex on USB, Cortex Control up, the daemon
running in bridge mode. A poster of "no device found" announces nothing.

Launch the packaged app with a debugging port and drive it over CDP:

```bash
open -a /Applications/Patchbay.app --args --remote-debugging-port=9333
# then, per view: click the tab, wait, screencapture -l <window id>
```

`docs/patchbay/*.png` are the README screenshots and are usually current enough
to reuse — check them against the build being announced before you do. If the
change being announced is in the main process, the installed build must postdate
it: a stale binary photographs the old app perfectly.

**Read every screenshot you take.** Look for: a home directory in a path (the
app collapses `$HOME` to `~`, but only where `paths.show` is used), an empty
state where you expected content, a daemon that says stopped.

## The zoomed detail is what sells it

At chat-column size a whole app window is texture — pleasant, unreadable, and it
proves nothing. What lands is **one control, blown up**: the device card, the
mode selector, the reports/s readout. Crop it tight and give it the accent glow.

**Measure the crop, never eyeball it.** Uneven padding around a control is
instantly visible once it is floating on a dark ground. Two ways:

```bash
# by accent colour, when the control is painted with it
python3 .claude/skills/release-poster/scripts/find-control.py docs/patchbay/console.png --pad 26
# ...or ask the DOM, which is exact and works for controls the accent misses
#    (the mode selector's active pill is a background tint, not a fill)
```

Getting the box off `getBoundingClientRect()` and multiplying by the DPR is the
reliable route here — the app is ours, so the element is addressable.

**Do not zoom something that is already legible in a fragment.** A plate that
repeats the fragment behind it reads as a duplicate. The first attempt zoomed
Home's signal path with Home in the big slot; the device card from Console works
because at 430 px that card is texture.

## Compose

Start from `assets/poster-template.html`. Replace `__REPO__` with the repo path
(the brand fonts load from `app/node_modules/@fontsource-variable/…`),
`__SHOTS__` with the prepped fragment directory, `__VERSION__` with the tag.

The direction is a **studio contact sheet**: fragments pinned at angles, deep
shadows, a warm stage bloom, mono annotations like a producer's markup. Palette
and faces are the app's own tokens (`--sz-accent #ffa028`, `--sz-bg #12100d`,
Bricolage Grotesque, Martian Mono). Do not invent a palette; the product has one.

**Minimum sizes on the 1024-wide canvas** (a chat renders it ~400 px, so divide
by ~2.5 for what the reader gets):

| element | size | why |
| --- | --- | --- |
| headline | 90–100 px | the only thing guaranteed to be read |
| subhead | 34–38 px | one line, not two |
| bullets | 30–34 px | three lines, ≤ 6 words each |
| mono labels | 22–25 px | below this they are decoration |

Composition rules that came from getting them wrong here:

- **Never cut a fragment mid-word.** A plate sliced through text reads as a
  broken export. The first draft had the Home window at `left: -74px`, through
  its own wordmark. Remember the rotation swings the corners ~13 px further out
  than the `left` value suggests, so the inset has to clear that too.
- **A window fragment keeps its own aspect.** These are 1960×1432 (ratio 1.369).
  Set a width and let the height follow; never crop one shorter to fit a box, or
  it stops reading as a window.
- **Keep the collage in its own fixed-height box**, or fragments land on the
  bullets and hide them.

The template's collage CSS is split into a **STRUCTURE** block and a
**POSITION** block. Re-compose POSITION freely; keep STRUCTURE verbatim.

## Sharpness: resize once, render 1:1

A soft poster is the most common failure, and the cause is always the same — the
same pixels resampled two or three times. Do the reduction **once**, before
rendering:

```bash
bash .claude/skills/release-poster/scripts/prep-fragments.sh <shots> <shots-1x> \
     .claude/skills/release-poster/assets/fragment-widths.json
node .claude/skills/release-poster/scripts/check-widths.cjs <poster.html> \
     .claude/skills/release-poster/assets/fragment-widths.json     # want: ALL FRAGMENTS 1:1
node .claude/skills/release-poster/scripts/render.cjs <poster.html> <out> v<version>-poster [--2x]
```

If you move a fragment or change its width in the CSS, change it in
`fragment-widths.json` too. They are two halves of one rule.

`render.cjs` needs `playwright-core` (an `app/` devDependency, so it never
reaches the shipped asar) and its Chromium:

```bash
cd app && npm i && npx playwright install chromium
```

**Do not swap it for Electron**, tempting as it is given Electron is already
here. Its offscreen `capturePage()` returns at the host's Retina scale and
ignores `force-device-scale-factor`, so a "1024×1280" render silently comes out
2048×2560 with every fragment resampled 2× — precisely the softness
`prep-fragments.sh` exists to prevent. That was tried and reverted.

Finally, quantize the render to a 256-colour adaptive palette. On this dark UI
with one accent it is visually lossless and roughly halves the file.

**Read the 400 px preview.** It is the acceptance test: if a bullet, the version
or the zoomed control cannot be read there, it cannot be read in the channel.

## The post text

English and Russian, both. Plain and warm, no marketing throat-clearing.

Telegram allows **1024 characters on a photo caption**; aim well under. Shape:

```
🎸 Patchbay v<version> — <the tagline, lowercased into a sentence>

<what you can now do, one short paragraph — the guitarist's action, not the feature>

<the annoyance that stopped, one line>

<one smaller change worth knowing, one line>

macOS · Windows · Quad Cortex on CorOS 4.0/4.1
⬇ [Windows](<exe url>) · [Mac — Apple silicon](<arm64 dmg>) · [Mac — Intel](<x64 dmg>) · [all builds](<releases/latest>)
```

**Put the links IN the text.** A caption may carry `[label](https://…)`, and it
costs the LABEL, never the URL — Telegram's link entities sit outside the
1024-character budget, so four links are cheaper than one spelled-out URL.

Count what Telegram counts: the visible text, in UTF-16 units, with the URLs
stripped out. `make-post-kit.cjs` reports it.

Write from the player's side: "Ask Claude for a tone and it builds it on the
device" — not "the MCP server exposes preset construction". Sizes and times earn
their place ("about eight seconds", "82 MB"); adjectives do not.

## Ship it as a post kit, not as loose files

Build the download list from the release itself — asset names carry the version
and the release decides them, so a link typed from memory is a 404 posted to a
channel:

```bash
gh release view v<version> -R lexasoft123/qc-mcp --json tagName,assets > <out>/release.json
```

Turn that into `[{label, file, url, mb}]` at `<out>/dl.json`, keeping only what a
person installs: the Windows `.exe`, both `.dmg`s, and the release page as a last
row. The `.blockmap` files are updater plumbing and do not belong on a post.

```bash
node .claude/skills/release-poster/scripts/make-post-kit.cjs \
  --poster docs/release-notes/v<version>-poster.png \
  --preview <out>/v<version>-poster-phone-preview.png \
  --en <out>/caption-en.txt --ru <out>/caption-ru.txt \
  --downloads <out>/dl.json \
  --version v<version> --out <out>/post-kit.html
```

Keep it a **local** file and hand it over with `SendUserFile`. Don't commit it —
it carries a base64 copy of the poster and is regenerated in seconds.

## What goes in the repo

Only the poster, next to the notes it belongs to:

```
docs/release-notes/v<version>-poster.png
```

Posting to the channel is the user's call — say the image is send-as-photo safe
at 1280.
