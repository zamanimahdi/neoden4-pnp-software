# Finds out why Windows refuses to write to a disk. Run as administrator:
#   & ".\build\image\disk-write-test.ps1" -Serial <disk serial>
# It writes ONE sector of zeros at the start of that disk (the disk is to be overwritten anyway),
# with the Win32 calls directly, and prints Windows' own error code for each step.
param([Parameter(Mandatory = $true)][string]$Serial)
$ErrorActionPreference = "Stop"
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Raw {
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFile(string n, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr t);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool DeviceIoControl(IntPtr h, uint code, IntPtr inB, uint inS, IntPtr outB, uint outS, out uint ret, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] b, uint n, out uint w, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
}
"@
$d = @(Get-Disk | Where-Object { $_.SerialNumber.Trim() -eq $Serial.Trim() })
if ($d.Count -ne 1) { throw "need exactly one disk with serial $Serial" }
$d = $d[0]
if ($d.IsSystem -or $d.IsBoot -or $d.NumberOfPartitions -gt 0) { throw "disk $($d.Number) is not an empty data disk - stopped" }
Write-Host "disk $($d.Number): $($d.FriendlyName)  offline=$($d.IsOffline) readonly=$($d.IsReadOnly) style=$($d.PartitionStyle)"
Write-Host "WriteProtect policy: $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\StorageDevicePolicies' -ErrorAction SilentlyContinue).WriteProtect)"
$h = [Raw]::CreateFile("\\.\PhysicalDrive$($d.Number)", [uint32]3221225472, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)   # GENERIC_READ | GENERIC_WRITE
if ($h -eq [IntPtr](-1)) { Write-Host "open for write: FAILED, error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"; return }
Write-Host "open for write: OK"
$r = 0
$ok = [Raw]::DeviceIoControl($h, 0x00070024, [IntPtr]::Zero, 0, [IntPtr]::Zero, 0, [ref]$r, [IntPtr]::Zero)   # IOCTL_DISK_IS_WRITABLE
Write-Host ("disk is writable (IOCTL): {0}  error {1}" -f $ok, $(if ($ok) { 0 } else { [Runtime.InteropServices.Marshal]::GetLastWin32Error() }))
$buf = New-Object byte[] 512
$w = 0
$ok = [Raw]::WriteFile($h, $buf, 512, [ref]$w, [IntPtr]::Zero)
Write-Host ("write sector 0: {0}  bytes {1}  error {2}" -f $ok, $w, $(if ($ok) { 0 } else { [Runtime.InteropServices.Marshal]::GetLastWin32Error() }))
[void][Raw]::CloseHandle($h)
Write-Host "error codes: 5 = access denied (policy / security software), 19 = disk write-protected, 21 = not ready"
