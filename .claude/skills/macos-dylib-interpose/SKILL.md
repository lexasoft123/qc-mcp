---
name: macos-dylib-interpose
description: Inject a DYLD interposer dylib into a hardened-runtime macOS app to hook its functions — capture the traffic it exchanges with a device/service, and optionally bridge/inject your own frames so your tool shares the app's session. Use when you need to observe or ride a signed macOS app's IOKit/network/library calls (e.g. capture a device protocol, or run alongside an app that holds a device exclusively).
---

# Inject a DYLD interposer into a macOS app (capture + bridge)

Use this to (a) log the exact calls/data a signed app exchanges with hardware, and
(b) share the app's session so your external tool can read/write through it while the
app runs. Everything stays local — a re-signed *copy* of the app on your own machine.

> Ethics/legal: only on software you're licensed to run, for interop/debugging on
> your own device. Don't distribute the re-signed app copy.

## 1. Interposer dylib (C)
Use the `__DATA,__interpose` section to replace functions app-wide. Calls from your
own dylib to the original name still reach the original (dyld doesn't interpose the
interposing image), so wrappers can call through.
```c
static IOReturn my_SetReport(IOHIDDeviceRef d, IOHIDReportType t, CFIndex id,
                             const uint8_t *r, CFIndex n){
    IOReturn rv = IOHIDDeviceSetReport(d,t,id,r,n);   // real call
    log_bytes("OUT", r, n);                            // observe
    return rv;
}
__attribute__((used)) static struct { const void*replacement,*replacee; }
interposers[] __attribute__((section("__DATA,__interpose"))) = {
    {(void*)my_SetReport,(void*)IOHIDDeviceSetReport},
};
```
To capture device→host, interpose the *callback registration* and substitute a
trampoline that logs then calls the app's callback (save it in a global).

## 2. Make injection possible (hardened runtime blocks DYLD_INSERT_LIBRARIES)
Re-sign a **copy** of the app ad-hoc **without** the runtime flag, adding entitlements
so it loads your unsigned dylib and honors env vars:
```bash
cp -R "/Applications/App.app" ./App-instrumented.app
# entitlements: keep originals + add:
#   com.apple.security.cs.disable-library-validation  = true
#   com.apple.security.cs.allow-dyld-environment-variables = true
codesign -f -s - --entitlements ent.plist "App-instrumented.app/Contents/MacOS/App"
codesign -f -s - --entitlements ent.plist "App-instrumented.app"   # NO --options runtime
codesign -dvvv App-instrumented.app | grep flags   # must NOT say 'runtime'
```
Compile the dylib and run: `DYLD_INSERT_LIBRARIES=./interpose.dylib "App-instrumented.app/Contents/MacOS/App"`.
(Recompiling just the dylib later needs no app re-sign — see `interceptor/build.sh --dylib-only`.)

## 3. Bridge mode (run your tool alongside the app)
Add two FIFOs in the dylib: one the tool writes frames to (you send them on the app's
device handle), one that mirrors every device→host frame to the tool. **Two critical
gotchas learned the hard way:**
- **`signal(SIGPIPE, SIG_IGN)`** in the dylib — else a broken mirror-FIFO write when
  your tool disconnects silently *kills the host app* (default SIGPIPE = terminate,
  no crash log).
- **Never send injected frames from a background thread** — they interleave the app's
  own multi-chunk messages and corrupt framing. Instead *queue* them and flush from
  inside the app's own send function, only at a message boundary (after the LAST/
  SINGLE chunk). All device I/O then happens on one thread, between complete messages.

## Reference
Full working implementation: `interceptor/interpose.c`, `build.sh`, `run-bridge.sh`,
and the tool-side FIFO client `src/qc_mcp/bridge.py`. See PROTOCOL.md §11.
