# Component harness

Renders one renderer component against stubbed IPC, so UI can be looked at
without a daemon, a device, or (in a worktree) an installed Electron binary —
which is most of the time in this repo.

```bash
npx vite build -c harness/vite.config.ts
cd harness/dist && python3 -m http.server 8731
```

Then `http://127.0.0.1:8731/?p=N` — one state per load. The components read the
IPC bridge off `window.patchbay` inside an effect, and effects run after every
render, so several panels on one page would all see whichever stub was
installed last.

Today it covers `Measured`, the measured half of the Leveling bench: no riff,
ready, mid-run, held by the true-peak guard, and audio extra absent.
