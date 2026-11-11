# Writes neoden-ubuntu.img to a disk chosen by its SERIAL NUMBER (never by its number, which
# changes when disks are plugged in or out), then reads it back and compares.
# Run in PowerShell opened with "Run as administrator":
#   & ".\build\image\write-image.ps1" -Serial <disk serial>
param(
    [Parameter(Mandatory = $true)][string]$Serial,
    [string]$Image = (Join-Path $PSScriptRoot "neoden-ubuntu.img")
)
$ErrorActionPreference = "Stop"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this in PowerShell opened with 'Run as administrator'."
}
$img = Get-Item $Image
$disks = @(Get-Disk | Where-Object { $_.SerialNumber.Trim() -eq $Serial.Trim() })
if ($disks.Count -ne 1) { throw "Found $($disks.Count) disks with serial '$Serial' (need exactly 1). Nothing written." }
$d = $disks[0]
$letters = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | Where-Object DriveLetter | ForEach-Object { "$($_.DriveLetter):" })
if ($d.IsSystem -or $d.IsBoot) { throw "Disk $($d.Number) is the Windows system/boot disk. Nothing written." }
if ($letters | Where-Object { $_ -in @("C:", "D:", "E:", "F:", "G:") }) { throw "Disk $($d.Number) holds $($letters -join ' '). Nothing written." }
if ($d.Size -lt $img.Length) { throw "Disk is smaller ($($d.Size)) than the image ($($img.Length)). Nothing written." }

Write-Host ""
Write-Host "Target disk:  number $($d.Number)  $($d.FriendlyName)  serial $($d.SerialNumber)"
Write-Host "              $([math]::Round($d.Size/1e9,1)) GB  $($d.BusType)  partitions $($d.NumberOfPartitions)  letters: $($letters -join ' ')"
Write-Host "Image:        $($img.FullName)  $([math]::Round($img.Length/1e9,2)) GB"
Write-Host ""
Write-Host "EVERYTHING ON THIS DISK WILL BE LOST." -ForegroundColor Red
$a = Read-Host "Type the disk's serial number again to write"
if ($a.Trim() -ne $d.SerialNumber.Trim()) { throw "Serial did not match. Nothing written." }

# Windows refuses raw writes into sectors that belong to a partition ("Access denied"), so the
# disk's partitions are removed first (it is being overwritten anyway)
Set-Disk -Number $d.Number -IsReadOnly $false
if ($d.PartitionStyle -ne "RAW" -or $d.NumberOfPartitions -gt 0) {
    if ($d.IsOffline) { Set-Disk -Number $d.Number -IsOffline $false }
    Write-Host "Removing the old partitions of disk $($d.Number)..."
    Clear-Disk -Number $d.Number -RemoveData -RemoveOEM -Confirm:$false
}
$d = Get-Disk -Number $d.Number
if ($d.SerialNumber.Trim() -ne $Serial.Trim()) { throw "The disk number changed meanwhile. Stopped." }
# raw writes need the disk ONLINE (an offline disk refuses them too); with no partitions left
# Windows has no volume on it to protect
if ($d.IsOffline) { Set-Disk -Number $d.Number -IsOffline $false }

$buf = New-Object byte[] (4MB)
$src = [IO.File]::OpenRead($img.FullName)
$dst = New-Object IO.FileStream("\\.\PhysicalDrive$($d.Number)", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite, 4MB, [IO.FileOptions]::WriteThrough)
$sha = [Security.Cryptography.SHA256]::Create()
[long]$done = 0; $t0 = Get-Date
try {
    while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
        if ($n % 512) { $n += 512 - ($n % 512) }          # whole sectors
        $dst.Write($buf, 0, $n)
        [void]$sha.TransformBlock($buf, 0, [int][math]::Min([long]$n, [long]($img.Length - $done)), $null, 0)
        $done += $n
        $p = [math]::Min(100, 100 * $done / $img.Length)
        Write-Progress -Activity "Writing" -Status ("{0:N1} %  {1:N0} MB/s" -f $p, ($done / 1MB / ((Get-Date) - $t0).TotalSeconds)) -PercentComplete $p
    }
    $dst.Flush()
} finally { $src.Close(); $dst.Close() }
[void]$sha.TransformFinalBlock($buf, 0, 0)
$h1 = [BitConverter]::ToString($sha.Hash) -replace "-", ""
Write-Host "Written in $([int]((Get-Date) - $t0).TotalSeconds) s. Reading back to compare..."

$sha2 = [Security.Cryptography.SHA256]::Create()
$rd = New-Object IO.FileStream("\\.\PhysicalDrive$($d.Number)", [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite, 4MB)
[long]$left = $img.Length
try {
    while ($left -gt 0) {
        $n = $rd.Read($buf, 0, $buf.Length)
        if ($n -le 0) { break }
        $use = [int][math]::Min([long]$n, $left)
        [void]$sha2.TransformBlock($buf, 0, $use, $null, 0)
        $left -= $use
        Write-Progress -Activity "Verifying" -PercentComplete (100 - 100 * $left / $img.Length)
    }
} finally { $rd.Close() }
[void]$sha2.TransformFinalBlock($buf, 0, 0)
$h2 = [BitConverter]::ToString($sha2.Hash) -replace "-", ""
Write-Host "image sha256: $h1"
Write-Host "disk  sha256: $h2"
if ($h1 -eq $h2) { Write-Host "OK: the disk matches the image. Remove it and put it in the machine." -ForegroundColor Green }
else { Write-Host "MISMATCH: do not use this disk; write it again." -ForegroundColor Red; exit 1 }
