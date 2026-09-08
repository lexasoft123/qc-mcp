/* The renderer store subscribes to the IPC bridge at import time, so anything
   that pulls in a view has to find one already installed. */
;(window as unknown as { patchbay: unknown }).patchbay = {
  setMode: async (m: string) => { console.log('setMode', m); return null },
  cortexFocus: async () => null,
  snapshot: async () => null,
  onSnapshot: () => {},
  onProgress: () => {}
}
