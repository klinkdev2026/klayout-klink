<#
capture_send_window.ps1 -- orchestrates the gf-7-send.png / gf-7-send-en.png
full-window screenshot: find the live KLayout window, verify it is
maximized to the EXACT size the annotation overlay's pixel coordinates
were calibrated against, capture it, annotate CN + EN, crop to the
toolbar+sidebar band.

Chains three already-verified helpers shipped alongside this script
(kept byte-for-byte as captured, do not "simplify" their internals without
re-verifying the annotation coordinates against a real window):
    cap4.ps1    -- foreground + CopyFromScreen raw window capture
                   (PrintWindow returns a stale cached frame for an
                   occluded Qt GL canvas, so this is the only reliable way)
    gfsend.ps1  -- draws the SEND red-box + arrow + CN/EN callout chip on
                   top of a raw capture (reads its JSON label file for text)
    crop.ps1    -- crops to the top band (toolbar + cell-tree sidebar)

Window selection: this dev machine routinely has SEVERAL KLayout windows
open at once (multiple klink sessions/ports), often at the SAME maximized
size, so size alone cannot disambiguate them. -TargetPid (the KLayout
process id backing the klink RPC port the caller is driving -- get it with
`client.exec_python("import os\\nos.getpid()")`) is the reliable
discriminator; when given, only windows belonging to that exact process are
considered. Without -TargetPid this falls back to "first KLayout-titled
window of the right size", which is fine for a lone manual run but is NOT
what draw_gf_ports_tutorial.py does (it always passes -TargetPid).

Preconditions (see README.md):
    - The target KLayout window is visible on THIS Windows session,
      maximized to EXACTLY 1550x838 px. The SEND button box / arrow /
      callout coordinates baked into gfsend.ps1 are pixel coordinates
      calibrated against that exact window size -- a different size will
      misplace the annotation, so this script refuses to proceed (no
      output file) rather than emit a silently-wrong image.

Usage:
    powershell -File capture_send_window.ps1 -OutDir <dir> [-TargetPid <pid>]

Writes <OutDir>/gf-7-send.png and <OutDir>/gf-7-send-en.png. Exits nonzero
and writes NOTHING if no matching window is found.
#>
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$TargetPid = 0,
    [int]$ExpectedWidth = 1550,
    [int]$ExpectedHeight = 838
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class SendWinEnum {
    public struct RECT { public int L, T, R, B; }
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
"@

$found = New-Object System.Collections.Generic.List[object]
$callback = {
    param([IntPtr]$hWnd, [IntPtr]$lParam)
    if ([SendWinEnum]::IsWindowVisible($hWnd)) {
        $len = [SendWinEnum]::GetWindowTextLength($hWnd)
        if ($len -gt 0) {
            $sb = New-Object System.Text.StringBuilder ($len + 1)
            [void][SendWinEnum]::GetWindowText($hWnd, $sb, $sb.Capacity)
            $title = $sb.ToString()
            if ($title -match "KLayout") {
                $r = New-Object SendWinEnum+RECT
                [void][SendWinEnum]::GetWindowRect($hWnd, [ref]$r)
                $procId = [uint32]0
                [void][SendWinEnum]::GetWindowThreadProcessId($hWnd, [ref]$procId)
                $found.Add([PSCustomObject]@{
                    Hwnd  = $hWnd
                    Title = $title
                    W     = ($r.R - $r.L)
                    H     = ($r.B - $r.T)
                    Pid   = [int]$procId
                })
            }
        }
    }
    return $true
}
[void][SendWinEnum]::EnumWindows($callback, [IntPtr]::Zero)

if ($found.Count -eq 0) {
    Write-Error "No visible window with 'KLayout' in its title was found. Start KLayout with the klink plugin loaded and make sure its window is visible (not minimized), then retry."
    exit 1
}

$candidates = $found
if ($TargetPid -gt 0) {
    $candidates = $found | Where-Object { $_.Pid -eq $TargetPid }
    if (-not $candidates) {
        $seen = ($found | ForEach-Object { "'$($_.Title)' pid=$($_.Pid)" }) -join "; "
        Write-Error "No visible KLayout window belongs to process id ${TargetPid} (the process behind the klink RPC port being captured). Windows found: $seen. Is that KLayout process's window minimized or on another desktop?"
        exit 1
    }
}

$match = $candidates | Where-Object { $_.W -eq $ExpectedWidth -and $_.H -eq $ExpectedHeight } | Select-Object -First 1
if (-not $match) {
    $sizes = ($candidates | ForEach-Object { "'$($_.Title)' ${($_.W)}x${($_.H)}" }) -join "; "
    Write-Error "Found candidate KLayout window(s) but none are exactly ${ExpectedWidth}x${ExpectedHeight}: $sizes. Maximize the KLayout window to exactly ${ExpectedWidth}x${ExpectedHeight} and retry -- gfsend.ps1's SEND-button box/arrow/callout are pixel coordinates calibrated to that exact size, so this script refuses to guess at a different size."
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# gfsend.ps1 hardcodes its SOURCE image as "$PSScriptRoot\gf-7-send.png" --
# stage the raw capture there (this directory), then delete it once the
# final crops are written to -OutDir.
$rawStage = Join-Path $ScriptDir "gf-7-send.png"
& (Join-Path $ScriptDir "cap4.ps1") -Hwnd $match.Hwnd -Out $rawStage
if (-not (Test-Path $rawStage)) {
    Write-Error "cap4.ps1 did not produce $rawStage"
    exit 1
}

$tmpCn = Join-Path $env:TEMP "gf-7-send-cn-annotated.png"
$tmpEn = Join-Path $env:TEMP "gf-7-send-en-annotated.png"
try {
    & (Join-Path $ScriptDir "gfsend.ps1") -JsonPath (Join-Path $ScriptDir "send-cn.json") -Out $tmpCn
    & (Join-Path $ScriptDir "gfsend.ps1") -JsonPath (Join-Path $ScriptDir "send-en.json") -Out $tmpEn

    & (Join-Path $ScriptDir "crop.ps1") -In $tmpCn -Out (Join-Path $OutDir "gf-7-send.png") -H 285
    & (Join-Path $ScriptDir "crop.ps1") -In $tmpEn -Out (Join-Path $OutDir "gf-7-send-en.png") -H 285
}
finally {
    Remove-Item $rawStage, $tmpCn, $tmpEn -Force -ErrorAction SilentlyContinue
}

Write-Output "wrote $(Join-Path $OutDir 'gf-7-send.png') and $(Join-Path $OutDir 'gf-7-send-en.png') from window '$($match.Title)' ($($match.W)x$($match.H))"
