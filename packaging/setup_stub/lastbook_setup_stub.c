// Lastbook-<version>-Setup.exe: a small front for the real installer (lastbook_setup.py, a PyInstaller one-file program).
//
// Why: a one-file PyInstaller program loads VCRUNTIME140.dll, VCRUNTIME140_1.dll and mscoree.dll by bare name, and Windows looks
// in the program's own folder first. For a downloaded installer that folder is Downloads, so a DLL planted there would run
// (security audit N-DLL). This front imports only kernel32 and user32 (known DLLs, never searched for), allows DLLs from System32 only,
// writes the real installer into a new private folder under this person's temporary folder, runs it from there with the same
// command line, waits, removes it, and exits with its exit code. The real installer's own folder is then that private folder.
//
// The real installer is appended to this file: [stub][installer bytes][8-byte size, little-endian]["LBSETUP1"].
// Authenticode may append alignment bytes and a certificate table after this footer.
// No C runtime: built with -nostdlib, so nothing else is loaded before the first line runs.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#ifndef LOAD_LIBRARY_SEARCH_SYSTEM32
#define LOAD_LIBRARY_SEARCH_SYSTEM32 0x00000800
#endif

// Lastbook ES: build_setup_windows.sh passes its own names (from lastbook_setup.py) in a header, with -include. These are upstream's.
#ifndef SETUP_TITLE
#define SETUP_TITLE L"Lastbook Setup"       // the title of this front's message boxes
#endif
#ifndef SETUP_FOLDER
#define SETUP_FOLDER L"LastbookSetup-"      // the private folder's name, before its random part
#endif
#ifndef SETUP_INNER
#define SETUP_INNER L"Lastbook Setup.exe"   // the real installer's name in that folder
#endif

// With no C runtime, the compiler may still turn a zeroing loop into a call to memset: this is it.
void *memset(void *d, int c, unsigned long long n) {
    volatile unsigned char *p = (volatile unsigned char *)d;
    while (n--) *p++ = (unsigned char)c;
    return d;
}

static const char MAGIC[8] = {'L', 'B', 'S', 'E', 'T', 'U', 'P', '1'};

static void fail(const wchar_t *what) {
    MessageBoxW(NULL, what, SETUP_TITLE, MB_OK | MB_ICONERROR);
    ExitProcess(1);
}

static int same8(const char *a, const char *b) {
    for (int i = 0; i < 8; i++) if (a[i] != b[i]) return 0;
    return 1;
}

static void wcat(wchar_t *dst, int cap, const wchar_t *src) {
    int n = 0;
    while (n < cap - 1 && dst[n]) n++;
    while (n < cap - 1 && *src) dst[n++] = *src++;
    dst[n] = 0;
}

static void whex(wchar_t *dst, int cap, unsigned long v) {
    wchar_t b[9];
    for (int i = 7; i >= 0; i--) { b[i] = L"0123456789abcdef"[v & 15]; v >>= 4; }
    b[8] = 0;
    wcat(dst, cap, b);
}

// The command line after this program's own name, as Windows gave it (quoted name or not).
static const wchar_t *args_after_name(const wchar_t *cl) {
    if (*cl == L'"') { cl++; while (*cl && *cl != L'"') cl++; if (*cl) cl++; }
    else { while (*cl && *cl != L' ' && *cl != L'\t') cl++; }
    while (*cl == L' ' || *cl == L'\t') cl++;
    return cl;
}

void WinMainCRTStartup(void) {
    // Before anything else: DLLs for this process come from System32 only, never from the folder this file is in.
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32);
    SetDllDirectoryW(L"");     // and take the current folder out of the old search order too

    static wchar_t self[32768];
    if (!GetModuleFileNameW(NULL, self, 32768)) fail(L"Setup could not find itself.");
    HANDLE f = CreateFileW(self, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) fail(L"Setup could not read itself.");
    LARGE_INTEGER total;
    if (!GetFileSizeEx(f, &total) || total.QuadPart < 16) fail(L"This copy of Setup is damaged. Download it again.");
    BYTE *image = (BYTE *)GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)image;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(image + dos->e_lfanew);
    IMAGE_DATA_DIRECTORY certificate = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_SECURITY];
    LONGLONG payload_end = total.QuadPart;
    if (certificate.Size) {
        if (certificate.VirtualAddress < 16 ||
            (ULONGLONG)certificate.VirtualAddress + certificate.Size > (ULONGLONG)total.QuadPart)
            fail(L"This copy of Setup is damaged. Download it again.");
        // Unlike other PE directories, the certificate table uses a file offset.
        payload_end = certificate.VirtualAddress;
    }
    char tail[16];
    DWORD got = 0;
    LARGE_INTEGER pos;
    int found = 0;
    // SignTool aligns the certificate table to eight bytes. The footer can precede up to seven padding bytes.
    for (int padding = 0; padding < 8 && payload_end >= 16 + padding; padding++) {
        pos.QuadPart = payload_end - 16 - padding;
        if (!SetFilePointerEx(f, pos, NULL, FILE_BEGIN) || !ReadFile(f, tail, 16, &got, NULL) || got != 16)
            fail(L"This copy of Setup is damaged. Download it again.");
        if (same8(tail + 8, MAGIC)) { payload_end -= padding; found = 1; break; }
    }
    if (!found) fail(L"This copy of Setup is damaged. Download it again.");
    unsigned long long size = 0;
    for (int i = 7; i >= 0; i--) size = (size << 8) | (unsigned char)tail[i];
    if (size == 0 || size > (unsigned long long)payload_end - 16) fail(L"This copy of Setup is damaged. Download it again.");
    pos.QuadPart = payload_end - 16 - (LONGLONG)size;

    // A new folder of this person's own, under their temporary folder (only they and the system can write there).
    static wchar_t dir[MAX_PATH + 64], exe[MAX_PATH + 96];
    DWORD n = GetTempPathW(MAX_PATH, dir);
    if (!n || n > MAX_PATH) fail(L"Setup could not find the temporary folder.");
    int made = 0;
    for (int tries = 0; tries < 20 && !made; tries++) {
        dir[n] = 0;
        wcat(dir, MAX_PATH + 64, SETUP_FOLDER);
        whex(dir, MAX_PATH + 64, GetCurrentProcessId());
        whex(dir, MAX_PATH + 64, GetTickCount() + (DWORD)tries * 7919u);
        made = CreateDirectoryW(dir, NULL);     // fails if it exists: never reuse a folder someone else made
    }
    if (!made) fail(L"Setup could not make a temporary folder.");
    exe[0] = 0;
    wcat(exe, MAX_PATH + 96, dir);
    wcat(exe, MAX_PATH + 96, L"\\");
    wcat(exe, MAX_PATH + 96, SETUP_INNER);
    HANDLE o = CreateFileW(exe, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (o == INVALID_HANDLE_VALUE) { RemoveDirectoryW(dir); fail(L"Setup could not write to the temporary folder."); }
    if (!SetFilePointerEx(f, pos, NULL, FILE_BEGIN)) fail(L"Setup could not read itself.");
    static char buf[1 << 20];
    unsigned long long left = size;
    while (left) {
        DWORD want = left > sizeof(buf) ? (DWORD)sizeof(buf) : (DWORD)left, r = 0, w = 0;
        if (!ReadFile(f, buf, want, &r, NULL) || r != want || !WriteFile(o, buf, r, &w, NULL) || w != r) {
            CloseHandle(o); DeleteFileW(exe); RemoveDirectoryW(dir); fail(L"Setup could not unpack itself. Check there is room on the disk.");
        }
        left -= r;
    }
    CloseHandle(o);
    CloseHandle(f);

    // Run it with the same arguments (/S, /uninstall), from its own folder, and wait.
    static wchar_t cmd[32768];
    cmd[0] = 0;
    wcat(cmd, 32768, L"\"");
    wcat(cmd, 32768, exe);
    wcat(cmd, 32768, L"\" ");
    wcat(cmd, 32768, args_after_name(GetCommandLineW()));
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    for (unsigned i = 0; i < sizeof(si); i++) ((char *)&si)[i] = 0;
    si.cb = sizeof(si);
    DWORD code = 1;
    if (CreateProcessW(exe, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    } else {
        DeleteFileW(exe); RemoveDirectoryW(dir);
        fail(L"Setup could not start. Windows may have blocked it.");
    }
    for (int i = 0; i < 50 && !DeleteFileW(exe); i++) Sleep(100);     // the installer's own clean-up can hold it a moment
    RemoveDirectoryW(dir);
    ExitProcess(code);
}
