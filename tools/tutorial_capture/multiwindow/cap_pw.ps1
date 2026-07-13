<#
cap_pw.ps1 -- PrintWindow(PW_RENDERFULLCONTENT) capture, the DISCONNECTED-
SESSION fallback to cap4.ps1.

cap4.ps1 (foreground + CopyFromScreen) is the primary method on an attached
desktop, because PrintWindow returns a stale cached frame for an OCCLUDED
Qt GL canvas there. But CopyFromScreen needs a real screen surface: in a
disconnected RDP session it throws "The handle is invalid" -- there is no
desktop to copy from. In that state PrintWindow after a forced
RedrawWindow(RDW_INVALIDATE|RDW_ALLCHILDREN|RDW_UPDATENOW|RDW_FRAME) DOES
return a fresh frame (verified live 2026-07: draw via RPC -> capture ->
the new geometry is in the PNG), because there is no occlusion/composition
path to serve a cached frame from.

capture_window.ps1 tries cap4.ps1 first and falls back to this script only
when cap4 fails; whoever runs the pipeline in fallback mode must eyeball
the output PNGs for staleness (the tutorial capture README says how).
#>
param([long]$Hwnd,[string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class CapPw{
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr hdc,uint f);
 [DllImport("user32.dll")] public static extern bool RedrawWindow(IntPtr h,IntPtr r,IntPtr u,uint f);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 public struct RECT{public int L,T,R,B;}
}
"@
$h=[IntPtr]$Hwnd
[void][CapPw]::RedrawWindow($h,[IntPtr]::Zero,[IntPtr]::Zero,0x0481)
Start-Sleep -Milliseconds 500
$r=New-Object CapPw+RECT; [void][CapPw]::GetWindowRect($h,[ref]$r)
$w=$r.R-$r.L; $ht=$r.B-$r.T
$bmp=New-Object System.Drawing.Bitmap $w,$ht
$g=[System.Drawing.Graphics]::FromImage($bmp)
$hdc=$g.GetHdc()
$ok=[CapPw]::PrintWindow($h,$hdc,2)   # 2 = PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
$g.Dispose()
if (-not $ok) { $bmp.Dispose(); Write-Error "PrintWindow returned false for hwnd $Hwnd"; exit 1 }
$bmp.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
"cap_pw $Hwnd -> $Out (${w}x${ht})"
