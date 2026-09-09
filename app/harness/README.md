# Component harness

Renders one renderer component against stubbed IPC, so UI can be looked at
without a daemon, a device, or (in a worktree) an installed Electron binary —
which is most of the time in this repo.

```bash
npx vite build -c harness/vite.config.ts
cd harness/dist && python3 -m http.server 8731
```

The pages:

- `/bench.html?s=fresh|measured|applied|error|empty|closed&h=620|788|1000` — the
  WHOLE Leveling view against a fake bench (`bench-stub.ts`, which streams a
  meter and answers every call). `measured` presses M for you, `applied` then
  presses ⌘↵, `error` fails one preset. Look at it at all three heights.
- `/transport.html?s=0..5` — the riff transport in one of its six states per
  load: empty, armed, recording, recorded, playing, a run in progress. It takes
  the sampler as a prop, so each state is a plain object.
- `/nojump.html` — walks the view through arm → record → play → measure → apply
  → drawer → focus with a ResizeObserver on every band, and logs any band whose
  size changed. Nothing but the rows may.
- `/modes.html` — every route and every sentence Home's mode block can produce,
  all on one page; `?only=1,7` narrows it to the cases you are looking at.
  `ModeChoice` is pure (it reads the snapshot it is handed and nothing else), so
  these can be stacked; `stub.ts` still has to install a bridge first, because
  importing a view pulls in the store, which subscribes at import time.
- `/keys.html` — the shortcut sheet.

Without a display, `shot.mjs` renders a page to a PNG with Electron itself and
prints the page's `#log` if it has one:

```bash
npx electron harness/shot.mjs "http://localhost:4173/bench.html?s=measured&h=788" out.png 5000 1120 860
npx electron harness/shot.mjs http://localhost:4173/nojump.html nojump.png 18000
```
