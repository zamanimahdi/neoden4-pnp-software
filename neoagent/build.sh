#!/bin/sh
# Build neoagent.exe (32-bit, runs on Windows XP). Needs MSYS2's 32-bit gcc: pacman -S mingw-w64-i686-gcc
set -e
cd "$(dirname "$0")"
export PATH=/c/msys64/mingw32/bin:$PATH
gcc -std=gnu99 -O2 -Wall -Wextra -Wno-unused-parameter -Wno-stringop-truncation -D_WIN32_WINNT=0x0501 -DWINVER=0x0501 -static -s \
    -o neoagent.exe main.c http.c files.c cam.c machine.c update.c ftp.c sha256.c -lws2_32
echo built neoagent.exe
