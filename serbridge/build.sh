#!/bin/sh
# Build serbridge.exe (32-bit, runs on Windows XP).
# Needs MSYS2's 32-bit gcc:  pacman -S mingw-w64-i686-gcc
set -e
cd "$(dirname "$0")"
export PATH=/c/msys64/mingw32/bin:$PATH
CF="-std=gnu99 -O2 -Wall -Wextra -D_WIN32_WINNT=0x0501 -DWINVER=0x0501 -static -s"
gcc $CF -o serbridge.exe serbridge.c -lws2_32
echo built serbridge.exe
