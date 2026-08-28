// Launch Cortex Control with qcinject.dll loaded before it runs.
//
// The macOS side just sets DYLD_INSERT_LIBRARIES; Windows has no equivalent that
// works on a signed app, so: start the process SUSPENDED, LoadLibrary the DLL in
// it via a remote thread, then resume. The DLL patches the import table before
// the app's first instruction, so it never misses the device being opened.
//
//   qclaunch.exe [path\to\Cortex Control.exe] [path\to\qcinject.dll]
//
// Both default to the usual install path / the DLL next to this exe.
#include <windows.h>
#include <stdio.h>

static int fail(const char *what) {
    fprintf(stderr, "qclaunch: %s failed (%lu)\n", what, GetLastError());
    return 1;
}

int wmain(int argc, wchar_t **argv) {
    wchar_t app[MAX_PATH], dll[MAX_PATH];
    if (argc > 1) wcscpy_s(app, MAX_PATH, argv[1]);
    else wcscpy_s(app, MAX_PATH,
                  L"C:\\Program Files\\Neural DSP\\Cortex Control\\Cortex Control.exe");
    if (argc > 2) {
        wcscpy_s(dll, MAX_PATH, argv[2]);
    } else {                                  // default: next to this executable
        GetModuleFileNameW(NULL, dll, MAX_PATH);
        wchar_t *slash = wcsrchr(dll, L'\\');
        if (slash) slash[1] = 0;
        wcscat_s(dll, MAX_PATH, L"qcinject.dll");
    }
    if (GetFileAttributesW(dll) == INVALID_FILE_ATTRIBUTES) {
        fwprintf(stderr, L"qclaunch: no such DLL: %s\n", dll);
        return 1;
    }
    wprintf(L"app: %s\ndll: %s\n", app, dll);

    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(app, NULL, NULL, NULL, FALSE, CREATE_SUSPENDED,
                        NULL, NULL, &si, &pi))
        return fail("CreateProcess");

    SIZE_T bytes = (wcslen(dll) + 1) * sizeof(wchar_t);
    void *remote = VirtualAllocEx(pi.hProcess, NULL, bytes, MEM_COMMIT, PAGE_READWRITE);
    if (!remote) return fail("VirtualAllocEx");
    if (!WriteProcessMemory(pi.hProcess, remote, dll, bytes, NULL))
        return fail("WriteProcessMemory");

    // kernel32 is at the same base in every process of the same session, so the
    // local address of LoadLibraryW is valid in the child too.
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    LPTHREAD_START_ROUTINE loadlib =
        (LPTHREAD_START_ROUTINE)GetProcAddress(k32, "LoadLibraryW");
    HANDLE th = CreateRemoteThread(pi.hProcess, NULL, 0, loadlib, remote, 0, NULL);
    if (!th) return fail("CreateRemoteThread");
    WaitForSingleObject(th, 10000);
    DWORD loaded = 0;
    GetExitCodeThread(th, &loaded);           // nonzero = the DLL's HMODULE
    CloseHandle(th);
    VirtualFreeEx(pi.hProcess, remote, 0, MEM_RELEASE);
    if (!loaded) {
        fprintf(stderr, "qclaunch: the DLL did not load; killing the app\n");
        TerminateProcess(pi.hProcess, 1);
        return 1;
    }
    printf("injected (module 0x%lx), resuming\n", loaded);
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
