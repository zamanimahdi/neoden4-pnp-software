/* neoagent - on-machine network agent for the NeoDen4 XP PC.
 * HTTP (web UI + JSON API), FTP, signed updates with rollback, process supervisor.
 * Plain C, Win32 + Winsock, 32-bit; runs on Windows XP. */
#ifndef NEOAGENT_H
#define NEOAGENT_H

#include <winsock2.h>
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AGENT_VERSION "1.0"
#define MAX_PROCS 8
#define PATHW 1024

typedef struct {
    char name[32];
    char dir[MAX_PATH];
    char cmd[512];
    int wanted;                 /* supervisor keeps it running */
    HANDLE proc;
    DWORD pid;
    int restarts;
    DWORD started;              /* GetTickCount at start */
    DWORD next_try;
} proc_t;

typedef struct {
    char home[MAX_PATH];        /* folder of neoagent.exe */
    char ini[MAX_PATH];
    char www[MAX_PATH];
    char backup[MAX_PATH];
    char bind[64];
    int http_port, ftp_port;
    char user[64], password[64];
    char update_key[256];
    char ftp_root[MAX_PATH];    /* "" = whole PC (drive list at /) */
    char neoden_dir[MAX_PATH];  /* NeoDen4 data folder (Config.ini, Proc) */
    int cam_port;               /* camserver on localhost */
    int sb_port;                /* serbridge on localhost */
    int nprocs;
    proc_t procs[MAX_PROCS];
    CRITICAL_SECTION lock;      /* procs + log */
    DWORD start_tick;
    volatile int quit;          /* set when an update restarts the agent */
} agent_t;

extern agent_t A;

/* main.c */
void log_msg(const char *fmt, ...);
void log_line(const char *src, const char *text);
int  log_read(unsigned int since, char *out, int max, unsigned int *next);   /* JSON array */
int  proc_find(const char *name);
int  proc_start(int i);
void proc_stop(int i);
int  proc_running(int i);
void procs_json(char *out, int max);
void now_stamp(char *out, int n);     /* YYYYMMDD-HHMMSS */

/* util */
int  utf8_to_w(const char *s, wchar_t *w, int n);
int  w_to_utf8(const wchar_t *w, char *s, int n);
int  json_str(char *out, int max, const char *s);       /* "escaped" */
void url_decode(char *s);
int  query_get(const char *q, const char *key, char *out, int max);
int  path_ok(const char *p);                             /* absolute, no ".." */

/* files.c */
int  fs_list_json(const char *path, char *out, int max);
int  fs_read(const char *path, char **data, long *len);
int  fs_write(const char *path, const char *data, long len, char *backup_dir);   /* backs up old */
int  fs_delete(const char *path, char *backup_dir);
int  fs_mkdir(const char *path);
int  fs_rename(const char *from, const char *to);
int  backup_file(const char *path, const char *set_dir);   /* copy into a backup set */
int  backup_set_new(char *set_dir, int n, const char *why);
int  backups_json(char *out, int max);
int  backup_restore(const char *id, char *report, int max);

/* cam.c */
int  cam_grab_bmp(int cam, int scale, char **bmp, long *len, char *err, int errn);
int  cam_set(int cam, int exposure, int gain, int w, int h, int led, char *err, int errn);
int  cam_info(char *out, int max);

/* http.c */
DWORD WINAPI http_server(LPVOID arg);

/* ftp.c */
DWORD WINAPI ftp_server(LPVOID arg);

/* machine.c (through serbridge on localhost) */
int  machine_status(char *out, int max);
int  machine_lock(const char *client, int release, char *out, int max);
int  machine_cmd(const char *cmd, const char *q, const char *client, char *out, int max, char *err, int errn);
void machine_cancel(void);

/* update.c */
int  update_apply(const char *pkg, long len, char *report, int max);
int  self_update_pending(void);

#endif
