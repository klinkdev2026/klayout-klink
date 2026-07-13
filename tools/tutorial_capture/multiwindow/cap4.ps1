param([long]$Hwnd,[string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class Cap4{
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
 [DllImport("user32.dll")] public static extern bool RedrawWindow(IntPtr h,IntPtr r,IntPtr u,uint f);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 public struct RECT{public int L,T,R,B;}
}
"@
$h=[IntPtr]$Hwnd
# NO ShowWindow / no resize -- keep whatever size it already is
[void][Cap4]::BringWindowToTop($h)
[void][Cap4]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 350
[void][Cap4]::RedrawWindow($h,[IntPtr]::Zero,[IntPtr]::Zero,0x0481)
Start-Sleep -Milliseconds 450
$r=New-Object Cap4+RECT; [void][Cap4]::GetWindowRect($h,[ref]$r)
$w=$r.R-$r.L; $ht=$r.B-$r.T
$bmp=New-Object System.Drawing.Bitmap $w,$ht
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L,$r.T,0,0,(New-Object System.Drawing.Size $w,$ht))
$g.Dispose()
$bmp.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
"cap4 $Hwnd -> $Out (${w}x${ht})"
