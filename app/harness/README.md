# Component harness

Renders one renderer component against stubbed IPC, so UI can be looked at
without a daemon, a device, or (in a worktree) an installed Electron binary —
which is most of the time in this repo.

```bash
npx vite build -c harness/vite.config.ts
cd harness/dist && python3 -m http.server 8731
```

Two pages:

- `/?p=N` — `Measured`, the measured half of the Leveling bench: no riff, ready,
  mid-run, held by the true-peak guard, and audio extra absent. **One state per
  load.** The component reads the IPC bridge off `window.patchbay` inside an
  effect, and effects run after every render, so several panels on one page
  would all see whichever stub was installed last.
- `/modes.html` — every route and every sentence Home's mode block can produce,
  all on one page; `?only=1,7` narrows it to the cases you are looking at.
  `ModeChoice` is pure (it reads the snapshot it is handed and nothing else), so
  these can be stacked; `stub.ts` still has to install a bridge first, because
  importing a view pulls in the store, which subscribes at import time.
