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
#include <limits.h>

static FILE *g_log = NULL;
static int g_verbose = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static mach_timebase_info_data_t g_tb;
static double now_ms(void);

// Version READ is a session operation, not a harmless identity query: it rotates
// comms_version and breaks Cortex Control's encrypted cloud channel. Publish
// only sanitized metadata observed from the APP's handshake, over local IPC.
#define IDENTITY_MAX 4096
static pthread_mutex_t g_identity_mu = PTHREAD_MUTEX_INITIALIZER;
static uint8_t g_identity[IDENTITY_MAX];
static size_t g_identity_length = 0;
static unsigned long g_identity_generation = 0, g_identity_written = 0;
static IOHIDDeviceRef g_identity_device = NULL;
static const char *out_path(void);

static int identity_path(char *path, size_t capacity) {
    int n = snprintf(path, capacity, "%s.identity", out_path());
    return n > 0 && (size_t)n < capacity;
}

static void identity_clear(IOHIDDeviceRef device) {
    char path[PATH_MAX];
    pthread_mutex_lock(&g_identity_mu);
    if (!device || device == g_identity_device) {
        g_identity_length = 0;
        g_identity_device = NULL;
        g_identity_generation++;
        if (identity_path(path, sizeof(path))) unlink(path);
    }
    pthread_mutex_unlock(&g_identity_mu);
}

static int pb_varint(const uint8_t *bytes, size_t length, size_t *offset, uint64_t *value) {
    *value = 0;
    for (unsigned shift = 0; shift < 70 && *offset < length; shift += 7) {
        uint8_t byte = bytes[(*offset)++];
        if (shift == 63 && byte > 1) return 0;
        *value |= (uint64_t)(byte & 127) << shift;
        if (!(byte & 128)) return 1;
    }
    return 0;
}

static void identity_record(IOHIDDeviceRef device, const uint8_t *bytes, size_t length) {
    uint8_t metadata[IDENTITY_MAX];
    size_t offset = 0, output = 0;
    int has_firmware = 0;
    if (length > sizeof(metadata)) return;
    while (offset < length) {
        size_t start = offset;
        uint64_t tag, value;
        if (!pb_varint(bytes, length, &offset, &tag) || !(tag >> 3)) return;
        unsigned field = (unsigned)(tag >> 3), wire = tag & 7;
        if (wire == 0) {
            if (!pb_varint(bytes, length, &offset, &value)) return;
        } else if (wire == 2) {
            if (!pb_varint(bytes, length, &offset, &value) || value > length - offset) return;
            if (field == 4 && value) has_firmware = 1;  // zenos_git_hash
            offset += (size_t)value;
        } else if (wire == 1 || wire == 5) {
            size_t size = wire == 1 ? 8 : 4;
            if (size > length - offset) return;
            offset += size;
        } else return;
        if (field != 10) {  // never persist opaque comms/session material
            memcpy(metadata + output, bytes + start, offset - start);
            output += offset - start;
        }
    }
    if (!has_firmware) return;  // ignore Version ACKs, not complete identity
    pthread_mutex_lock(&g_identity_mu);
    if (g_identity_device != device) {
        pthread_mutex_unlock(&g_identity_mu);
        return;  // a late callback from a closed/unregistered device
    }
    memcpy(g_identity, metadata, output);
    g_identity_length = output;
    g_identity_device = device;
    g_identity_generation++;
    pthread_mutex_unlock(&g_identity_mu);
}

// Called by the background FIFO thread, never by a HID callback. Atomic rename
// prevents readers seeing a partial update; generation check prevents publishing
// an old device's metadata after close/unregister invalidated it.
static void identity_publish(void) {
    uint8_t metadata[IDENTITY_MAX];
    size_t length;
    unsigned long generation;
    char path[PATH_MAX], temporary[PATH_MAX];
    pthread_mutex_lock(&g_identity_mu);
    length = g_identity_length;
    generation = g_identity_generation;
    if (!length || generation == g_identity_written) {
        pthread_mutex_unlock(&g_identity_mu);
        return;
    }
    memcpy(metadata, g_identity, length);
    pthread_mutex_unlock(&g_identity_mu);
    if (!identity_path(path, sizeof(path))) return;
    int n = snprintf(temporary, sizeof(temporary), "%s.tmp.%ld", path, (long)getpid());
    if (n < 0 || (size_t)n >= sizeof(temporary)) return;
    int fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
    if (fd < 0) return;
    struct stat st;
    if (fstat(fd, &st) || st.st_uid != geteuid() || !S_ISREG(st.st_mode) || fchmod(fd, 0600)) {
        close(fd); return;
    }
    FILE *file = fdopen(fd, "wb");
    if (!file) { close(fd); unlink(temporary); return; }
    int ok = fprintf(file, "QC_IDENTITY_V1 %ld\n", (long)getpid()) > 0;
    ok = (fwrite(metadata, 1, length, file) == length) && ok;
    if (fclose(file)) ok = 0;
    pthread_mutex_lock(&g_identity_mu);
    if (ok && generation == g_identity_generation && g_identity_length && !rename(temporary, path))
        g_identity_written = generation;
    else unlink(temporary);
    pthread_mutex_unlock(&g_identity_mu);
}


// injection: the app's live device handle + a queue of frames the MCP wants sent.
// Frames are NOT sent from the reader thread (that would interleave with the app's
// multi-chunk messages and corrupt framing). They are queued and flushed from
// inside the app's own SetReport, at a message boundary (after a LAST/SINGLE
// report) — so all IOHIDDeviceSetReport calls happen on one thread, between the
// app's complete messages. Injection is published only after its own LAST arrives.
#define IQ_CAP 128
static uint8_t g_iq[IQ_CAP][129];
// head..tail contains only COMPLETE messages. A producer stages a new message
// beyond tail and publishes it atomically on LAST. Otherwise the app can drain
// FIRST, send one of its own messages, then drain the remaining MCP chunks.
static int g_iq_head = 0, g_iq_tail = 0;
static int g_iq_pending_tail = 0, g_iq_pending = 0, g_iq_dropping = 0;
static pthread_mutex_t g_iq_mu = PTHREAD_MUTEX_INITIALIZER;

static void iq_push(const uint8_t *frame) {
    pthread_mutex_lock(&g_iq_mu);
    int first = frame[2] & 0x40, last = frame[2] & 0x80;
    if (first) {
        // A new FIRST also abandons any interrupted writer's partial message.
        g_iq_pending_tail = g_iq_tail;
        g_iq_pending = 1;
        g_iq_dropping = 0;
    }
    if (!g_iq_pending) {
        pthread_mutex_unlock(&g_iq_mu);
        return;  // orphan middle/LAST: never send an incomplete message
    }
    if (!g_iq_dropping) {
        int nxt = (g_iq_pending_tail + 1) % IQ_CAP;
        if (nxt == g_iq_head) {
            // Roll back the entire pending message; committed messages survive.
            g_iq_dropping = 1;
            g_iq_pending_tail = g_iq_tail;
            if (g_log) {
                fprintf(g_log, "%.3f INJECT dropped whole message: queue full\n", now_ms());
                fflush(g_log);
            }
        } else {
            memcpy(g_iq[g_iq_pending_tail], frame, 129);
            g_iq_pending_tail = nxt;
        }
    }
    if (last) {
        if (!g_iq_dropping) g_iq_tail = g_iq_pending_tail;
        g_iq_pending = 0;
    }
    pthread_mutex_unlock(&g_iq_mu);
}

// send all queued inject frames on the app's device handle. Call ONLY from the
// app's SetReport thread, at a message boundary.
static void iq_flush(IOHIDDeviceRef device) {
    for (;;) {
        uint8_t frame[129];
        pthread_mutex_lock(&g_iq_mu);
        if (g_iq_head == g_iq_tail) { pthread_mutex_unlock(&g_iq_mu); break; }
        memcpy(frame, g_iq[g_iq_head], 129);
        g_iq_head = (g_iq_head + 1) % IQ_CAP;
        pthread_mutex_unlock(&g_iq_mu);
        IOReturn r = IOHIDDeviceSetReport(device, 1 /*Output*/, frame[0], frame, 129);
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

#ifndef QC_INTERPOSE_TEST
__attribute__((constructor))
#endif
static void init_log(void) {
    // Never let a broken FIFO write (MCP disconnected) kill the host app.
    signal(SIGPIPE, SIG_IGN);
    identity_clear(NULL);
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
        identity_publish();
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
    IOReturn r = IOHIDDeviceOpen(device, options);
    if (r == kIOReturnSuccess) {
        identity_clear(NULL);
        pthread_mutex_lock(&g_identity_mu);
        g_identity_device = device;
        pthread_mutex_unlock(&g_identity_mu);
    }
    if (g_log) {
        fprintf(g_log, "%.3f OPEN device=%p options=0x%x -> 0x%08x\n",
                now_ms(), (void *)device, options, r);
        fflush(g_log);
    }
    return r;
}

static IOReturn my_DeviceClose(IOHIDDeviceRef device, IOOptionBits options) {
    identity_clear(device);
    return IOHIDDeviceClose(device, options);
}

// ---- host -> device ----
static IOReturn my_SetReport(IOHIDDeviceRef device, IOHIDReportType type,
                             CFIndex reportID, const uint8_t *report,
                             CFIndex reportLength) {
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
        iq_flush(device);
    return r;
}

// ---- device -> host (wrap the app's callback) ----
// Each registration owns the callback AND its original context. A global callback
// pairs one device's function with another device's context after reconnects or
// when multiple HID devices register. Contexts remain alive for late callbacks;
// their small allocation is released with the host process, never during delivery.
struct input_registration {
    IOHIDReportCallback callback;
    void *context;
    IOHIDDeviceRef device;
    uint8_t message[IDENTITY_MAX + 8];
    size_t message_length;
    int message_active;
};

static void my_input_trampoline(void *context, IOReturn result, void *sender,
                                IOHIDReportType type, uint32_t reportID,
                                uint8_t *report, CFIndex reportLength) {
    struct input_registration *registration = context;
    // The app owns the supplied IOHID buffer and may mutate/reuse it in its
    // callback. Snapshot raw bytes, deliver to the app immediately, then perform
    // optional bridge work on our copy. FIFO/disk latency must never precede
    // host delivery, and app mutations must not leak into the mirrored stream.
    uint8_t snapshot[1024];
    int captured = report && reportLength > 0 && reportLength <= sizeof(snapshot);
    if (captured) memcpy(snapshot, report, (size_t)reportLength);
    registration->callback(registration->context, result, sender, type,
                           reportID, report, reportLength);
    int callback_errno = errno;
    if (captured) {
        // Observe full Version messages without querying/rekeying the device.
        if (reportLength >= 3 && snapshot[0] == 1 && snapshot[1] <= reportLength - 3) {
            size_t count = snapshot[1];
            unsigned flags = snapshot[2];
            if (flags & 0x40) { registration->message_length = 0; registration->message_active = 1; }
            if (registration->message_active) {
                if (count > sizeof(registration->message) - registration->message_length)
                    registration->message_active = 0;
                else {
                    memcpy(registration->message + registration->message_length, snapshot + 3, count);
                    registration->message_length += count;
                }
                if ((flags & 0x80) && registration->message_active) {
                    size_t size = registration->message_length;
                    if (size >= 8 && registration->message[size-8] == 10 && registration->message[size-7] == 0)
                        identity_record(registration->device, registration->message, size-8);
                    registration->message_active = 0;
                }
            }
        }
        log_report("IN ", (long)reportID, snapshot, (long)reportLength);
        forward_report(snapshot, (long)reportLength);
    }
    errno = callback_errno;

}

static void my_RegisterInputReportCallback(IOHIDDeviceRef device, uint8_t *report,
                                           CFIndex reportLength,
                                           IOHIDReportCallback callback,
                                           void *context) {
    // NULL is IOKit's unregister operation: preserve it exactly. Installing a
    // trampoline here instead leaves delivery active during the app's teardown.
    if (!callback) {
        identity_clear(device);
        IOHIDDeviceRegisterInputReportCallback(device, report, reportLength,
                                               NULL, context);
        return;
    }
    struct input_registration *registration = malloc(sizeof(*registration));
    if (!registration) {
        // Mirroring is optional; never break the app's callback on allocation failure.
        IOHIDDeviceRegisterInputReportCallback(device, report, reportLength,
                                               callback, context);
        return;
    }
    pthread_mutex_lock(&g_identity_mu);
    if (!g_identity_device) g_identity_device = device;
    pthread_mutex_unlock(&g_identity_mu);
    registration->callback = callback;
    registration->context = context;
    registration->device = device;
    registration->message_length = 0;
    registration->message_active = 0;
    IOHIDDeviceRegisterInputReportCallback(device, report, reportLength,
                                           my_input_trampoline, registration);
}

// ---- interpose table ----
#ifndef QC_INTERPOSE_TEST
__attribute__((used))
static struct { const void *replacement; const void *replacee; }
interposers[] __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)my_SetReport, (const void *)IOHIDDeviceSetReport },
    { (const void *)my_DeviceOpen, (const void *)IOHIDDeviceOpen },
    { (const void *)my_DeviceClose, (const void *)IOHIDDeviceClose },
    { (const void *)my_RegisterInputReportCallback,
      (const void *)IOHIDDeviceRegisterInputReportCallback },
};
#endif
