/* neoagent: config, log, helpers, process supervisor, main. */
#include "neoagent.h"

agent_t A;

/* ---------------------------------------------------------------- log (ring buffer) */
#define LOG_LINES 2000
static struct { unsigned int seq; char text[300]; } logbuf[LOG_LINES];
static unsigned int log_seq;
static FILE *log_file;

void now_stamp(char *out, int n)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    _snprintf(out, n, "%04d%02d%02d-%02d%02d%02d", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
}

void log_line(const char *src, const char *text)
{
    SYSTEMTIME t;
    char line[300];
    GetLocalTime(&t);
    _snprintf(line, sizeof line, "%02d:%02d:%02d %s: %s", t.wHour, t.wMinute, t.wSecond, src, text);
    line[sizeof line - 1] = 0;
    EnterCriticalSection(&A.lock);
    logbuf[log_seq % LOG_LINES].seq = log_seq;
    strcpy(logbuf[log_seq % LOG_LINES].text, line);
    log_seq++;
    if (log_file) { fprintf(log_file, "%04d-%02d-%02d %s\n", t.wYear, t.wMonth, t.wDay, line); fflush(log_file); }
    LeaveCriticalSection(&A.lock);
    printf("%s\n", line);
    fflush(stdout);
}

void log_msg(const char *fmt, ...)
{
    char b[290];
    va_list ap;
    va_start(ap, fmt); _vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    b[sizeof b - 1] = 0;
    log_line("agent", b);
}

/* JSON: {"next":N,"lines":["...",...]} with lines of seq >= since */
int log_read(unsigned int since, char *out, int max, unsigned int *next)
{
    unsigned int s, first;
    int n;
    EnterCriticalSection(&A.lock);
    first = log_seq > LOG_LINES ? log_seq - LOG_LINES : 0;
    if (since < first) since = first;
    n = _snprintf(out, max, "{\"next\":%u,\"lines\":[", log_seq);
    for (s = since; s < log_seq && n < max - 700; s++) {
        if (s != since) out[n++] = ',';
        n += json_str(out + n, max - n, logbuf[s % LOG_LINES].text);
    }
    *next = log_seq;
    LeaveCriticalSection(&A.lock);
    n += _snprintf(out + n, max - n, "]}");
    return n;
}

/* ---------------------------------------------------------------- helpers */
int utf8_to_w(const char *s, wchar_t *w, int n) { return MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n); }
int w_to_utf8(const wchar_t *w, char *s, int n) { return WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL); }

int json_str(char *out, int max, const char *s)
{
    int n = 0;
    if (max < 3) return 0;
    out[n++] = '"';
    for (; *s && n < max - 8; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { out[n++] = '\\'; out[n++] = (char)c; }
        else if (c < 32) n += _snprintf(out + n, max - n, "\\u%04x", c);
        else out[n++] = (char)c;
    }
    out[n++] = '"';
    out[n] = 0;
    return n;
}

void url_decode(char *s)
{
    char *d = s;
    for (; *s; s++, d++) {
        if (*s == '%' && s[1] && s[2]) {
            char h[3] = { s[1], s[2], 0 };
            *d = (char)strtol(h, NULL, 16); s += 2;
        } else if (*s == '+') *d = ' ';
        else *d = *s;
    }
    *d = 0;
}

int query_get(const char *q, const char *key, char *out, int max)
{
    size_t kl = strlen(key);
    while (q && *q) {
        const char *amp = strchr(q, '&');
        size_t len = amp ? (size_t)(amp - q) : strlen(q);
        if (len > kl && !strncmp(q, key, kl) && q[kl] == '=') {
            size_t vl = len - kl - 1;
            if ((int)vl >= max) vl = max - 1;
            memcpy(out, q + kl + 1, vl); out[vl] = 0;
            url_decode(out);
            return 1;
        }
        q = amp ? amp + 1 : NULL;
    }
    out[0] = 0;
    return 0;
}

/* absolute Windows path (X:\...), no ".." component */
int path_ok(const char *p)
{
    if (!p || strlen(p) < 2 || p[1] != ':' || !((p[0] >= 'A' && p[0] <= 'Z') || (p[0] >= 'a' && p[0] <= 'z'))) return 0;
    if (strstr(p, "\\..") || strstr(p, "/..")) return 0;
    return 1;
}

/* ---------------------------------------------------------------- config */
static void ini_str(const char *sec, const char *key, const char *def, char *out, int n)
{
    GetPrivateProfileStringA(sec, key, def, out, n, A.ini);
}

static void abs_path(char *p, int n)
{
    char t[MAX_PATH];
    if (p[0] && !(p[1] == ':' || (p[0] == '\\' && p[1] == '\\'))) {
        _snprintf(t, sizeof t, "%s\\%s", A.home, p);
        strncpy(p, t, n - 1); p[n - 1] = 0;
    }
}

static void write_default_ini(void)
{
    FILE *f = fopen(A.ini, "w");
    if (!f) return;
    fprintf(f,
        "; neoagent settings (restart neoagent after editing)\n"
        "[agent]\n"
        "http_port=8080\n"
        "ftp_port=21\n"
        "bind=0.0.0.0\n"
        "user=admin\n"
        "password=neoden\n"
        "; HMAC key for update packages (change it; tools/mkpkg.py needs the same key)\n"
        "update_key=change-me\n"
        "www=www\n"
        "backup=backup\n"
        "; FTP root: empty = all drives\n"
        "ftp_root=\n"
        "cam_port=5600\n"
        "serbridge_port=5602\n"
        "; NeoDen4 data folder (Config.ini, Proc\\*.csv)\n"
        "neoden_dir=D:\\NeoDen4\n"
        "\n"
        "; programs kept running: name=folder|command line   (folder empty = this folder)\n"
        "[procs]\n"
        "camserver=D:\\NeoDen4|camserver.exe --bind 127.0.0.1\n"
        "serbridge=D:\\NeoDen4|serbridge.exe --bind 127.0.0.1\n");
    fclose(f);
}

static void load_config(void)
{
    char b[4096], v[1024], *p;
    GetModuleFileNameA(NULL, A.home, sizeof A.home);
    if (strrchr(A.home, '\\')) *strrchr(A.home, '\\') = 0;
    if (!A.ini[0]) _snprintf(A.ini, sizeof A.ini, "%s\\neoagent.ini", A.home);
    if (GetFileAttributesA(A.ini) == INVALID_FILE_ATTRIBUTES) write_default_ini();
    A.http_port = GetPrivateProfileIntA("agent", "http_port", 8080, A.ini);
    A.ftp_port = GetPrivateProfileIntA("agent", "ftp_port", 21, A.ini);
    A.cam_port = GetPrivateProfileIntA("agent", "cam_port", 5600, A.ini);
    A.sb_port = GetPrivateProfileIntA("agent", "serbridge_port", 5602, A.ini);
    ini_str("agent", "bind", "0.0.0.0", A.bind, sizeof A.bind);
    ini_str("agent", "user", "admin", A.user, sizeof A.user);
    ini_str("agent", "password", "neoden", A.password, sizeof A.password);
    ini_str("agent", "update_key", "change-me", A.update_key, sizeof A.update_key);
    ini_str("agent", "www", "www", A.www, sizeof A.www); abs_path(A.www, sizeof A.www);
    ini_str("agent", "backup", "backup", A.backup, sizeof A.backup); abs_path(A.backup, sizeof A.backup);
    ini_str("agent", "ftp_root", "", A.ftp_root, sizeof A.ftp_root);
    ini_str("agent", "neoden_dir", "D:\\NeoDen4", A.neoden_dir, sizeof A.neoden_dir);
    CreateDirectoryA(A.backup, NULL);

    A.nprocs = 0;
    GetPrivateProfileSectionA("procs", b, sizeof b, A.ini);
    for (p = b; *p && A.nprocs < MAX_PROCS; p += strlen(p) + 1) {
        char *eq = strchr(p, '='), *bar;
        proc_t *pr = &A.procs[A.nprocs];
        if (!eq || *p == ';') continue;
        memset(pr, 0, sizeof *pr);
        _snprintf(pr->name, sizeof pr->name, "%.*s", (int)(eq - p), p);
        strncpy(v, eq + 1, sizeof v - 1); v[sizeof v - 1] = 0;
        bar = strchr(v, '|');
        if (bar) { *bar = 0; strncpy(pr->dir, v, sizeof pr->dir - 1); strncpy(pr->cmd, bar + 1, sizeof pr->cmd - 1); }
        else strncpy(pr->cmd, v, sizeof pr->cmd - 1);
        if (!pr->dir[0]) strcpy(pr->dir, A.home);
        pr->wanted = 1;
        A.nprocs++;
    }
}

/* ---------------------------------------------------------------- supervisor */
typedef struct { HANDLE rd; char name[32]; } pipe_arg_t;

static DWORD WINAPI pipe_reader(LPVOID arg)
{
    pipe_arg_t *pa = (pipe_arg_t *)arg;
    char buf[512], line[290];
    int ln = 0;
    DWORD n, i;
    while (ReadFile(pa->rd, buf, sizeof buf, &n, NULL) && n) {
        for (i = 0; i < n; i++) {
            if (buf[i] == '\n' || ln == (int)sizeof line - 1) {
                line[ln] = 0;
                if (ln && line[ln - 1] == '\r') line[ln - 1] = 0;
                if (line[0]) log_line(pa->name, line);
                ln = 0;
            } else line[ln++] = buf[i];
        }
    }
    CloseHandle(pa->rd);
    free(pa);
    return 0;
}

int proc_find(const char *name)
{
    int i;
    for (i = 0; i < A.nprocs; i++) if (!strcmp(A.procs[i].name, name)) return i;
    return -1;
}

int proc_running(int i)
{
    DWORD code;
    proc_t *p = &A.procs[i];
    return p->proc && GetExitCodeProcess(p->proc, &code) && code == STILL_ACTIVE;
}

int proc_start(int i)
{
    proc_t *p = &A.procs[i];
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    SECURITY_ATTRIBUTES sa;
    HANDLE rd, wr;
    char cmd[MAX_PATH + 600];
    pipe_arg_t *pa;

    if (proc_running(i)) return 1;
    if (p->proc) { CloseHandle(p->proc); p->proc = NULL; }
    sa.nLength = sizeof sa; sa.lpSecurityDescriptor = NULL; sa.bInheritHandle = TRUE;
    if (!CreatePipe(&rd, &wr, &sa, 0)) return 0;
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    si.hStdOutput = wr; si.hStdError = wr; si.hStdInput = NULL;
    /* the program name is resolved in its own folder */
    _snprintf(cmd, sizeof cmd, "\"%s\\%s", p->dir, p->cmd);
    {
        char *sp = strchr(cmd + 1 + strlen(p->dir), ' ');
        if (sp) { memmove(sp + 1, sp, strlen(sp) + 1); *sp = '"'; } else strcat(cmd, "\"");
    }
    if (!CreateProcessA(NULL, cmd, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, p->dir, &si, &pi)) {
        log_msg("cannot start %s (%s): error %lu", p->name, cmd, GetLastError());
        CloseHandle(rd); CloseHandle(wr);
        return 0;
    }
    CloseHandle(wr);
    CloseHandle(pi.hThread);
    p->proc = pi.hProcess; p->pid = pi.dwProcessId; p->started = GetTickCount();
    pa = (pipe_arg_t *)malloc(sizeof *pa);
    pa->rd = rd; strcpy(pa->name, p->name);
    CloseHandle(CreateThread(NULL, 0, pipe_reader, pa, 0, NULL));
    log_msg("started %s (pid %lu)", p->name, p->pid);
    return 1;
}

void proc_stop(int i)
{
    proc_t *p = &A.procs[i];
    if (proc_running(i)) {
        TerminateProcess(p->proc, 1);
        WaitForSingleObject(p->proc, 5000);
        log_msg("stopped %s", p->name);
    }
    if (p->proc) { CloseHandle(p->proc); p->proc = NULL; }
}

void procs_json(char *out, int max)
{
    int i, n = _snprintf(out, max, "[");
    EnterCriticalSection(&A.lock);
    for (i = 0; i < A.nprocs && n < max - 600; i++) {
        proc_t *p = &A.procs[i];
        int run = proc_running(i);
        n += _snprintf(out + n, max - n, "%s{\"name\":", i ? "," : "");
        n += json_str(out + n, max - n, p->name);
        n += _snprintf(out + n, max - n, ",\"dir\":");
        n += json_str(out + n, max - n, p->dir);
        n += _snprintf(out + n, max - n, ",\"cmd\":");
        n += json_str(out + n, max - n, p->cmd);
        n += _snprintf(out + n, max - n, ",\"running\":%s,\"wanted\":%s,\"pid\":%lu,\"restarts\":%d,\"uptime_s\":%lu}",
                       run ? "true" : "false", p->wanted ? "true" : "false", run ? p->pid : 0UL, p->restarts,
                       run ? (GetTickCount() - p->started) / 1000 : 0UL);
    }
    LeaveCriticalSection(&A.lock);
    _snprintf(out + n, max - n, "]");
}

static DWORD WINAPI supervisor(LPVOID arg)
{
    int i;
    (void)arg;
    while (!A.quit) {
        EnterCriticalSection(&A.lock);
        for (i = 0; i < A.nprocs; i++) {
            proc_t *p = &A.procs[i];
            if (!p->wanted || proc_running(i) || GetTickCount() < p->next_try) continue;
            if (p->proc) {                           /* it was running and exited */
                DWORD code = 0;
                GetExitCodeProcess(p->proc, &code);
                log_msg("%s exited (code %lu), restarting", p->name, code);
                p->restarts++;
            }
            if (!proc_start(i)) p->next_try = GetTickCount() + 10000;
            else p->next_try = GetTickCount() + 3000;    /* no faster than every 3 s */
        }
        LeaveCriticalSection(&A.lock);
        Sleep(1000);
    }
    return 0;
}

/* ---------------------------------------------------------------- main */
int main(int argc, char **argv)
{
    WSADATA wsa;
    int i;
    char lf[MAX_PATH];

    InitializeCriticalSection(&A.lock);
    A.start_tick = GetTickCount();
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ini") && i + 1 < argc) strncpy(A.ini, argv[++i], sizeof A.ini - 1);
        else if (!strcmp(argv[i], "--after-update")) ;      /* started by a self update */
        else { printf("usage: neoagent [--ini neoagent.ini]\n"); return 2; }
    }
    load_config();
    _snprintf(lf, sizeof lf, "%s\\neoagent.log", A.home);
    log_file = fopen(lf, "a");
    log_msg("neoagent %s in %s, http %d, ftp %d, %d program(s)", AGENT_VERSION, A.home, A.http_port, A.ftp_port, A.nprocs);
    if (!strcmp(A.password, "neoden") || !strcmp(A.update_key, "change-me"))
        log_msg("WARNING: default password or update key in %s; change them", A.ini);

    if (WSAStartup(MAKEWORD(2, 0), &wsa)) { log_msg("WSAStartup failed"); return 1; }
    CloseHandle(CreateThread(NULL, 0, supervisor, NULL, 0, NULL));
    if (A.ftp_port > 0) CloseHandle(CreateThread(NULL, 0, ftp_server, NULL, 0, NULL));
    http_server(NULL);                  /* returns when a self update hands over */
    if (self_update_pending()) for (;;) Sleep(1000);    /* the swap thread ends the process */
    for (i = 0; i < A.nprocs; i++) A.procs[i].wanted = 0;
    log_msg("neoagent exits");
    if (log_file) fclose(log_file);
    return 0;
}
