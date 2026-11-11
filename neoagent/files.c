/* neoagent: file system operations (UTF-8 paths, wide Win32 API) and backups.
 * Every overwrite or delete first copies the old file into a backup set:
 *   <backup>\<YYYYMMDD-HHMMSS>-<n>\files\<drive>\<path>   +  manifest.txt ("why" + original paths) */
#include "neoagent.h"

static LONG backup_counter;

static void ft_to_str(const FILETIME *ft, char *out, int n)
{
    FILETIME lt;
    SYSTEMTIME st;
    FileTimeToLocalFileTime(ft, &lt);
    FileTimeToSystemTime(&lt, &st);
    _snprintf(out, n, "%04d-%02d-%02d %02d:%02d:%02d", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
}

/* "" or "/" lists the drives */
int fs_list_json(const char *path, char *out, int max)
{
    wchar_t wp[PATHW];
    WIN32_FIND_DATAW fd;
    HANDLE h;
    int n, first = 1;
    char name[MAX_PATH * 3], tm[32];

    if (!path[0] || !strcmp(path, "/") || !strcmp(path, "\\")) {
        DWORD d = GetLogicalDrives();
        int i;
        n = _snprintf(out, max, "{\"path\":\"\",\"entries\":[");
        for (i = 0; i < 26; i++)
            if (d & (1u << i)) {
                char root[4] = { (char)('A' + i), ':', '\\', 0 };
                UINT t = GetDriveTypeA(root);
                if (t == DRIVE_NO_ROOT_DIR) continue;
                n += _snprintf(out + n, max - n, "%s{\"name\":\"%c:\",\"dir\":true,\"size\":0,\"mtime\":\"\"}", first ? "" : ",", 'A' + i);
                first = 0;
            }
        return n + _snprintf(out + n, max - n, "]}");
    }
    if (!path_ok(path)) return -1;
    {
        char pat[PATHW];
        _snprintf(pat, sizeof pat, "%s%s*", path, path[strlen(path) - 1] == '\\' ? "" : "\\");
        utf8_to_w(pat, wp, PATHW);
    }
    h = FindFirstFileW(wp, &fd);
    if (h == INVALID_HANDLE_VALUE) return -1;
    n = _snprintf(out, max, "{\"path\":");
    n += json_str(out + n, max - n, path);
    n += _snprintf(out + n, max - n, ",\"entries\":[");
    do {
        if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
        if (n > max - 1200) break;
        w_to_utf8(fd.cFileName, name, sizeof name);
        ft_to_str(&fd.ftLastWriteTime, tm, sizeof tm);
        n += _snprintf(out + n, max - n, "%s{\"name\":", first ? "" : ",");
        n += json_str(out + n, max - n, name);
        n += _snprintf(out + n, max - n, ",\"dir\":%s,\"size\":%.0f,\"mtime\":\"%s\"}",
                       (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? "true" : "false",
                       (double)fd.nFileSizeHigh * 4294967296.0 + fd.nFileSizeLow, tm);
        first = 0;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return n + _snprintf(out + n, max - n, "]}");
}

int fs_read(const char *path, char **data, long *len)
{
    wchar_t wp[PATHW];
    HANDLE h;
    DWORD sz, got;
    if (!path_ok(path)) return 0;
    utf8_to_w(path, wp, PATHW);
    h = CreateFileW(wp, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    sz = GetFileSize(h, NULL);
    if (sz == INVALID_FILE_SIZE || sz > 200u * 1024 * 1024) { CloseHandle(h); return 0; }
    *data = (char *)malloc(sz + 1);
    if (!*data || !ReadFile(h, *data, sz, &got, NULL) || got != sz) { free(*data); CloseHandle(h); return 0; }
    (*data)[sz] = 0;
    *len = (long)sz;
    CloseHandle(h);
    return 1;
}

static int mkdirs_w(const wchar_t *dir)
{
    wchar_t t[PATHW];
    wchar_t *p;
    wcsncpy(t, dir, PATHW - 1); t[PATHW - 1] = 0;
    for (p = t + 3; *p; p++)
        if (*p == L'\\') { *p = 0; CreateDirectoryW(t, NULL); *p = L'\\'; }
    return CreateDirectoryW(t, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
}

int backup_set_new(char *set_dir, int n, const char *why)
{
    char st[32], mf[MAX_PATH + 40];
    FILE *f;
    now_stamp(st, sizeof st);
    _snprintf(set_dir, n, "%s\\%s-%ld", A.backup, st, InterlockedIncrement(&backup_counter));
    CreateDirectoryA(set_dir, NULL);
    _snprintf(mf, sizeof mf, "%s\\manifest.txt", set_dir);
    if ((f = fopen(mf, "a"))) { fprintf(f, "why %s\n", why); fclose(f); }
    return 1;
}

/* copy path into the set (keeps drive + folders); no-op when the file does not exist */
int backup_file(const char *path, const char *set_dir)
{
    char dst[PATHW], mf[MAX_PATH + 40];
    wchar_t ws[PATHW], wd[PATHW], wdir[PATHW], *slash;
    FILE *f;
    utf8_to_w(path, ws, PATHW);
    if (GetFileAttributesW(ws) == INVALID_FILE_ATTRIBUTES) return 1;
    _snprintf(dst, sizeof dst, "%s\\files\\%c\\%s", set_dir, path[0], path + 3);
    utf8_to_w(dst, wd, PATHW);
    wcscpy(wdir, wd);
    if ((slash = wcsrchr(wdir, L'\\'))) { *slash = 0; mkdirs_w(wdir); }
    if (!CopyFileW(ws, wd, FALSE)) return 0;
    _snprintf(mf, sizeof mf, "%s\\manifest.txt", set_dir);
    if ((f = fopen(mf, "a"))) { fprintf(f, "file %s\n", path); fclose(f); }
    return 1;
}

int fs_write(const char *path, const char *data, long len, char *backup_dir)
{
    wchar_t wp[PATHW], wt[PATHW], wdir[PATHW], *slash;
    char tmp[PATHW], set[MAX_PATH];
    HANDLE h;
    DWORD put;
    if (!path_ok(path)) return 0;
    utf8_to_w(path, wp, PATHW);
    if (GetFileAttributesW(wp) != INVALID_FILE_ATTRIBUTES) {
        backup_set_new(set, sizeof set, "write");
        if (!backup_file(path, set)) return 0;
        if (backup_dir) strcpy(backup_dir, set);
    } else if (backup_dir) backup_dir[0] = 0;
    wcscpy(wdir, wp);
    if ((slash = wcsrchr(wdir, L'\\'))) { *slash = 0; mkdirs_w(wdir); }
    /* write next to it, then replace: a crash never leaves a half-written file */
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

int fs_delete(const char *path, char *backup_dir)
{
    wchar_t wp[PATHW];
    DWORD a;
    char set[MAX_PATH];
    if (!path_ok(path) || strlen(path) <= 3) return 0;
    utf8_to_w(path, wp, PATHW);
    a = GetFileAttributesW(wp);
    if (a == INVALID_FILE_ATTRIBUTES) return 0;
    if (a & FILE_ATTRIBUTE_DIRECTORY) return RemoveDirectoryW(wp);   /* empty folders only */
    backup_set_new(set, sizeof set, "delete");
    if (!backup_file(path, set)) return 0;
    if (backup_dir) strcpy(backup_dir, set);
    return DeleteFileW(wp);
}

int fs_mkdir(const char *path)
{
    wchar_t wp[PATHW];
    if (!path_ok(path)) return 0;
    utf8_to_w(path, wp, PATHW);
    return mkdirs_w(wp);
}

int fs_rename(const char *from, const char *to)
{
    wchar_t a[PATHW], b[PATHW];
    if (!path_ok(from) || !path_ok(to)) return 0;
    utf8_to_w(from, a, PATHW); utf8_to_w(to, b, PATHW);
    return MoveFileW(a, b);
}

/* [{"id":..., "why":..., "files":[...]}] newest first (by name) */
int backups_json(char *out, int max)
{
    WIN32_FIND_DATAA fd;
    HANDLE h;
    char pat[MAX_PATH], ids[200][64] = { { 0 } };
    int n = 0, k = 0, i;
    _snprintf(pat, sizeof pat, "%s\\*", A.backup);
    h = FindFirstFileA(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && fd.cFileName[0] != '.' && k < 200)
            strncpy(ids[k++], fd.cFileName, 63);
        while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    for (i = 0; i < k; i++) {                      /* sort descending */
        int j;
        for (j = i + 1; j < k; j++) if (strcmp(ids[j], ids[i]) > 0) { char t[64]; strcpy(t, ids[i]); strcpy(ids[i], ids[j]); strcpy(ids[j], t); }
    }
    n = _snprintf(out, max, "[");
    for (i = 0; i < k && n < max - 2000; i++) {
        char mf[MAX_PATH], line[600];
        FILE *f;
        int nf = 0;
        n += _snprintf(out + n, max - n, "%s{\"id\":\"%s\",\"why\":", i ? "," : "", ids[i]);
        _snprintf(mf, sizeof mf, "%s\\%s\\manifest.txt", A.backup, ids[i]);
        f = fopen(mf, "r");
        if (f && fgets(line, sizeof line, f) && !strncmp(line, "why ", 4)) {
            line[strcspn(line, "\r\n")] = 0;
            n += json_str(out + n, max - n, line + 4);
        } else n += _snprintf(out + n, max - n, "\"\"");
        n += _snprintf(out + n, max - n, ",\"files\":[");
        while (f && fgets(line, sizeof line, f) && n < max - 1000) {
            line[strcspn(line, "\r\n")] = 0;
            if (strncmp(line, "file ", 5)) continue;
            if (nf++) out[n++] = ',';
            n += json_str(out + n, max - n, line + 5);
        }
        if (f) fclose(f);
        n += _snprintf(out + n, max - n, "]}");
    }
    return n + _snprintf(out + n, max - n, "]");
}

/* copy every file of a backup set back to its place (the current versions are backed up first) */
int backup_restore(const char *id, char *report, int max)
{
    char mf[MAX_PATH], line[600], set[MAX_PATH], src[PATHW];
    wchar_t ws[PATHW], wd[PATHW];
    FILE *f;
    int ok = 1, n = 0;
    if (strchr(id, '\\') || strchr(id, '/') || strstr(id, "..")) return 0;
    _snprintf(mf, sizeof mf, "%s\\%s\\manifest.txt", A.backup, id);
    if (!(f = fopen(mf, "r"))) { _snprintf(report, max, "no backup %s", id); return 0; }
    backup_set_new(set, sizeof set, "before restore");
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (strncmp(line, "file ", 5)) continue;
        backup_file(line + 5, set);
        _snprintf(src, sizeof src, "%s\\%s\\files\\%c\\%s", A.backup, id, line[5], line + 8);
        utf8_to_w(src, ws, PATHW); utf8_to_w(line + 5, wd, PATHW);
        if (CopyFileW(ws, wd, FALSE)) n += _snprintf(report + n, max - n, "restored %s\n", line + 5);
        else { ok = 0; n += _snprintf(report + n, max - n, "FAILED %s (error %lu)\n", line + 5, GetLastError()); }
    }
    fclose(f);
    return ok;
}
