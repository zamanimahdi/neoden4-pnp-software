#!/bin/sh
# Build camserver.exe (32-bit, runs on Windows XP) and the fake camera DLLs for the test.
# Needs MSYS2's 32-bit gcc:  pacman -S mingw-w64-i686-gcc
set -e
cd "$(dirname "$0")"
export PATH=/c/msys64/mingw32/bin:$PATH
CF="-std=gnu99 -O2 -Wall -Wextra -D_WIN32_WINNT=0x0501 -DWINVER=0x0501 -static -s"
gcc $CF -o camserver.exe camserver.c -lws2_32
mkdir -p test/build/new test/build/old
gcc $CF -shared -DNEW -o test/build/new/NeodenCamera.dll test/fakecam.c
gcc $CF -shared -DOLD -o test/build/old/imgdll.dll test/fakecam.c
echo built camserver.exe
