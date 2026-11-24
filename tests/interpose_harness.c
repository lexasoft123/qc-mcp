// Offline harness: use real IOKit types but replace all device I/O with recorders.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDDevice.h>
#include <assert.h>

static IOReturn record_set_report(IOHIDDeviceRef, IOHIDReportType, CFIndex,
                                  const uint8_t *, CFIndex);
static void record_registration(IOHIDDeviceRef, uint8_t *, CFIndex,
                                 IOHIDReportCallback, void *);
#define IOHIDDeviceSetReport record_set_report
#define IOHIDDeviceRegisterInputReportCallback record_registration
#define QC_INTERPOSE_TEST
#include "../interceptor/interpose.c"

struct registration_record {
    IOHIDDeviceRef device;
    uint8_t *buffer;
    CFIndex length;
    IOHIDReportCallback callback;
    void *context;
};
static struct registration_record registrations[4];
static int registration_count, writes, callbacks_a, callbacks_b;
static IOHIDDeviceRef written_devices[1024];
static uint8_t written_values[1024];
static void *expected_a = (void *)101, *expected_b = (void *)202;

static IOReturn record_set_report(IOHIDDeviceRef device, IOHIDReportType type,
                                  CFIndex id, const uint8_t *report, CFIndex length) {
    assert(type == kIOHIDReportTypeOutput && id == 2 && length == 129);
    assert(report[0] == 2);
    assert(writes < 1024);
    written_values[writes] = report[3];
    written_devices[writes++] = device;
    return kIOReturnSuccess;
}
static void record_registration(IOHIDDeviceRef device, uint8_t *buffer, CFIndex length,
                                 IOHIDReportCallback callback, void *context) {
    registrations[registration_count++] = (struct registration_record){
        device, buffer, length, callback, context};
}
static void callback_a(void *context, IOReturn result, void *sender,
                        IOHIDReportType type, uint32_t id, uint8_t *report, CFIndex length) {
    assert(context == expected_a && result == kIOReturnSuccess);
    assert(sender == (void *)303 && type == kIOHIDReportTypeInput);
    assert(id == 1 && length == 3 && report[1] == 0);
    callbacks_a++;
}
static void callback_b(void *context, IOReturn result, void *sender,
                        IOHIDReportType type, uint32_t id, uint8_t *report, CFIndex length) {
    assert(context == expected_b);
    callbacks_b++;
}
static int mirror_read_fd;
static void callback_before_diagnostics(void *context, IOReturn result, void *sender,
        IOHIDReportType type, uint32_t id, uint8_t *report, CFIndex length) {
    assert(context == expected_a && length == 129);
    assert(ftell(g_log) == 0);  // app must see input before capture touches disk
    uint8_t byte;
    assert(read(mirror_read_fd, &byte, 1) == -1 && errno == EAGAIN);
    report[3] = 99;  // the app may reuse/modify its supplied input buffer
    errno = EDOM;
}

int main(void) {
    uint8_t a[129] = {1}, b[129] = {1};
    IOHIDDeviceRef device_a = (IOHIDDeviceRef)11, device_b = (IOHIDDeviceRef)22;
    my_RegisterInputReportCallback(device_a, a, sizeof(a), callback_a, expected_a);
    my_RegisterInputReportCallback(device_b, b, sizeof(b), callback_b, expected_b);
    assert(registrations[0].device == device_a && registrations[0].buffer == a);
    assert(registrations[0].length == 129 && registrations[1].device == device_b);
    // The first device must retain its own function/context after the second registers.
    for (int i = 0; i < 2; i++)
        registrations[i].callback(registrations[i].context, kIOReturnSuccess,
            (void *)303, kIOHIDReportTypeInput, 1, a, 3);
    assert(callbacks_a == 1 && callbacks_b == 1);
    my_RegisterInputReportCallback(device_b, b, sizeof(b), NULL, expected_b);
    assert(registrations[2].callback == NULL && registrations[2].context == expected_b);
    // Unregistering B must not disable or change A.
    registrations[0].callback(registrations[0].context, kIOReturnSuccess,
        (void *)303, kIOHIDReportTypeInput, 1, a, 3);
    assert(callbacks_a == 2 && callbacks_b == 1);
    uint8_t injected[129] = {2, 1, 0xc0, 42};
    uint8_t app[129] = {2, 1, 0x40, 24};
    iq_push(injected);
    my_SetReport(device_a, kIOHIDReportTypeOutput, 2, app, sizeof(app));
    assert(writes == 1);  // no injection inside the app's multi-report message
    app[2] = 0x80;
    my_SetReport(device_a, kIOHIDReportTypeOutput, 2, app, sizeof(app));
    assert(writes == 3 && g_iq_head == g_iq_tail);
    for (int i = 0; i < writes; i++) assert(written_devices[i] == device_a);
    // A producer may pause between chunks while the app finishes another message.
    // Nothing from the partial injected message may reach the device yet.
    writes = 0;
    app[2] = 0xc0;
    injected[2] = 0x40; injected[3] = 10;
    iq_push(injected);
    my_SetReport(device_a, kIOHIDReportTypeOutput, 2, app, sizeof(app));
    assert(writes == 1 && written_values[0] == 24);
    injected[2] = 0; injected[3] = 11;
    iq_push(injected);
    my_SetReport(device_a, kIOHIDReportTypeOutput, 2, app, sizeof(app));
    assert(writes == 2 && written_values[1] == 24);
    injected[2] = 0x80; injected[3] = 12;
    iq_push(injected);
    my_SetReport(device_a, kIOHIDReportTypeOutput, 2, app, sizeof(app));
    assert(writes == 6);
    assert(written_values[2] == 24 && written_values[3] == 10);
    assert(written_values[4] == 11 && written_values[5] == 12);

    // Overflow must discard a whole pending message, never publish a prefix/tail.
    writes = 0;
    injected[2] = 0xc0; injected[3] = 30; iq_push(injected);
    injected[2] = 0x40; injected[3] = 40; iq_push(injected);
    injected[2] = 0;
    for (int i = 0; i < IQ_CAP; i++) iq_push(injected);
    injected[2] = 0x80; iq_push(injected);
    iq_flush(device_a);
    assert(writes == 1 && written_values[0] == 30);
    // Orphan continuation/tail after a failed sender cannot become a message.
    injected[2] = 0; iq_push(injected);
    injected[2] = 0x80; iq_push(injected);
    iq_flush(device_a);
    assert(writes == 1);
    injected[2] = 0xc0; injected[3] = 50; iq_push(injected);
    iq_flush(device_a);
    assert(writes == 2 && written_values[1] == 50);

    // Repeated complete messages exercise ring wrap and keep their order.
    writes = 0;
    for (int i = 0; i < IQ_CAP * 2; i++) {
        injected[2] = 0xc0; injected[3] = (uint8_t)i; iq_push(injected);
        iq_flush(device_a);
        assert(written_values[i] == (uint8_t)i);
    }
    assert(writes == IQ_CAP * 2);

    // Optional capture/mirroring must run AFTER the host callback, using a copy
    // made before it can mutate its IOHID buffer. Preserve the callback's errno.
    int mirror[2]; assert(pipe(mirror) == 0);
    mirror_read_fd = mirror[0]; g_out_fd = mirror[1];
    fcntl(mirror[0], F_SETFL, O_NONBLOCK);
    g_log = tmpfile(); assert(g_log != NULL); g_verbose = 1;
    mach_timebase_info(&g_tb);
    uint8_t received[129] = {1, 1, 0xc0, 77};
    my_RegisterInputReportCallback(device_a, received, sizeof(received),
                                   callback_before_diagnostics, expected_a);
    registrations[3].callback(registrations[3].context, kIOReturnSuccess,
        (void *)303, kIOHIDReportTypeInput, 1, received, sizeof(received));
    assert(errno == EDOM);
    uint8_t mirrored[131]; assert(read(mirror[0], mirrored, sizeof(mirrored)) == 131);
    assert(mirrored[5] == 77 && received[3] == 99);
    rewind(g_log); char line[1024]; assert(fgets(line, sizeof(line), g_log));
    assert(strstr(line, "0101c04d") != NULL);  // captured pre-callback bytes
    fclose(g_log); g_log = NULL; g_verbose = 0;
    close(mirror[0]); close(mirror[1]); g_out_fd = -1;
    // Version metadata cache never persists comms_version session bytes.
    char directory[] = "/tmp/qc-identity-test-XXXXXX";
    assert(mkdtemp(directory));
    char out[PATH_MAX], identity[PATH_MAX];
    snprintf(out, sizeof(out), "%s/out", directory);
    setenv("QC_OUT", out, 1);
    assert(identity_path(identity, sizeof(identity)));
    uint8_t version[] = {8,1,34,5,'4','.','1','.','1',82,4,0x11,0x22,0x33,0x44};
    uint8_t metadata[] = {8,1,34,5,'4','.','1','.','1'};
    g_identity_device = device_a;  // simulate a successful device open
    identity_record(device_a, version, sizeof(version));
    assert(g_identity_length == sizeof(metadata));
    assert(!memcmp(g_identity, metadata, sizeof(metadata)));
    identity_publish();
    struct stat identity_stat; assert(stat(identity, &identity_stat) == 0);
    assert((identity_stat.st_mode & 0777) == 0600);
    FILE *cache = fopen(identity, "rb"); assert(cache);
    char header[100]; assert(fgets(header, sizeof(header), cache));
    assert(strstr(header, "QC_IDENTITY_V1 ") == header);
    uint8_t cached[100]; size_t size = fread(cached, 1, sizeof(cached), cache);
    assert(size == sizeof(metadata) && !memcmp(cached, metadata, size));
    fclose(cache);
    uint8_t ack[] = {8,3};
    identity_record(device_a, ack, sizeof(ack));
    assert(g_identity_length == sizeof(metadata)); // ACK must not erase full identity
    identity_clear(device_b); assert(access(identity, F_OK) == 0);
    identity_clear(device_a); assert(access(identity, F_OK) != 0);
    identity_record(device_a, version, sizeof(version)); // late callback after close
    assert(g_identity_length == 0);
    identity_publish(); assert(access(identity, F_OK) != 0); // no stale republish
    rmdir(directory); unsetenv("QC_OUT");
    free(registrations[3].context);
    free(registrations[0].context);
    free(registrations[1].context);
    return 0;
}
