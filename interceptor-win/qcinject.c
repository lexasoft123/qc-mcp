// IAT interposer / bridge for Cortex Control <-> Quad Cortex USB-HID on Windows.
//
// The Win32 sibling of ../interceptor/interpose.c. Same job: hook the app's HID
// calls so an external MCP can share the app's exclusive device session, letting
// Cortex Control and the MCP run at the SAME time.
//
// Why IAT hooking and not Detours/MinHook: Cortex Control (x64, one static exe)
// imports NO hid.dll and has no delay-load table. It reaches the device purely
// through KERNEL32 -- CreateFileW, ReadFile, WriteFile, GetOverlappedResult --
// all in the plain import table, so patching those five slots is enough and
// needs no inline-hook engine.
//
//   host -> QC : WriteFile on the device handle   (mirrored + logged)
//   QC -> host : ReadFile + GetOverlappedResult   (mirrored to the out pipe)
//
// Sending is the delicate part. Two rules, both learned the hard way:
//
//  1. ONE MESSAGE AT A TIME. A message is 1..n 129-byte reports and the device
//     reassembles them in arrival order, so a frame slipped between the app's
//     FIRST and LAST chunks corrupts both messages. g_tx is held for the app's
//     whole message (taken on a chunk without FLAG_LAST, released on the one
//     with it) and for the whole injected message, so the two can never braid.
//
//  2. THE DEVICE TAKES ONE OUTPUT REPORT AT A TIME. The app's writes are
//     overlapped and still in flight when WriteFile returns, and a second
//     concurrent output report is refused with ERROR_GEN_FAILURE(31). We cannot
//     see when the app's write completes -- it does not reap them through
//     GetOverlappedResult -- so the injector simply retries on 31 until the
//     device accepts it.
//
// Hooks must also leave GetLastError() exactly as the real call left it: the app
// reads ERROR_IO_PENDING after its own WriteFile, and logging in between would
// clobber it.
//
// Pipes (created on load; the DLL is the SERVER end, like the FIFOs on macOS):
//   \\.\pipe\qc_inject : MCP writes 129-byte HID reports -> sent to the QC
//   \\.\pipe\qc_out    : every device->host report, [uint16 LE len][report]
//
// The app is unaffected when no MCP is attached (nothing connected = mirroring
// is skipped). Set QC_VERBOSE=1 to log every frame to QC_LOG.
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>

#define FRAME_LEN   129
#define IQ_CAP      128
#define PEND_CAP    64
#define FLAG_LAST   0x80        // set on the final chunk of a message
#define FLAG_SINGLE 0xC0

static const wchar_t *QC_MATCH = L"vid_152a&pid_880a";

static FILE *g_log;
static int   g_verbose;
static CRITICAL_SECTION g_mu;      // guards g_dev + the pending-read table
static CRITICAL_SECTION g_iq_mu;   // guards the inject queue

static HANDLE g_dev = INVALID_HANDLE_VALUE;   // the app's live QC handle
static CRITICAL_SECTION g_tx;      // one whole message on the wire at a time
static LONG g_tx_depth;            // app-side nesting; only its own thread touches it
static wchar_t g_dev_path[512];    // the interface path the app opened
static HANDLE g_tx_dev = INVALID_HANDLE_VALUE;   // OUR handle, for injecting
static HANDLE g_out_pipe = INVALID_HANDLE_VALUE;
static volatile LONG g_out_connected;

// real function pointers (filled when we patch the IAT)
static HANDLE (WINAPI *real_CreateFileW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES,
                                         DWORD, DWORD, HANDLE);
static BOOL (WINAPI *real_ReadFile)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
static BOOL (WINAPI *real_WriteFile)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
static BOOL (WINAPI *real_GetOverlappedResult)(HANDLE, LPOVERLAPPED, LPDWORD, BOOL);
static BOOL (WINAPI *real_CloseHandle)(HANDLE);

static void qc_log(const char *fmt, ...) {
    if (!g_log) return;
    va_list ap; va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

// ---------------------------------------------------------------- inject queue
// Frames the MCP wants sent. NOT written from the pipe thread: that would
// interleave with the app's own multi-chunk messages and corrupt framing. They
// are queued and flushed from inside the app's own WriteFile, at a message
// boundary (right after a LAST/SINGLE chunk), so every write to the device
// happens on one thread between complete messages -- same rule as macOS.
static unsigned char g_iq[IQ_CAP][FRAME_LEN];
static int g_iq_head, g_iq_tail;

static void iq_push(const unsigned char *frame) {
    EnterCriticalSection(&g_iq_mu);
    int nxt = (g_iq_tail + 1) % IQ_CAP;
    if (nxt != g_iq_head) {                 // drop if full (never for control traffic)
        memcpy(g_iq[g_iq_tail], frame, FRAME_LEN);
        g_iq_tail = nxt;
    }
    LeaveCriticalSection(&g_iq_mu);
}

// Our OWN handle to the device, for injecting.
//
// Writing on the APP's handle is refused outright with ERROR_GEN_FAILURE(31) --
// not transiently, so it is not "device busy": 400 retries over 400ms all failed,
// while the app's own writes on that same handle succeed. Whatever the HID stack
// objects to, it is specific to using someone else's handle. The app opens the
// device FILE_SHARE_READ|WRITE, so we can just open our own, which is exactly
// what the MCP does in shared mode and it writes fine. g_tx still serialises us
// against the app's messages, so this keeps the ordering guarantee that is the
// whole reason for the interposer.
static HANDLE tx_handle(void) {
    if (g_tx_dev != INVALID_HANDLE_VALUE) return g_tx_dev;
    if (!g_dev_path[0]) return INVALID_HANDLE_VALUE;
    // Deliberately NOT overlapped: the injector is its own thread and can block,
    // and a synchronous handle keeps our writes out of the app's completion paths
    // entirely.
    HANDLE h = real_CreateFileW(g_dev_path, GENERIC_READ | GENERIC_WRITE,
                                FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                                OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        qc_log("inject: could not open our own device handle (%lu)", GetLastError());
        return INVALID_HANDLE_VALUE;
    }
    g_tx_dev = h;
    // ERROR_GEN_FAILURE on a HID write is classically "buffer != the collection's
    // OutputReportByteLength", so log what the driver thinks that is for OUR handle.
    HMODULE hid = LoadLibraryW(L"hid.dll");
    if (hid) {
        BOOL (WINAPI *getpp)(HANDLE, PVOID *) =
            (BOOL (WINAPI *)(HANDLE, PVOID *))GetProcAddress(hid, "HidD_GetPreparsedData");
        LONG (WINAPI *getcaps)(PVOID, PVOID) =
            (LONG (WINAPI *)(PVOID, PVOID))GetProcAddress(hid, "HidP_GetCaps");
        BOOL (WINAPI *freepp)(PVOID) =
            (BOOL (WINAPI *)(PVOID))GetProcAddress(hid, "HidD_FreePreparsedData");
        PVOID pp = NULL;
        USHORT caps[32];
        if (getpp && getcaps && getpp(h, &pp)) {
            ZeroMemory(caps, sizeof caps);
            LONG st = getcaps(pp, caps);
            qc_log("inject: HidP_GetCaps st=0x%08lx usage=%u/%u in=%u out=%u feat=%u",
                   st, caps[0], caps[1], caps[2], caps[3], caps[4]);
            if (freepp) freepp(pp);
        } else {
            qc_log("inject: HidD_GetPreparsedData failed (%lu)", GetLastError());
        }
    }
    qc_log("inject: opened our own device handle %p", h);
    return h;
}

// Write one frame, retrying while the device says "busy". Returns TRUE if the
// device took it.
static BOOL inject_one(HANDLE dev, const unsigned char *frame) {
    for (int attempt = 0; attempt < 100; attempt++) {
        DWORD wrote = 0;
        BOOL ok = real_WriteFile(dev, frame, FRAME_LEN, &wrote, NULL);  // synchronous
        DWORD err = ok ? 0 : GetLastError();
        if (ok && wrote == FRAME_LEN) {
            if (g_verbose)
                qc_log("INJECT ok%s %02x%02x%02x",
                       attempt ? " (after retries)" : "", frame[0], frame[1], frame[2]);
            return TRUE;
        }
        if (!ok && err == ERROR_GEN_FAILURE) {   // device busy: back off and retry
            Sleep(2);
            continue;
        }
        qc_log("INJECT failed ok=%d err=%lu wrote=%lu %02x%02x%02x",
               ok, err, wrote, frame[0], frame[1], frame[2]);
        return FALSE;
    }
    qc_log("INJECT gave up after 100 tries %02x%02x%02x",
           frame[0], frame[1], frame[2]);
    return FALSE;
}

// Drain one COMPLETE injected message (up to and including a FLAG_LAST frame)
// while holding g_tx, so it can never interleave with the app's own message.
static DWORD WINAPI inject_thread(LPVOID unused) {
    (void)unused;
    for (;;) {
        EnterCriticalSection(&g_iq_mu);
        int empty = (g_iq_head == g_iq_tail);
        LeaveCriticalSection(&g_iq_mu);
        if (empty || g_dev == INVALID_HANDLE_VALUE) { Sleep(2); continue; }
        HANDLE dev = tx_handle();
        if (dev == INVALID_HANDLE_VALUE) { Sleep(50); continue; }

        EnterCriticalSection(&g_tx);         // blocks until the app's message ends
        for (;;) {
            unsigned char frame[FRAME_LEN];
            EnterCriticalSection(&g_iq_mu);
            int have = (g_iq_head != g_iq_tail);
            if (have) {
                memcpy(frame, g_iq[g_iq_head], FRAME_LEN);
                g_iq_head = (g_iq_head + 1) % IQ_CAP;
            }
            LeaveCriticalSection(&g_iq_mu);
            if (!have) break;                // queue drained mid-message: stop here
            inject_one(dev, frame);
            if (frame[2] & FLAG_LAST) break; // our message is complete
        }
        LeaveCriticalSection(&g_tx);
    }
}

// ------------------------------------------------------------- device -> host
static void mirror(const unsigned char *report, DWORD len) {
    if (!InterlockedCompareExchange(&g_out_connected, 0, 0)) return;
    unsigned char hdr[2];
    hdr[0] = (unsigned char)(len & 0xff);
    hdr[1] = (unsigned char)((len >> 8) & 0xff);
    DWORD wrote = 0;
    if (!real_WriteFile(g_out_pipe, hdr, 2, &wrote, NULL) ||
        !real_WriteFile(g_out_pipe, report, len, &wrote, NULL)) {
        // consumer vanished -- drop back to "nobody attached" and wait again
        InterlockedExchange(&g_out_connected, 0);
        DisconnectNamedPipe(g_out_pipe);
        qc_log("out pipe consumer went away");
    }
}

// pending overlapped reads on the device, so GetOverlappedResult can mirror them
typedef struct { LPOVERLAPPED ov; void *buf; } pend_t;
static pend_t g_pend[PEND_CAP];

static void pend_add(LPOVERLAPPED ov, void *buf) {
    EnterCriticalSection(&g_mu);
    for (int i = 0; i < PEND_CAP; i++) {
        if (g_pend[i].ov == NULL || g_pend[i].ov == ov) {
            g_pend[i].ov = ov; g_pend[i].buf = buf; break;
        }
    }
    LeaveCriticalSection(&g_mu);
}

static void *pend_take(LPOVERLAPPED ov) {
    void *buf = NULL;
    EnterCriticalSection(&g_mu);
    for (int i = 0; i < PEND_CAP; i++) {
        if (g_pend[i].ov == ov) { buf = g_pend[i].buf; g_pend[i].ov = NULL; break; }
    }
    LeaveCriticalSection(&g_mu);
    return buf;
}

// ------------------------------------------------------------------- the hooks
static HANDLE WINAPI my_CreateFileW(LPCWSTR name, DWORD access, DWORD share,
                                    LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                    DWORD flags, HANDLE tmpl) {
    HANDLE h = real_CreateFileW(name, access, share, sa, disp, flags, tmpl);
    DWORD cerr = (h == INVALID_HANDLE_VALUE) ? GetLastError() : 0;
    if (h != INVALID_HANDLE_VALUE && name) {
        // Windows spells the ids into the interface path; lowercase to match.
        wchar_t low[512]; size_t i = 0;
        for (; name[i] && i < 511; i++) low[i] = (wchar_t)towlower(name[i]);
        low[i] = 0;
        // Enumeration opens the same path with access 0 just to read the ids
        // (our own winhid._probe does it too), then closes it. Those are NOT the
        // session: latching onto one and clearing g_dev when it closed made us
        // lose the real handle after two frames.
        if (wcsstr(low, QC_MATCH) && (access & (GENERIC_READ | GENERIC_WRITE))) {
            EnterCriticalSection(&g_mu);
            g_dev = h;
            wcsncpy(g_dev_path, name, 511);
            g_dev_path[511] = 0;
            LeaveCriticalSection(&g_mu);
            qc_log("device handle %p opened for I/O (access=0x%08lx share=0x%08lx flags=0x%08lx)",
                   h, access, share, flags);
        } else if (wcsstr(low, QC_MATCH) && g_verbose) {
            qc_log("  (ignoring probe open %p, access=0x%08lx)", h, access);
        }
    }
    if (h == INVALID_HANDLE_VALUE) SetLastError(cerr);
    return h;
}

static BOOL WINAPI my_ReadFile(HANDLE h, LPVOID buf, DWORD len, LPDWORD got,
                               LPOVERLAPPED ov) {
    BOOL ok = real_ReadFile(h, buf, len, got, ov);
    DWORD rerr = ok ? 0 : GetLastError();
    if (h == g_dev) {
        if (ok && got && *got) {
            const unsigned char *f = (const unsigned char *)buf;
            if (g_verbose) qc_log("QC->APP(sync) %02x%02x%02x len=%lu",
                                  f[0], f[1], f[2], *got);
            mirror(f, *got);                            // completed synchronously
        } else if (!ok && rerr == ERROR_IO_PENDING && ov) {
            pend_add(ov, buf);                          // mirror on completion
        }
    }
    SetLastError(rerr);
    return ok;
}

static BOOL WINAPI my_GetOverlappedResult(HANDLE h, LPOVERLAPPED ov, LPDWORD got,
                                          BOOL wait) {
    BOOL ok = real_GetOverlappedResult(h, ov, got, wait);
    DWORD gerr = ok ? 0 : GetLastError();
    if (ok && h == g_dev && got && *got) {
        void *buf = pend_take(ov);
        if (buf) {
            const unsigned char *f = (const unsigned char *)buf;
            if (g_verbose) qc_log("QC->APP %02x%02x%02x len=%lu",
                                  f[0], f[1], f[2], *got);
            mirror(f, *got);
        }
    }
    SetLastError(gerr);
    return ok;
}

static BOOL WINAPI my_WriteFile(HANDLE h, LPCVOID buf, DWORD len, LPDWORD wrote,
                                LPOVERLAPPED ov) {
    const unsigned char *f = (const unsigned char *)buf;
    BOOL is_dev = (h == g_dev && len >= 3);
    // Take the wire for this whole message before the first chunk goes out, so
    // the injector cannot land a frame in the middle of it.
    if (is_dev) {
        if (g_tx_depth == 0) EnterCriticalSection(&g_tx);
        g_tx_depth++;
    }
    BOOL ok = real_WriteFile(h, buf, len, wrote, ov);
    DWORD werr = ok ? 0 : GetLastError();
    if (is_dev) {
        if (g_verbose) qc_log("APP->QC %02x%02x%02x len=%lu ok=%d err=%lu",
                              f[0], f[1], f[2], len, ok, werr);
        if (f[2] & FLAG_LAST) {              // message complete: release the wire
            while (g_tx_depth > 0) { g_tx_depth--; LeaveCriticalSection(&g_tx); }
        }
    }
    SetLastError(werr);   // the app reads ERROR_IO_PENDING; don't clobber it
    return ok;
}

static BOOL WINAPI my_CloseHandle(HANDLE h) {
    if (h == g_dev) {
        EnterCriticalSection(&g_mu);
        g_dev = INVALID_HANDLE_VALUE;
        if (g_tx_dev != INVALID_HANDLE_VALUE) {
            real_CloseHandle(g_tx_dev);       // ours followed the app's session
            g_tx_dev = INVALID_HANDLE_VALUE;
        }
        LeaveCriticalSection(&g_mu);
        qc_log("device handle %p closed", h);
    }
    return real_CloseHandle(h);
}

// --------------------------------------------------------------- IAT patching
static int patch_iat(HMODULE mod, const char *want_fn, void *repl, void **out_real) {
    unsigned char *base = (unsigned char *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    IMAGE_DATA_DIRECTORY *dir =
        &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir->VirtualAddress) return 0;
    IMAGE_IMPORT_DESCRIPTOR *imp =
        (IMAGE_IMPORT_DESCRIPTOR *)(base + dir->VirtualAddress);
    int patched = 0;
    for (; imp->Name; imp++) {
        IMAGE_THUNK_DATA *orig = (IMAGE_THUNK_DATA *)(base + imp->OriginalFirstThunk);
        IMAGE_THUNK_DATA *addr = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        if (!imp->OriginalFirstThunk) orig = addr;
        for (; orig->u1.AddressOfData; orig++, addr++) {
            if (orig->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;   // imported by ordinal
            IMAGE_IMPORT_BY_NAME *nm =
                (IMAGE_IMPORT_BY_NAME *)(base + orig->u1.AddressOfData);
            if (strcmp((const char *)nm->Name, want_fn)) continue;
            DWORD old;
            if (!VirtualProtect(&addr->u1.Function, sizeof(void *),
                                PAGE_READWRITE, &old)) continue;
            if (out_real && !*out_real) *out_real = (void *)addr->u1.Function;
            addr->u1.Function = (ULONGLONG)(uintptr_t)repl;
            VirtualProtect(&addr->u1.Function, sizeof(void *), old, &old);
            patched++;
        }
    }
    return patched;
}

// ------------------------------------------------------------------ pipe server
static DWORD WINAPI out_pipe_thread(LPVOID unused) {
    (void)unused;
    for (;;) {
        g_out_pipe = CreateNamedPipeW(L"\\\\.\\pipe\\qc_out", PIPE_ACCESS_OUTBOUND,
                                      PIPE_TYPE_BYTE | PIPE_WAIT, 255,
                                      1 << 20, 1 << 20, 0, NULL);
        if (g_out_pipe == INVALID_HANDLE_VALUE) { Sleep(1000); continue; }
        if (ConnectNamedPipe(g_out_pipe, NULL) ||
            GetLastError() == ERROR_PIPE_CONNECTED) {
            InterlockedExchange(&g_out_connected, 1);
            qc_log("MCP attached to the out pipe");
            while (InterlockedCompareExchange(&g_out_connected, 0, 0)) Sleep(200);
            qc_log("MCP detached");
        }
        DisconnectNamedPipe(g_out_pipe);
        CloseHandle(g_out_pipe);
        g_out_pipe = INVALID_HANDLE_VALUE;
    }
}

static DWORD WINAPI inject_pipe_thread(LPVOID unused) {
    (void)unused;
    for (;;) {
        HANDLE p = CreateNamedPipeW(L"\\\\.\\pipe\\qc_inject", PIPE_ACCESS_INBOUND,
                                    PIPE_TYPE_BYTE | PIPE_WAIT, 255,
                                    1 << 16, 1 << 16, 0, NULL);
        if (p == INVALID_HANDLE_VALUE) { Sleep(1000); continue; }
        if (ConnectNamedPipe(p, NULL) || GetLastError() == ERROR_PIPE_CONNECTED) {
            unsigned char frame[FRAME_LEN];
            DWORD have = 0;
            for (;;) {
                DWORD n = 0;
                if (!ReadFile(p, frame + have, FRAME_LEN - have, &n, NULL) || !n) break;
                have += n;
                if (have == FRAME_LEN) { iq_push(frame); have = 0; }
            }
        }
        DisconnectNamedPipe(p);
        CloseHandle(p);
    }
}

static DWORD WINAPI init_thread(LPVOID unused) {
    (void)unused;
    CreateThread(NULL, 0, out_pipe_thread, NULL, 0, NULL);
    CreateThread(NULL, 0, inject_pipe_thread, NULL, 0, NULL);
    CreateThread(NULL, 0, inject_thread, NULL, 0, NULL);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE self, DWORD reason, LPVOID reserved) {
    (void)self; (void)reserved;
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(self);
    InitializeCriticalSection(&g_mu);
    InitializeCriticalSection(&g_iq_mu);
    InitializeCriticalSection(&g_tx);

    char path[MAX_PATH];
    DWORD n = GetEnvironmentVariableA("QC_LOG", path, sizeof path);
    if (n && n < sizeof path) g_log = fopen(path, "a");
    char v[8];
    g_verbose = GetEnvironmentVariableA("QC_VERBOSE", v, sizeof v) && v[0] == '1';

    HMODULE exe = GetModuleHandleW(NULL);
    int a = patch_iat(exe, "CreateFileW", my_CreateFileW, (void **)&real_CreateFileW);
    int b = patch_iat(exe, "ReadFile", my_ReadFile, (void **)&real_ReadFile);
    int c = patch_iat(exe, "WriteFile", my_WriteFile, (void **)&real_WriteFile);
    int d = patch_iat(exe, "GetOverlappedResult", my_GetOverlappedResult,
                      (void **)&real_GetOverlappedResult);
    int e = patch_iat(exe, "CloseHandle", my_CloseHandle, (void **)&real_CloseHandle);
    // Anything we failed to find, fall back to the real export so we never call NULL.
    if (!real_CreateFileW) real_CreateFileW = CreateFileW;
    if (!real_ReadFile) real_ReadFile = ReadFile;
    if (!real_WriteFile) real_WriteFile = WriteFile;
    if (!real_GetOverlappedResult) real_GetOverlappedResult = GetOverlappedResult;
    if (!real_CloseHandle) real_CloseHandle = CloseHandle;
    qc_log("qcinject loaded: patched CreateFileW=%d ReadFile=%d WriteFile=%d "
         "GetOverlappedResult=%d CloseHandle=%d", a, b, c, d, e);

    CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
    return TRUE;
}
