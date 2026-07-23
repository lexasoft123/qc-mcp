// DYLD interposer / bridge for Cortex Control <-> Quad Cortex USB-HID.
//
// It hooks the app's HID calls so an external MCP can share the app's (exclusive)
// device session — letting Cortex Control and the MCP run at the SAME time:
//
//   host -> QC : IOHIDDeviceSetReport               (also injectable via QC_INJECT fifo)
//   QC -> host : IOHIDDeviceRegisterInputReportCallback (mirrored to the QC_OUT fifo)
//
// FIFOs (created on load):
//   QC_INJECT (default /tmp/qc_inject) : MCP writes 129-byte HID reports -> sent to QC
//   QC_OUT    (default /tmp/qc_in)     : every device->host report, length-prefixed,
//                                        forwarded to the MCP  [uint16 LE len][report]
// The app is unaffected when no MCP is attached (the OUT fifo has no reader, so
// forwarding is skipped). Set QC_VERBOSE=1 to also log every frame to QC_LOG.
//
// Build (see build.sh) and inject via DYLD_INSERT_LIBRARIES into a re-signed copy
// of Cortex Control.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDDevice.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <mach/mach_time.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <signal.h>

static FILE *g_log = NULL;
static int g_verbose = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static mach_timebase_info_data_t g_tb;
static double now_ms(void);

// injection: the app's live device handle + a queue of frames the MCP wants sent.
// Frames are NOT sent from the reader thread (that would interleave with the app's
// multi-chunk messages and corrupt framing). They are queued and flushed from
// inside the app's own SetReport, at a message boundary (after a LAST/SINGLE
// report) — so all IOHIDDeviceSetReport calls happen on one thread, between the
// app's complete messages.
static IOHIDDeviceRef g_device = NULL;
#define IQ_CAP 128
static uint8_t g_iq[IQ_CAP][129];
static int g_iq_head = 0, g_iq_tail = 0;
static pthread_mutex_t g_iq_mu = PTHREAD_MUTEX_INITIALIZER;

static void iq_push(const uint8_t *frame) {
    pthread_mutex_lock(&g_iq_mu);
    int nxt = (g_iq_tail + 1) % IQ_CAP;
    if (nxt != g_iq_head) {          // drop if full (shouldn't happen for control)
        memcpy(g_iq[g_iq_tail], frame, 129);
        g_iq_tail = nxt;
    }
    pthread_mutex_unlock(&g_iq_mu);
}

// send all queued inject frames on the app's device handle. Call ONLY from the
// app's SetReport thread, at a message boundary.
static void iq_flush(void) {
    for (;;) {
        uint8_t frame[129];
        pthread_mutex_lock(&g_iq_mu);
        if (g_iq_head == g_iq_tail) { pthread_mutex_unlock(&g_iq_mu); break; }
        memcpy(frame, g_iq[g_iq_head], 129);
        g_iq_head = (g_iq_head + 1) % IQ_CAP;
        pthread_mutex_unlock(&g_iq_mu);
        IOReturn r = IOHIDDeviceSetReport(g_device, 1 /*Output*/, frame[0], frame, 129);
        if (g_log && g_verbose) {
            fprintf(g_log, "%.3f INJECT ret=0x%08x %02x%02x%02x\n",
                    now_ms(), r, frame[0], frame[1], frame[2]);
            fflush(g_log);
        }
    }
}

// device->host mirror to the MCP.
static int g_out_fd = -1;
static pthread_mutex_t g_out_mu = PTHREAD_MUTEX_INITIALIZER;

static void *inject_thread(void *arg);
static void *out_thread(void *arg);
static double now_ms(void);

__attribute__((constructor))
static void init_log(void) {
    // Never let a broken FIFO write (MCP disconnected) kill the host app.
    signal(SIGPIPE, SIG_IGN);
    const char *p = getenv("QC_LOG");
    if (!p) p = "/tmp/qc_hid_log.txt";
    g_log = fopen(p, "a");
    g_verbose = getenv("QC_VERBOSE") != NULL;
    mach_timebase_info(&g_tb);
    if (g_log) {
        fprintf(g_log, "\n===== interpose loaded pid=%d =====\n", getpid());
        fflush(g_log);
    }
    pthread_t t1, t2;
    pthread_create(&t1, NULL, inject_thread, NULL);
    pthread_detach(t1);
    pthread_create(&t2, NULL, out_thread, NULL);
    pthread_detach(t2);
}

static double now_ms(void) {
    uint64_t t = mach_absolute_time();
    return (double)t * g_tb.numer / g_tb.denom / 1e6;
}

static void log_report(const char *dir, long reportID, const uint8_t *buf, long len) {
    if (!g_log || !g_verbose) return;
    pthread_mutex_lock(&g_mu);
    fprintf(g_log, "%.3f %s id=%ld len=%ld ", now_ms(), dir, reportID, len);
    for (long i = 0; i < len; i++) fprintf(g_log, "%02x", buf[i]);
    fprintf(g_log, "\n");
    fflush(g_log);
    pthread_mutex_unlock(&g_mu);
}

// ---- injection reader: read 129-byte HID reports from a FIFO, QUEUE them ----
static void *inject_thread(void *arg) {
    const char *path = getenv("QC_INJECT");
    if (!path) path = "/tmp/qc_inject";
    mkfifo(path, 0666);  // ok if it already exists
    uint8_t buf[129];
    for (;;) {
        int fd = open(path, O_RDONLY);          // blocks until a writer opens
        if (fd < 0) { sleep(1); continue; }
        for (;;) {
            size_t got = 0;
            int eof = 0;
            while (got < sizeof(buf)) {
                ssize_t n = read(fd, buf + got, sizeof(buf) - got);
                if (n <= 0) { eof = 1; break; }
                got += (size_t)n;
            }
            if (got == sizeof(buf))
                iq_push(buf);       // queued; flushed at the next app message boundary
            if (eof) break;         // writer closed; reopen and wait for next
        }
        close(fd);
    }
    return NULL;
}

// ---- device->host mirror: keep the OUT fifo open for writing when the MCP is
//      listening (non-blocking, so the app is never stalled if no MCP is there) ----
static const char *out_path(void) {
    const char *p = getenv("QC_OUT");
    return p ? p : "/tmp/qc_in";
}

static void *out_thread(void *arg) {
    const char *path = out_path();
    mkfifo(path, 0666);
    for (;;) {
        if (g_out_fd < 0) {
            int fd = open(path, O_WRONLY | O_NONBLOCK);  // ENXIO until MCP reads
            if (fd >= 0) {
                pthread_mutex_lock(&g_out_mu);
                g_out_fd = fd;
                pthread_mutex_unlock(&g_out_mu);
            }
        }
        usleep(200000);
    }
    return NULL;
}

// forward one device->host report to the MCP as [uint16 LE len][report].
static void forward_report(const uint8_t *report, long len) {
    if (g_out_fd < 0 || len <= 0 || len > 1024) return;
    uint8_t frame[2 + 1024];
    frame[0] = (uint8_t)(len & 0xff);
    frame[1] = (uint8_t)((len >> 8) & 0xff);
    memcpy(frame + 2, report, (size_t)len);
    pthread_mutex_lock(&g_out_mu);
    if (g_out_fd >= 0) {
        ssize_t n = write(g_out_fd, frame, (size_t)len + 2);  // atomic (<PIPE_BUF)
        if (n < 0 && (errno == EPIPE || errno == EBADF)) {
            close(g_out_fd);
            g_out_fd = -1;   // reader went away; out_thread reopens
        }
    }
    pthread_mutex_unlock(&g_out_mu);
}

// ---- device open (log options) ----
static IOReturn my_DeviceOpen(IOHIDDeviceRef device, IOOptionBits options) {
    g_device = device;
    IOReturn r = IOHIDDeviceOpen(device, options);
    if (g_log) {
        fprintf(g_log, "%.3f OPEN device=%p options=0x%x -> 0x%08x\n",
                now_ms(), (void *)device, options, r);
        fflush(g_log);
    }
    return r;
}

// ---- host -> device ----
static IOReturn my_SetReport(IOHIDDeviceRef device, IOHIDReportType type,
                             CFIndex reportID, const uint8_t *report,
                             CFIndex reportLength) {
    g_device = device;
    IOReturn r = IOHIDDeviceSetReport(device, type, reportID, report, reportLength);
    if (g_log && g_verbose) {
        pthread_mutex_lock(&g_mu);
        fprintf(g_log, "%.3f OUT type=%d id=%ld len=%ld ret=0x%08x ",
                now_ms(), (int)type, (long)reportID, (long)reportLength, r);
        for (long i = 0; i < reportLength; i++) fprintf(g_log, "%02x", report[i]);
        fprintf(g_log, "\n");
        fflush(g_log);
        pthread_mutex_unlock(&g_mu);
    }
    // Flush queued MCP injects only at a message boundary (flags byte has the LAST
    // bit 0x80 set — LAST or SINGLE), so we never interleave the app's chunks.
    if (reportLength >= 3 && (report[2] & 0x80))
        iq_flush();
    return r;
}

// ---- device -> host (wrap the app's callback) ----
static IOHIDReportCallback g_app_cb = NULL;

static void my_input_trampoline(void *context, IOReturn result, void *sender,
                                IOHIDReportType type, uint32_t reportID,
                                uint8_t *report, CFIndex reportLength) {
    log_report("IN ", (long)reportID, report, (long)reportLength);
    forward_report(report, (long)reportLength);   // mirror to the MCP
    if (g_app_cb) g_app_cb(context, result, sender, type, reportID, report, reportLength);
}

static void my_RegisterInputReportCallback(IOHIDDeviceRef device, uint8_t *report,
                                           CFIndex reportLength,
                                           IOHIDReportCallback callback,
                                           void *context) {
    g_app_cb = callback;
    IOHIDDeviceRegisterInputReportCallback(device, report, reportLength,
                                           my_input_trampoline, context);
}

// ---- interpose table ----
__attribute__((used))
static struct { const void *replacement; const void *replacee; }
interposers[] __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)my_SetReport, (const void *)IOHIDDeviceSetReport },
    { (const void *)my_DeviceOpen, (const void *)IOHIDDeviceOpen },
    { (const void *)my_RegisterInputReportCallback,
      (const void *)IOHIDDeviceRegisterInputReportCallback },
};
