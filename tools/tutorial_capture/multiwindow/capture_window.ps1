<#
capture_window.ps1 -- find ONE visible KLayout window belonging to a given
OS process id, verify it is maximized to the exact size the multiwindow
tutorial's annotation overlay (annotate3.ps1) was calibrated against, and
capture it via cap4.ps1 (foreground + CopyFromScreen -- PrintWindow returns
a stale cached frame for an occluded Qt GL canvas, so that is the only
reliable capture method here).

This is the generic single-shot sibling of gf_ports/capture_send_window.ps1:
that script also runs the annotate+crop pipeline inline for a single figure;
this one only finds-and-captures, because draw_multiwindow_tutorial.py needs
to interleave FOUR captures (two windows, two states each) with live RPC
calls in between, then run annotate3.ps1 twice (CN, EN) at the end over all
four raw captures at once.

Window selection is by OS PID, not title/size alone: this dev machine
routinely has several KLayout windows open at once (multiple klink
sessions/ports), often at the exact same maximized size, so PID is the only
reliable discriminator (get it with
`client.exec_python("import os\\nos.getpid()")` against the live RPC port
being captured).

Preconditions: the target window must be visible (not minimized) and
maximized to EXACTLY -ExpectedWidth x -ExpectedHeight (default 1550x838 --
the size annotate3.ps1's pixel coordinates are calibrated against). Refuses
to proceed (no output file, nonzero exit) rather than emit a silently
misaligned capture.

Usage:
    powershell -File capture_window.ps1 -TargetPid <pid> -Out <path> `
        [-ExpectedWidth 1550] [-ExpectedHeight 838]
#>
param(
    [Parameter(Mandatory = $true)][int]$TargetPid,
    [Parameter(Mandatory = $true)][string]$Out,
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
public class MwWinEnum {
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
    if ([MwWinEnum]::IsWindowVisible($hWnd)) {
        $len = [MwWinEnum]::GetWindowTextLength($hWnd)
        if ($len -gt 0) {
            $sb = New-Object System.Text.StringBuilder ($len + 1)
            [void][MwWinEnum]::GetWindowText($hWnd, $sb, $sb.Capacity)
            $title = $sb.ToString()
            if ($title -match "KLayout") {
                $r = New-Object MwWinEnum+RECT
                [void][MwWinEnum]::GetWindowRect($hWnd, [ref]$r)
                $procId = [uint32]0
                [void][MwWinEnum]::GetWindowThreadProcessId($hWnd, [ref]$procId)
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
[void][MwWinEnum]::EnumWindows($callback, [IntPtr]::Zero)

if ($found.Count -eq 0) {
    Write-Error "No visible window with 'KLayout' in its title was found. Start KLayout with the klink plugin loaded and make sure its window is visible (not minimized), then retry."
    exit 1
}

$candidates = $found | Where-Object { $_.Pid -eq $TargetPid }
if (-not $candidates) {
    $seen = ($found | ForEach-Object { "'$($_.Title)' pid=$($_.Pid)" }) -join "; "
    Write-Error "No visible KLayout window belongs to process id ${TargetPid} (the process behind the klink RPC port being captured). Windows found: $seen. Is that KLayout process's window minimized or on another desktop?"
    exit 1
}

$match = $candidates | Where-Object { $_.W -eq $ExpectedWidth -and $_.H -eq $ExpectedHeight } | Select-Object -First 1
if (-not $match) {
    $sizes = ($candidates | ForEach-Object { "'$($_.Title)' $($_.W)x$($_.H)" }) -join "; "
    Write-Error "Found candidate KLayout window(s) for pid ${TargetPid} but none are exactly ${ExpectedWidth}x${ExpectedHeight}: $sizes. Maximize that KLayout window to exactly ${ExpectedWidth}x${ExpectedHeight} and retry -- the multiwindow tutorial's annotation overlay (annotate3.ps1) is pixel coordinates calibrated to that exact size, so this script refuses to guess at a different size."
    exit 1
}

$outDir = Split-Path -Parent $Out
if ($outDir -and -not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

# Primary: cap4.ps1 (foreground + CopyFromScreen) -- the only method that is
# guaranteed fresh on an ATTACHED desktop (PrintWindow serves stale cached
# frames for an occluded Qt GL canvas there). Fallback: cap_pw.ps1
# (RedrawWindow + PrintWindow PW_RENDERFULLCONTENT) -- CopyFromScreen throws
# "The handle is invalid" in a disconnected RDP session (no screen surface),
# while PrintWindow after a forced redraw is fresh in that state; see
# cap_pw.ps1's header. In fallback mode, eyeball the output for staleness.
$capturedVia = "cap4"
try {
    & (Join-Path $ScriptDir "cap4.ps1") -Hwnd $match.Hwnd -Out $Out
} catch {
    Write-Output "cap4.ps1 (CopyFromScreen) failed: $($_.Exception.Message)"
    Write-Output "falling back to cap_pw.ps1 (PrintWindow) -- disconnected-session mode"
    $capturedVia = "cap_pw (PrintWindow fallback -- verify freshness by eye)"
    & (Join-Path $ScriptDir "cap_pw.ps1") -Hwnd $match.Hwnd -Out $Out
}
if (-not (Test-Path $Out)) {
    Write-Error "neither cap4.ps1 nor cap_pw.ps1 produced $Out"
    exit 1
}

Write-Output "captured pid=$TargetPid '$($match.Title)' ($($match.W)x$($match.H)) via $capturedVia -> $Out"
