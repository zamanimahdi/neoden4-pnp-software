/* neoagent: FTP server (RFC 959 + SIZE/MDTM/REST/EPSV/UTF8), one thread per client.
 * Virtual root "/" = the drives ("/D/NeoDen4/Config.ini" = D:\NeoDen4\Config.ini),
 * or ftp_root when set. Same user/password as HTTP. Overwrites and deletes are backed up
 * like the HTTP API does. Passive and active (PORT) data connections. */
#include <ctype.h>
#include "neoagent.h"

typedef struct {
    SOCKET c, pasv, data;
    char cwd[PATHW];            /* virtual, "/" based, '/' separators */
    char user[64];
    int authed, type_a;
    long long rest;
    struct sockaddr_in port_addr;
    int have_port;
    char rnfr[PATHW];
} ftp_t;

static void out(ftp_t *f, const char *fmt, ...)
{
    char b[1200];
    va_list ap;
    int n;
    va_start(ap, fmt); n = _vsnprintf(b, sizeof b - 3, fmt, ap); va_end(ap);
    if (n < 0) n = (int)sizeof b - 3;
    b[n++] = '\r'; b[n++] = '\n';
    send(f->c, b, n, 0);
}

/* virtual path (absolute or relative to cwd) -> normalised virtual + real Windows path */
static int resolve(ftp_t *f, const char *arg, char *virt, char *real)
{
    char t[PATHW * 2], *parts[128], *tok, *q;
    int np = 0, i;
    if (arg && arg[0] == '/') strncpy(t, arg, sizeof t - 1);
    else _snprintf(t, sizeof t, "%s/%s", f->cwd, arg ? arg : "");
    t[sizeof t - 1] = 0;
    for (q = t; *q; ) {                                      /* split on / and \ (thread-safe) */
        while (*q == '/' || *q == '\\') *q++ = 0;
        if (!*q) break;
        tok = q;
        while (*q && *q != '/' && *q != '\\') q++;
        if (*q) *q++ = 0;
        if (!strcmp(tok, ".")) continue;
        if (!strcmp(tok, "..")) { if (np) np--; continue; }
        if (np < 128) parts[np++] = tok;
    }
    strcpy(virt, "/");
    for (i = 0; i < np; i++) { if (i) strcat(virt, "/"); strncat(virt, parts[i], PATHW - strlen(virt) - 2); }
    if (A.ftp_root[0]) {
        strcpy(real, A.ftp_root);
        for (i = 0; i < np; i++) { strcat(real, "\\"); strcat(real, parts[i]); }
        return 1;
    }
    if (!np) { real[0] = 0; return 1; }                     /* drive list */
    if (strlen(parts[0]) != 1 || !isalpha((unsigned char)parts[0][0])) return 0;
    _snprintf(real, PATHW, "%c:\\", toupper((unsigned char)parts[0][0]));
    for (i = 1; i < np; i++) { if (i > 1) strcat(real, "\\"); strncat(real, parts[i], PATHW - strlen(real) - 2); }
    return 1;
}

static SOCKET open_data(ftp_t *f)
{
    SOCKET s = INVALID_SOCKET;
    if (f->pasv != INVALID_SOCKET) {
        fd_set fs; struct timeval tv = { 20, 0 };
        FD_ZERO(&fs); FD_SET(f->pasv, &fs);
        if (select(0, &fs, NULL, NULL, &tv) > 0) s = accept(f->pasv, NULL, NULL);
        closesocket(f->pasv); f->pasv = INVALID_SOCKET;
    } else if (f->have_port) {
        s = socket(AF_INET, SOCK_STREAM, 0);
        if (connect(s, (struct sockaddr *)&f->port_addr, sizeof f->port_addr)) { closesocket(s); s = INVALID_SOCKET; }
        f->have_port = 0;
    }
    return s;
}

static void list_dir(ftp_t *f, const char *real, int names_only)
{
    SOCKET d;
    char line[PATHW + 200], name[PATHW];
    out(f, "150 Opening data connection");
    if ((d = open_data(f)) == INVALID_SOCKET) { out(f, "425 Cannot open data connection"); return; }
    if (!real[0]) {                                         /* drives */
        DWORD m = GetLogicalDrives(); int i;
        for (i = 0; i < 26; i++) if (m & (1u << i)) {
            char root[4] = { (char)('A' + i), ':', '\\', 0 };
            if (GetDriveTypeA(root) == DRIVE_NO_ROOT_DIR) continue;
            if (names_only) _snprintf(line, sizeof line, "%c\r\n", 'A' + i);
            else _snprintf(line, sizeof line, "drwxr-xr-x 1 owner group 0 Jan 01 00:00 %c\r\n", 'A' + i);
            send(d, line, (int)strlen(line), 0);
        }
    } else {
        WIN32_FIND_DATAW fd;
        wchar_t wp[PATHW];
        HANDLE h;
        char pat[PATHW];
        static const char *mon[] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
        _snprintf(pat, sizeof pat, "%s%s*", real, real[strlen(real) - 1] == '\\' ? "" : "\\");
        utf8_to_w(pat, wp, PATHW);
        h = FindFirstFileW(wp, &fd);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                FILETIME lt; SYSTEMTIME st;
                if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
                w_to_utf8(fd.cFileName, name, sizeof name);
                FileTimeToLocalFileTime(&fd.ftLastWriteTime, &lt); FileTimeToSystemTime(&lt, &st);
                if (names_only) _snprintf(line, sizeof line, "%s\r\n", name);
                else _snprintf(line, sizeof line, "%s 1 owner group %.0f %s %02d %04d %s\r\n",
                               (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? "drwxr-xr-x" : "-rw-r--r--",
                               (double)fd.nFileSizeHigh * 4294967296.0 + fd.nFileSizeLow,
                               mon[(st.wMonth + 11) % 12], st.wDay, st.wYear, name);
                send(d, line, (int)strlen(line), 0);
            } while (FindNextFileW(h, &fd));
            FindClose(h);
        }
    }
    closesocket(d);
    out(f, "226 Transfer complete");
}

static void retr(ftp_t *f, const char *real)
{
    wchar_t wp[PATHW];
    HANDLE h;
    SOCKET d;
    char buf[65536];
    DWORD n;
    LARGE_INTEGER off;
    utf8_to_w(real, wp, PATHW);
    h = CreateFileW(wp, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) { out(f, "550 Cannot open file"); return; }
    off.QuadPart = f->rest; f->rest = 0;
    SetFilePointer(h, off.LowPart, &off.HighPart, FILE_BEGIN);
    out(f, "150 Opening data connection");
    if ((d = open_data(f)) == INVALID_SOCKET) { CloseHandle(h); out(f, "425 Cannot open data connection"); return; }
    while (ReadFile(h, buf, sizeof buf, &n, NULL) && n) {
        char *p = buf; int left = (int)n;
        while (left > 0) { int k = send(d, p, left, 0); if (k <= 0) goto broken; p += k; left -= k; }
    }
    closesocket(d); CloseHandle(h);
    out(f, "226 Transfer complete");
    return;
broken:
    closesocket(d); CloseHandle(h);
    out(f, "426 Connection closed; transfer aborted");
}

static void stor(ftp_t *f, const char *real, int append)
{
    wchar_t wp[PATHW], wt[PATHW];
    char tmp[PATHW], set[MAX_PATH], buf[65536];
    HANDLE h;
    SOCKET d;
    int k, ok = 1;
    DWORD put;
    if (!path_ok(real)) { out(f, "553 Not allowed here"); return; }
    utf8_to_w(real, wp, PATHW);
    if (GetFileAttributesW(wp) != INVALID_FILE_ATTRIBUTES) {
        backup_set_new(set, sizeof set, "ftp upload");
        backup_file(real, set);
    }
    _snprintf(tmp, sizeof tmp, "%s.neoagent-tmp", real);
    utf8_to_w(tmp, wt, PATHW);
    if (append || f->rest) CopyFileW(wp, wt, FALSE);
    h = CreateFileW(wt, GENERIC_WRITE, 0, NULL, (append || f->rest) ? OPEN_ALWAYS : CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) { out(f, "550 Cannot create file"); return; }
    if (append) SetFilePointer(h, 0, NULL, FILE_END);
    else if (f->rest) { LARGE_INTEGER o; o.QuadPart = f->rest; SetFilePointer(h, o.LowPart, &o.HighPart, FILE_BEGIN); SetEndOfFile(h); }
    f->rest = 0;
    out(f, "150 Ready to receive");
    if ((d = open_data(f)) == INVALID_SOCKET) { CloseHandle(h); DeleteFileW(wt); out(f, "425 Cannot open data connection"); return; }
    while ((k = recv(d, buf, sizeof buf, 0)) > 0)
        if (!WriteFile(h, buf, (DWORD)k, &put, NULL) || put != (DWORD)k) { ok = 0; break; }
    closesocket(d);
    FlushFileBuffers(h);
    CloseHandle(h);
    if (ok && k == 0 && MoveFileExW(wt, wp, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        log_msg("ftp %s: stored %s", f->user, real);
        out(f, "226 Transfer complete");
    } else { DeleteFileW(wt); out(f, "451 Transfer failed; file unchanged"); }
}

static DWORD WINAPI ftp_client(LPVOID arg)
{
    ftp_t F, *f = &F;
    char line[PATHW + 16], cmd[16], *argp, virt[PATHW], real[PATHW];
    int ln = 0;
    memset(f, 0, sizeof F);
    f->c = (SOCKET)arg; f->pasv = INVALID_SOCKET;
    strcpy(f->cwd, "/");
    out(f, "220 neoagent FTP ready");
    for (;;) {
        char ch;
        int k = recv(f->c, &ch, 1, 0);
        if (k <= 0) break;
        if (ch == '\r') continue;
        if (ch != '\n') { if (ln < (int)sizeof line - 1) line[ln++] = ch; continue; }
        line[ln] = 0; ln = 0;
        argp = strchr(line, ' ');
        if (argp) *argp++ = 0;
        _snprintf(cmd, sizeof cmd, "%s", line);
        _strupr(cmd);

        if (!strcmp(cmd, "USER")) { strncpy(f->user, argp ? argp : "", sizeof f->user - 1); out(f, "331 Password required"); continue; }
        if (!strcmp(cmd, "PASS")) {
            f->authed = !strcmp(f->user, A.user) && argp && !strcmp(argp, A.password);
            if (f->authed) { out(f, "230 Logged in"); log_msg("ftp: %s logged in", f->user); }
            else { out(f, "530 Login incorrect"); Sleep(1000); }
            continue;
        }
        if (!strcmp(cmd, "QUIT")) { out(f, "221 Bye"); break; }
        if (!strcmp(cmd, "FEAT")) { out(f, "211-Features:\r\n UTF8\r\n SIZE\r\n MDTM\r\n REST STREAM\r\n PASV\r\n EPSV\r\n211 End"); continue; }
        if (!strcmp(cmd, "OPTS")) { out(f, "200 OK"); continue; }
        if (!strcmp(cmd, "SYST")) { out(f, "215 UNIX Type: L8"); continue; }
        if (!strcmp(cmd, "NOOP")) { out(f, "200 OK"); continue; }
        if (!f->authed) { out(f, "530 Please log in"); continue; }

        if (!strcmp(cmd, "PWD") || !strcmp(cmd, "XPWD")) { out(f, "257 \"%s\"", f->cwd); continue; }
        if (!strcmp(cmd, "CWD") || !strcmp(cmd, "XCWD") || !strcmp(cmd, "CDUP")) {
            DWORD at;
            wchar_t w[PATHW];
            if (!resolve(f, !strcmp(cmd, "CDUP") ? ".." : argp, virt, real)) { out(f, "550 No such folder"); continue; }
            if (real[0]) { utf8_to_w(real, w, PATHW); at = GetFileAttributesW(w);
                if (at == INVALID_FILE_ATTRIBUTES || !(at & FILE_ATTRIBUTE_DIRECTORY)) { out(f, "550 No such folder"); continue; } }
            strcpy(f->cwd, virt);
            out(f, "250 OK");
            continue;
        }
        if (!strcmp(cmd, "TYPE")) { f->type_a = argp && toupper((unsigned char)argp[0]) == 'A'; out(f, "200 Type set"); continue; }
        if (!strcmp(cmd, "MODE") || !strcmp(cmd, "STRU")) { out(f, "200 OK"); continue; }
        if (!strcmp(cmd, "REST")) { f->rest = argp ? _atoi64(argp) : 0; out(f, "350 Restarting at %I64d", f->rest); continue; }
        if (!strcmp(cmd, "PASV") || !strcmp(cmd, "EPSV")) {
            struct sockaddr_in a; int al = sizeof a;
            if (f->pasv != INVALID_SOCKET) closesocket(f->pasv);
            f->pasv = socket(AF_INET, SOCK_STREAM, 0);
            getsockname(f->c, (struct sockaddr *)&a, &al);
            a.sin_port = 0;
            bind(f->pasv, (struct sockaddr *)&a, sizeof a);
            listen(f->pasv, 1);
            getsockname(f->pasv, (struct sockaddr *)&a, &al);
            if (!strcmp(cmd, "EPSV")) out(f, "229 Entering Extended Passive Mode (|||%d|)", ntohs(a.sin_port));
            else {
                unsigned char *ip = (unsigned char *)&a.sin_addr.s_addr;
                out(f, "227 Entering Passive Mode (%d,%d,%d,%d,%d,%d)", ip[0], ip[1], ip[2], ip[3], ntohs(a.sin_port) >> 8, ntohs(a.sin_port) & 255);
            }
            continue;
        }
        if (!strcmp(cmd, "PORT")) {
            int h1, h2, h3, h4, p1, p2;
            if (!argp || sscanf(argp, "%d,%d,%d,%d,%d,%d", &h1, &h2, &h3, &h4, &p1, &p2) != 6) { out(f, "501 Bad PORT"); continue; }
            memset(&f->port_addr, 0, sizeof f->port_addr);
            f->port_addr.sin_family = AF_INET;
            f->port_addr.sin_addr.s_addr = htonl((u_long)(h1 << 24 | h2 << 16 | h3 << 8 | h4));
            f->port_addr.sin_port = htons((u_short)(p1 << 8 | p2));
            f->have_port = 1;
            out(f, "200 PORT OK");
            continue;
        }
        if (!strcmp(cmd, "LIST") || !strcmp(cmd, "NLST")) {
            const char *a2 = argp;
            while (a2 && *a2 == '-') { a2 = strchr(a2, ' '); if (a2) a2++; }   /* ignore "-la" */
            if (!resolve(f, a2 && *a2 ? a2 : NULL, virt, real)) { out(f, "550 No such folder"); continue; }
            list_dir(f, real, !strcmp(cmd, "NLST"));
            continue;
        }
        if (!argp) { out(f, "501 Missing argument"); continue; }
        if (!resolve(f, argp, virt, real) || !real[0]) { out(f, "550 Not allowed"); continue; }

        if (!strcmp(cmd, "RETR")) { retr(f, real); continue; }
        if (!strcmp(cmd, "STOR")) { stor(f, real, 0); continue; }
        if (!strcmp(cmd, "APPE")) { stor(f, real, 1); continue; }
        if (!strcmp(cmd, "SIZE") || !strcmp(cmd, "MDTM")) {
            WIN32_FILE_ATTRIBUTE_DATA ad;
            wchar_t w[PATHW];
            utf8_to_w(real, w, PATHW);
            if (!GetFileAttributesExW(w, GetFileExInfoStandard, &ad) || (ad.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) { out(f, "550 No such file"); continue; }
            if (cmd[0] == 'S') out(f, "213 %.0f", (double)ad.nFileSizeHigh * 4294967296.0 + ad.nFileSizeLow);
            else { SYSTEMTIME st; FileTimeToSystemTime(&ad.ftLastWriteTime, &st);
                out(f, "213 %04d%02d%02d%02d%02d%02d", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond); }
            continue;
        }
        if (!strcmp(cmd, "DELE")) { if (fs_delete(real, NULL)) { log_msg("ftp %s: deleted %s", f->user, real); out(f, "250 Deleted"); } else out(f, "550 Cannot delete"); continue; }
        if (!strcmp(cmd, "MKD") || !strcmp(cmd, "XMKD")) { if (fs_mkdir(real)) out(f, "257 \"%s\" created", virt); else out(f, "550 Cannot create"); continue; }
        if (!strcmp(cmd, "RMD") || !strcmp(cmd, "XRMD")) {
            wchar_t w[PATHW]; utf8_to_w(real, w, PATHW);
            if (RemoveDirectoryW(w)) out(f, "250 Removed"); else out(f, "550 Cannot remove (must be empty)");
            continue;
        }
        if (!strcmp(cmd, "RNFR")) { strcpy(f->rnfr, real); out(f, "350 Ready for RNTO"); continue; }
        if (!strcmp(cmd, "RNTO")) {
            if (f->rnfr[0] && fs_rename(f->rnfr, real)) { log_msg("ftp %s: renamed %s -> %s", f->user, f->rnfr, real); out(f, "250 Renamed"); }
            else out(f, "550 Cannot rename");
            f->rnfr[0] = 0;
            continue;
        }
        out(f, "502 Command not implemented");
    }
    if (f->pasv != INVALID_SOCKET) closesocket(f->pasv);
    closesocket(f->c);
    return 0;
}

DWORD WINAPI ftp_server(LPVOID arg)
{
    SOCKET ls;
    struct sockaddr_in a;
    int one = 1;
    (void)arg;
    ls = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)A.ftp_port); a.sin_addr.s_addr = inet_addr(A.bind);
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 8)) { log_msg("cannot listen on %s:%d (FTP)", A.bind, A.ftp_port); return 1; }
    log_msg("FTP on %s:%d (root %s)", A.bind, A.ftp_port, A.ftp_root[0] ? A.ftp_root : "all drives");
    while (!A.quit) {
        fd_set fs; struct timeval tv = { 1, 0 };
        FD_ZERO(&fs); FD_SET(ls, &fs);
        if (select(0, &fs, NULL, NULL, &tv) <= 0) continue;
        {   SOCKET c = accept(ls, NULL, NULL);
            if (c != INVALID_SOCKET) CloseHandle(CreateThread(NULL, 0, ftp_client, (LPVOID)c, 0, NULL)); }
    }
    closesocket(ls);
    return 0;
}
