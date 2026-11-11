/* neoagent: HTTP/1.0-style server (one thread per connection, Connection: close),
 * Basic authentication on everything, static web UI from <www>, JSON API under /api/.
 * API reference: ../docs/NEOAGENT.md */
#include "neoagent.h"

#define MAX_HEADER 16384
#define MAX_BODY   (300L * 1024 * 1024)

typedef struct {
    SOCKET s;
    char method[8], path[2048], query[4096];
    char auth[256];
    long clen;
    char *body;
    char peer[32];
} req_t;

static void b64(const char *in, char *out)
{
    static const char t[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t n = strlen(in), i;
    char *o = out;
    for (i = 0; i < n; i += 3) {
        unsigned v = (unsigned char)in[i] << 16 | (i + 1 < n ? (unsigned char)in[i + 1] << 8 : 0) | (i + 2 < n ? (unsigned char)in[i + 2] : 0);
        *o++ = t[v >> 18 & 63]; *o++ = t[v >> 12 & 63];
        *o++ = i + 1 < n ? t[v >> 6 & 63] : '=';
        *o++ = i + 2 < n ? t[v & 63] : '=';
    }
    *o = 0;
}

static int send_all(SOCKET s, const char *p, long n)
{
    while (n > 0) { int k = send(s, p, n > 65536 ? 65536 : (int)n, 0); if (k <= 0) return 0; p += k; n -= k; }
    return 1;
}

static void reply(req_t *r, int code, const char *type, const char *body, long len, const char *extra)
{
    char h[1024];
    const char *msg = code == 200 ? "OK" : code == 401 ? "Unauthorized" : code == 404 ? "Not Found" :
                      code == 400 ? "Bad Request" : code == 409 ? "Conflict" : code == 413 ? "Too Large" : "Error";
    int n = _snprintf(h, sizeof h, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %ld\r\n"
                      "Cache-Control: no-store\r\nConnection: close\r\n%s\r\n",
                      code, msg, type, len, extra ? extra : "");
    send_all(r->s, h, n);
    if (len && strcmp(r->method, "HEAD")) send_all(r->s, body, len);
}

static void reply_json(req_t *r, int code, const char *json) { reply(r, code, "application/json; charset=utf-8", json, (long)strlen(json), NULL); }

static void reply_err(req_t *r, int code, const char *msg)
{
    char b[1200];
    int n = _snprintf(b, sizeof b, "{\"ok\":false,\"error\":");
    json_str(b + n, sizeof b - n, msg);
    strcat(b, "}");
    reply_json(r, code, b);
}

static void reply_ok(req_t *r, const char *extra_json)
{
    char b[2048];
    _snprintf(b, sizeof b, "{\"ok\":true%s%s}", extra_json ? "," : "", extra_json ? extra_json : "");
    reply_json(r, 200, b);
}

static const char *mime(const char *p)
{
    const char *e = strrchr(p, '.');
    if (!e) return "application/octet-stream";
    if (!_stricmp(e, ".html") || !_stricmp(e, ".htm")) return "text/html; charset=utf-8";
    if (!_stricmp(e, ".js")) return "text/javascript; charset=utf-8";
    if (!_stricmp(e, ".css")) return "text/css; charset=utf-8";
    if (!_stricmp(e, ".json")) return "application/json";
    if (!_stricmp(e, ".png")) return "image/png";
    if (!_stricmp(e, ".svg")) return "image/svg+xml";
    if (!_stricmp(e, ".wasm")) return "application/wasm";
    if (!_stricmp(e, ".bmp")) return "image/bmp";
    if (!_stricmp(e, ".txt") || !_stricmp(e, ".ini") || !_stricmp(e, ".csv") || !_stricmp(e, ".log")) return "text/plain; charset=utf-8";
    return "application/octet-stream";
}

static void serve_static(req_t *r)
{
    char p[MAX_PATH * 2], *data;
    long len;
    const char *rel = r->path[1] ? r->path + 1 : "index.html";
    char *q;
    if (strstr(rel, "..") || strchr(rel, ':')) { reply_err(r, 400, "bad path"); return; }
    _snprintf(p, sizeof p, "%s\\%s", A.www, rel);
    for (q = p; *q; q++) if (*q == '/') *q = '\\';
    if (!fs_read(p, &data, &len)) { reply_err(r, 404, "not found"); return; }
    reply(r, 200, mime(p), data, len, NULL);
    free(data);
}

/* ---------------------------------------------------------------- API */
static void api(req_t *r)
{
    const char *ep = r->path + 5;              /* after "/api/" */
    char a[2048], b[2048], c[64];
    static char big[512 * 1024];               /* JSON scratch; serialised by big_lock */
    static CRITICAL_SECTION big_lock;
    static LONG init;
    if (InterlockedCompareExchange(&init, 1, 0) == 0) { InitializeCriticalSection(&big_lock); init = 2; }
    while (init != 2) Sleep(1);

    if (!strcmp(ep, "ping")) { reply_ok(r, "\"agent\":\"neoagent " AGENT_VERSION "\""); return; }

    if (!strcmp(ep, "status")) {
        char host[128] = "", procs[8192], cam[512];
        DWORD hn = sizeof host;
        GetComputerNameA(host, &hn);
        procs_json(procs, sizeof procs);
        cam_info(cam, sizeof cam);
        EnterCriticalSection(&big_lock);
        {   char nd[MAX_PATH * 2]; json_str(nd, sizeof nd, A.neoden_dir);
            _snprintf(big, sizeof big, "{\"ok\":true,\"agent\":\"%s\",\"host\":\"%s\",\"uptime_s\":%lu,\"neoden_dir\":%s,\"procs\":%s,\"camera\":%s}",
                      AGENT_VERSION, host, (GetTickCount() - A.start_tick) / 1000, nd, procs, cam); }
        reply_json(r, 200, big);
        LeaveCriticalSection(&big_lock);
        return;
    }

    if (!strcmp(ep, "procs")) { char p[8192]; procs_json(p, sizeof p); reply_json(r, 200, p); return; }

    if (!strcmp(ep, "proc")) {                 /* POST ?name=&action=start|stop|restart */
        int i;
        query_get(r->query, "name", a, sizeof a); query_get(r->query, "action", b, sizeof b);
        EnterCriticalSection(&A.lock);
        i = proc_find(a);
        if (i < 0) { LeaveCriticalSection(&A.lock); reply_err(r, 404, "no such program"); return; }
        if (!strcmp(b, "stop")) { A.procs[i].wanted = 0; proc_stop(i); }
        else if (!strcmp(b, "start")) { A.procs[i].wanted = 1; proc_start(i); }
        else if (!strcmp(b, "restart")) { proc_stop(i); A.procs[i].wanted = 1; proc_start(i); }
        else { LeaveCriticalSection(&A.lock); reply_err(r, 400, "action must be start, stop or restart"); return; }
        LeaveCriticalSection(&A.lock);
        log_msg("%s: %s %s", r->peer, b, a);
        reply_ok(r, NULL);
        return;
    }

    if (!strcmp(ep, "log")) {
        unsigned int next;
        query_get(r->query, "since", a, sizeof a);
        EnterCriticalSection(&big_lock);
        log_read((unsigned int)strtoul(a, NULL, 10), big, sizeof big, &next);
        reply_json(r, 200, big);
        LeaveCriticalSection(&big_lock);
        return;
    }

    if (!strcmp(ep, "files")) {                /* GET ?path=   ("" = drives) */
        query_get(r->query, "path", a, sizeof a);
        EnterCriticalSection(&big_lock);
        if (fs_list_json(a, big, sizeof big) < 0) reply_err(r, 404, "cannot list folder");
        else reply_json(r, 200, big);
        LeaveCriticalSection(&big_lock);
        return;
    }

    if (!strcmp(ep, "file")) {                 /* GET (read) / PUT (write, backed up) ?path= */
        query_get(r->query, "path", a, sizeof a);
        if (!path_ok(a)) { reply_err(r, 400, "path must be absolute, e.g. D:\\NeoDen4\\Config.ini"); return; }
        if (!strcmp(r->method, "GET")) {
            char *d; long n;
            if (!fs_read(a, &d, &n)) { reply_err(r, 404, "cannot read file"); return; }
            reply(r, 200, mime(a), d, n, NULL);
            free(d);
        } else if (!strcmp(r->method, "PUT") || !strcmp(r->method, "POST")) {
            char set[MAX_PATH] = "", j[MAX_PATH + 40];
            if (!fs_write(a, r->body ? r->body : "", r->clen, set)) { reply_err(r, 500, "cannot write file"); return; }
            log_msg("%s: wrote %s (%ld bytes)%s%s", r->peer, a, r->clen, set[0] ? ", backup " : "", set[0] ? strrchr(set, '\\') + 1 : "");
            _snprintf(j, sizeof j, "\"backup\":\"%s\"", set[0] ? strrchr(set, '\\') + 1 : "");
            reply_ok(r, j);
        } else reply_err(r, 400, "use GET or PUT");
        return;
    }

    if (!strcmp(ep, "delete")) {
        char set[MAX_PATH] = "";
        query_get(r->query, "path", a, sizeof a);
        if (!fs_delete(a, set)) { reply_err(r, 500, "cannot delete (folders must be empty)"); return; }
        log_msg("%s: deleted %s", r->peer, a);
        reply_ok(r, NULL);
        return;
    }

    if (!strcmp(ep, "mkdir")) {
        query_get(r->query, "path", a, sizeof a);
        if (!fs_mkdir(a)) reply_err(r, 500, "cannot create folder"); else reply_ok(r, NULL);
        return;
    }

    if (!strcmp(ep, "rename")) {
        query_get(r->query, "from", a, sizeof a); query_get(r->query, "to", b, sizeof b);
        if (!fs_rename(a, b)) reply_err(r, 500, "cannot rename"); else { log_msg("%s: renamed %s -> %s", r->peer, a, b); reply_ok(r, NULL); }
        return;
    }

    if (!strcmp(ep, "backups")) {
        EnterCriticalSection(&big_lock);
        backups_json(big, sizeof big);
        reply_json(r, 200, big);
        LeaveCriticalSection(&big_lock);
        return;
    }

    if (!strcmp(ep, "restore")) {
        char rep[8192], j[9000];
        int ok;
        query_get(r->query, "id", a, sizeof a);
        ok = backup_restore(a, rep, sizeof rep);
        log_msg("%s: restore %s: %s", r->peer, a, ok ? "ok" : "failed");
        j[0] = 0; strcat(j, "\"report\":"); json_str(j + strlen(j), sizeof j - (int)strlen(j), rep);
        if (ok) reply_ok(r, j); else reply_err(r, 500, rep);
        return;
    }

    if (!strncmp(ep, "cam/", 4)) {
        int cam;
        query_get(r->query, "cam", c, sizeof c);
        cam = atoi(c);
        if (!strcmp(ep + 4, "grab")) {         /* GET ?cam=0|1&scale=1..8  -> BMP */
            char *bmp, err[256];
            long n;
            query_get(r->query, "scale", a, sizeof a);
            if (!cam_grab_bmp(cam, a[0] ? atoi(a) : 1, &bmp, &n, err, sizeof err)) { reply_err(r, 502, err); return; }
            reply(r, 200, "image/bmp", bmp, n, NULL);
            free(bmp);
        } else if (!strcmp(ep + 4, "set")) {   /* POST ?cam=&exposure=&gain=&w=&h=&led= (each optional) */
            char e[16], g[16], w[16], h[16], l[16], err[256];
            query_get(r->query, "exposure", e, sizeof e); query_get(r->query, "gain", g, sizeof g);
            query_get(r->query, "w", w, sizeof w); query_get(r->query, "h", h, sizeof h); query_get(r->query, "led", l, sizeof l);
            if (!cam_set(cam, e[0] ? atoi(e) : 0, g[0] ? atoi(g) : -1, w[0] ? atoi(w) : 0, h[0] ? atoi(h) : 0, l[0] ? atoi(l) : -1, err, sizeof err))
                reply_err(r, 502, err);
            else reply_ok(r, NULL);
        } else reply_err(r, 404, "unknown camera call");
        return;
    }

    if (!strncmp(ep, "mc/", 3)) {              /* machine control (machine.c) */
        const char *cmd = ep + 3;
        char client[64], extra[512], err[400], j[1200];
        query_get(r->query, "client", client, sizeof client);
        if (!strcmp(cmd, "status")) {
            EnterCriticalSection(&big_lock);
            machine_status(big, sizeof big);
            reply_json(r, 200, big);
            LeaveCriticalSection(&big_lock);
        } else if (!strcmp(cmd, "lock") || !strcmp(cmd, "unlock")) {
            if (!client[0]) { reply_err(r, 400, "client=<id> needed"); return; }
            machine_lock(client, cmd[0] == 'u', j, sizeof j);
            reply_json(r, 200, j);
        } else if (!strcmp(cmd, "cancel")) {
            machine_cancel();
            reply_ok(r, NULL);
        } else if (strcmp(r->method, "POST")) {
            reply_err(r, 400, "use POST");
        } else if (machine_cmd(cmd, r->query, client, extra, sizeof extra, err, sizeof err)) {
            reply_ok(r, extra[0] ? extra : NULL);
        } else reply_err(r, 409, err);
        return;
    }

    if (!strcmp(ep, "update")) {               /* POST body = package (tools/mkpkg.py) */
        static char rep[64 * 1024];
        char *j;
        int ok;
        if (strcmp(r->method, "POST") || !r->body) { reply_err(r, 400, "POST the package as the body"); return; }
        EnterCriticalSection(&big_lock);
        ok = update_apply(r->body, r->clen, rep, sizeof rep);
        log_msg("%s: update %s", r->peer, ok ? "applied" : "rejected");
        j = (char *)malloc(2 * sizeof rep + 64);
        sprintf(j, "{\"ok\":%s,\"report\":", ok ? "true" : "false");
        json_str(j + strlen(j), (int)(2 * sizeof rep), rep);
        strcat(j, "}");
        reply_json(r, ok ? 200 : 500, j);
        free(j);
        LeaveCriticalSection(&big_lock);
        return;
    }

    reply_err(r, 404, "unknown API call");
}

/* ---------------------------------------------------------------- connection */
static DWORD WINAPI conn_thread(LPVOID arg)
{
    req_t r;
    char hdr[MAX_HEADER + 1], expect[300], *p, *line, *end;
    int got = 0, k;
    memset(&r, 0, sizeof r);
    r.s = (SOCKET)arg;
    {
        struct sockaddr_in pa; int pl = sizeof pa;
        getpeername(r.s, (struct sockaddr *)&pa, &pl);
        strcpy(r.peer, inet_ntoa(pa.sin_addr));
    }
    {   int to = 30000; setsockopt(r.s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&to, sizeof to); }

    end = NULL;
    while (!end && got < MAX_HEADER) {         /* the first read may already hold part of the body */
        k = recv(r.s, hdr + got, MAX_HEADER - got, 0);
        if (k <= 0) goto done;
        got += k;
        hdr[got] = 0;
        end = strstr(hdr, "\r\n\r\n");
    }
    if (!end) goto done;
    *end = 0;
    line = hdr;
    if (sscanf(line, "%7s %2047s", r.method, r.path) != 2) goto done;
    if ((p = strchr(r.path, '?'))) { *p = 0; strncpy(r.query, p + 1, sizeof r.query - 1); }
    url_decode(r.path);
    for (p = strstr(hdr, "\r\n"); p; p = strstr(p + 2, "\r\n")) {
        char *h = p + 2;
        if (!_strnicmp(h, "Content-Length:", 15)) r.clen = atol(h + 15);
        else if (!_strnicmp(h, "Authorization:", 14)) sscanf(h + 14, " %*s %255s", r.auth);
    }

    {   /* Basic auth on everything */
        char up[200], want[300];
        _snprintf(up, sizeof up, "%s:%s", A.user, A.password);
        b64(up, want);
        if (strcmp(r.auth, want)) {
            r.clen = 0;
            reply(&r, 401, "text/plain", "login required", 14, "WWW-Authenticate: Basic realm=\"NeoDen4\"\r\n");
            goto done;
        }
    }

    if (r.clen < 0 || r.clen > MAX_BODY) { reply_err(&r, 413, "body too large"); goto done; }
    if (r.clen > 0) {
        long have = got - (long)(end + 4 - hdr);
        r.body = (char *)malloc(r.clen + 1);
        if (!r.body) { reply_err(&r, 500, "out of memory"); goto done; }
        if (have > r.clen) have = r.clen;
        memcpy(r.body, end + 4, have);
        while (have < r.clen) {
            k = recv(r.s, r.body + have, (int)(r.clen - have > 65536 ? 65536 : r.clen - have), 0);
            if (k <= 0) goto done;
            have += k;
        }
        r.body[r.clen] = 0;
    }
    (void)expect;
    if (!strncmp(r.path, "/api/", 5)) api(&r);
    else if (!strcmp(r.method, "GET") || !strcmp(r.method, "HEAD")) serve_static(&r);
    else reply_err(&r, 400, "bad request");
done:
    free(r.body);
    shutdown(r.s, SD_SEND);
    closesocket(r.s);
    return 0;
}

DWORD WINAPI http_server(LPVOID arg)
{
    SOCKET ls;
    struct sockaddr_in a;
    int one = 1;
    (void)arg;
    ls = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)A.http_port); a.sin_addr.s_addr = inet_addr(A.bind);
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 16)) {
        log_msg("cannot listen on %s:%d (HTTP)", A.bind, A.http_port);
        return 1;
    }
    log_msg("HTTP on %s:%d, web UI from %s", A.bind, A.http_port, A.www);
    while (!A.quit) {
        fd_set fs;
        struct timeval tv = { 1, 0 };
        FD_ZERO(&fs); FD_SET(ls, &fs);
        if (select(0, &fs, NULL, NULL, &tv) <= 0) continue;
        {
            SOCKET c = accept(ls, NULL, NULL);
            if (c != INVALID_SOCKET) CloseHandle(CreateThread(NULL, 0, conn_thread, (LPVOID)c, 0, NULL));
        }
    }
    closesocket(ls);
    return 0;
}
