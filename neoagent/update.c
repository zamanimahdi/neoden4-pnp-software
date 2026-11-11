/* neoagent: signed update packages with backup and rollback.
 *
 * Package (built by ../tools/mkpkg.py):
 *   NEOPKG1\n
 *   name <text>\n
 *   file <size> <sha256 hex> <absolute destination path, or @agent\relative>\n   (one per file)
 *   stop <program name>\n                                                    (optional, extra)
 *   end\n
 *   <file contents, in the same order>
 *   <32 bytes HMAC-SHA256(update_key, everything before these 32 bytes)>
 *
 * Apply: verify HMAC + every SHA-256 -> backup set "update <name>" -> stop the programs that
 * live in a destination folder (and the "stop" ones) -> write each file (temp + replace) ->
 * restart them. Any failure restores the backup and deletes files the package created.
 * A new neoagent.exe is swapped in after the reply: the new agent is started and must answer
 * /api/ping within 15 s, otherwise the old one is put back and restarted. */
#include "neoagent.h"
#include "sha256.h"

#define MAX_FILES 256

typedef struct { char dest[PATHW]; long size; char sha[65]; const char *data; int existed; } pfile_t;

static volatile LONG self_pending;
static char self_new[MAX_PATH];

int self_update_pending(void) { return self_pending != 0; }

static int write_file_raw(const char *path, const char *data, long len)
{
    wchar_t wp[PATHW], wt[PATHW];
    char tmp[PATHW], d[PATHW];
    HANDLE h;
    DWORD put;
    utf8_to_w(path, wp, PATHW);
    strcpy(d, path);
    if (strrchr(d, '\\')) { *strrchr(d, '\\') = 0; fs_mkdir(d); }
    _snprintf(tmp, sizeof tmp, "%s.neoagent-tmp", path);
    utf8_to_w(tmp, wt, PATHW);
    h = CreateFileW(wt, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!WriteFile(h, data, (DWORD)len, &put, NULL) || put != (DWORD)len) { CloseHandle(h); DeleteFileW(wt); return 0; }
    FlushFileBuffers(h);
    CloseHandle(h);
    if (!MoveFileExW(wt, wp, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) { DeleteFileW(wt); return 0; }
    return 1;
}

static int same_folder_prefix(const char *file, const char *dir)
{
    size_t n = strlen(dir);
    return n && !_strnicmp(file, dir, n) && (file[n] == '\\' || dir[n - 1] == '\\');
}

static DWORD WINAPI self_swap(LPVOID arg)
{
    char exe[MAX_PATH], old[MAX_PATH], cmd[MAX_PATH + 40];
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    int i, ok = 0;
    (void)arg;
    Sleep(1500);                                   /* let the HTTP reply go out */
    GetModuleFileNameA(NULL, exe, sizeof exe);
    _snprintf(old, sizeof old, "%s.old", exe);
    log_msg("self update: stopping and handing over to the new neoagent.exe");
    A.quit = 1;
    Sleep(1500);                                   /* HTTP loop closes its listening socket */
    EnterCriticalSection(&A.lock);
    for (i = 0; i < A.nprocs; i++) { A.procs[i].wanted = 0; proc_stop(i); }
    LeaveCriticalSection(&A.lock);
    DeleteFileA(old);
    if (!MoveFileA(exe, old) || !MoveFileA(self_new, exe)) {
        log_msg("self update: cannot swap files (error %lu); keeping the old agent", GetLastError());
        MoveFileA(old, exe);
        goto restart_old;
    }
    memset(&si, 0, sizeof si); si.cb = sizeof si;
    _snprintf(cmd, sizeof cmd, "\"%s\" --after-update", exe);
    if (CreateProcessA(NULL, cmd, NULL, NULL, FALSE, 0, NULL, A.home, &si, &pi)) {
        for (i = 0; i < 15 && !ok; i++) {          /* new agent must answer /api/ping */
            SOCKET s = socket(AF_INET, SOCK_STREAM, 0);
            struct sockaddr_in a;
            Sleep(1000);
            memset(&a, 0, sizeof a);
            a.sin_family = AF_INET; a.sin_port = htons((unsigned short)A.http_port); a.sin_addr.s_addr = inet_addr("127.0.0.1");
            if (!connect(s, (struct sockaddr *)&a, sizeof a)) {
                char req[] = "GET /api/ping HTTP/1.0\r\n\r\n", buf[64] = "";
                send(s, req, (int)strlen(req), 0);
                recv(s, buf, sizeof buf - 1, 0);
                ok = !strncmp(buf, "HTTP/1.1 ", 9);   /* 200 or 401: it is serving */
            }
            closesocket(s);
        }
        if (ok) { log_msg("self update: new agent is up, old one exits"); CloseHandle(pi.hThread); CloseHandle(pi.hProcess); ExitProcess(0); }
        log_msg("self update: new agent did not answer; rolling back");
        TerminateProcess(pi.hProcess, 1);
        WaitForSingleObject(pi.hProcess, 5000);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    }
    DeleteFileA(exe);
    MoveFileA(old, exe);
restart_old:
    memset(&si, 0, sizeof si); si.cb = sizeof si;
    _snprintf(cmd, sizeof cmd, "\"%s\"", exe);
    if (CreateProcessA(NULL, cmd, NULL, NULL, FALSE, 0, NULL, A.home, &si, &pi)) { CloseHandle(pi.hThread); CloseHandle(pi.hProcess); }
    ExitProcess(0);
    return 0;
}

int update_apply(const char *pkg, long len, char *report, int max)
{
    pfile_t *f = (pfile_t *)calloc(MAX_FILES, sizeof *f);
    char stop_names[MAX_PROCS][32] = { { 0 } }, set[MAX_PATH], name[200] = "", exe[MAX_PATH], line[PATHW + 120];
    int nf = 0, ns = 0, i, j, n = 0, ok = 0, stopped[MAX_PROCS] = { 0 }, self = -1;
    const char *p = pkg, *hend, *data;
    unsigned char mac[32];
    hmac_t h;

    report[0] = 0;
    if (!f) return 0;
#define FAIL(...) do { n += _snprintf(report + n, max - n, __VA_ARGS__); goto out; } while (0)
    if (len < 40 || strncmp(pkg, "NEOPKG1\n", 8)) FAIL("not a neoagent package\n");
    hmac_sha256_init(&h, A.update_key, strlen(A.update_key));
    hmac_sha256_update(&h, pkg, len - 32);
    hmac_sha256_final(&h, mac);
    {   unsigned char d = 0; for (i = 0; i < 32; i++) d |= mac[i] ^ (unsigned char)pkg[len - 32 + i];
        if (d) FAIL("signature does not match (wrong update_key, or the package was changed)\n"); }

    /* header */
    hend = strstr(pkg, "\nend\n");
    if (!hend || hend - pkg > len - 32) FAIL("bad header\n");
    data = hend + 5;
    p = pkg + 8;
    GetModuleFileNameA(NULL, exe, sizeof exe);
    while (p < hend + 1) {
        const char *e = strchr(p, '\n');
        int ll = (int)(e - p);
        if (ll >= (int)sizeof line) FAIL("header line too long\n");
        memcpy(line, p, ll); line[ll] = 0;
        p = e + 1;
        if (!strncmp(line, "name ", 5)) strncpy(name, line + 5, sizeof name - 1);
        else if (!strncmp(line, "stop ", 5) && ns < MAX_PROCS) strncpy(stop_names[ns++], line + 5, 31);
        else if (!strncmp(line, "file ", 5)) {
            char dest[PATHW];
            if (nf >= MAX_FILES) FAIL("too many files\n");
            if (sscanf(line + 5, "%ld %64s", &f[nf].size, f[nf].sha) != 2) FAIL("bad file line: %s\n", line);
            {   const char *d = strchr(line + 5, ' '); d = d ? strchr(d + 1, ' ') : NULL;
                if (!d) FAIL("bad file line: %s\n", line);
                if (!strncmp(d + 1, "@agent\\", 7)) _snprintf(dest, sizeof dest, "%s\\%s", A.home, d + 8);
                else strncpy(dest, d + 1, sizeof dest - 1);
                dest[sizeof dest - 1] = 0; }
            if (!path_ok(dest)) FAIL("destination must be absolute: %s\n", dest);
            strcpy(f[nf].dest, dest);
            if (!_stricmp(dest, exe)) self = nf;
            nf++;
        }
    }
    /* contents + hashes */
    for (i = 0; i < nf; i++) {
        unsigned char d[32];
        char hx[65];
        if (f[i].size < 0 || data + f[i].size > pkg + len - 32) FAIL("package is truncated\n");
        f[i].data = data;
        sha256(data, f[i].size, d); hex32(d, hx);
        if (_stricmp(hx, f[i].sha)) FAIL("SHA-256 mismatch for %s\n", f[i].dest);
        data += f[i].size;
    }
    if (data != pkg + len - 32) FAIL("package has extra bytes\n");
    n += _snprintf(report + n, max - n, "package \"%s\": %d file(s), signature and hashes ok\n", name, nf);

    /* backup */
    {   char why[260]; _snprintf(why, sizeof why, "update %s", name); backup_set_new(set, sizeof set, why); }
    for (i = 0; i < nf; i++) {
        wchar_t w[PATHW];
        utf8_to_w(f[i].dest, w, PATHW);
        f[i].existed = GetFileAttributesW(w) != INVALID_FILE_ATTRIBUTES;
        if (f[i].existed && !backup_file(f[i].dest, set)) FAIL("cannot back up %s\n", f[i].dest);
    }
    n += _snprintf(report + n, max - n, "backup %s\n", strrchr(set, '\\') + 1);

    /* stop programs whose folder gets a file, plus the named ones */
    EnterCriticalSection(&A.lock);
    for (j = 0; j < A.nprocs; j++) {
        int hit = 0;
        for (i = 0; i < nf && !hit; i++) if (i != self && same_folder_prefix(f[i].dest, A.procs[j].dir)) hit = 1;
        for (i = 0; i < ns && !hit; i++) if (!strcmp(stop_names[i], A.procs[j].name)) hit = 1;
        if (hit && proc_running(j)) { A.procs[j].wanted = 0; proc_stop(j); stopped[j] = 1;
            n += _snprintf(report + n, max - n, "stopped %s\n", A.procs[j].name); }
    }
    LeaveCriticalSection(&A.lock);

    /* write */
    for (i = 0; i < nf; i++) {
        if (i == self) {
            _snprintf(self_new, sizeof self_new, "%s.new", exe);
            if (!write_file_raw(self_new, f[i].data, f[i].size)) break;
            n += _snprintf(report + n, max - n, "staged new neoagent.exe (swapped after this reply)\n");
            continue;
        }
        if (!write_file_raw(f[i].dest, f[i].data, f[i].size)) break;
        n += _snprintf(report + n, max - n, "wrote %s (%ld bytes)\n", f[i].dest, f[i].size);
    }
    if (i < nf) {                                  /* rollback */
        n += _snprintf(report + n, max - n, "FAILED writing %s (error %lu); rolling back\n", f[i].dest, GetLastError());
        for (j = 0; j < nf; j++) {
            wchar_t w[PATHW];
            if (j == self) continue;
            if (!f[j].existed) { utf8_to_w(f[j].dest, w, PATHW); DeleteFileW(w); }
        }
        {   char rep2[4096]; backup_restore(strrchr(set, '\\') + 1, rep2, sizeof rep2);
            n += _snprintf(report + n, max - n, "%s", rep2); }
        self = -1;
    } else ok = 1;

    /* restart what we stopped */
    EnterCriticalSection(&A.lock);
    for (j = 0; j < A.nprocs; j++)
        if (stopped[j]) { A.procs[j].wanted = 1; if (proc_start(j)) n += _snprintf(report + n, max - n, "started %s\n", A.procs[j].name); }
    LeaveCriticalSection(&A.lock);

    if (ok && self >= 0 && InterlockedExchange(&self_pending, 1) == 0)
        CloseHandle(CreateThread(NULL, 0, self_swap, NULL, 0, NULL));
out:
#undef FAIL
    free(f);
    return ok;
}
