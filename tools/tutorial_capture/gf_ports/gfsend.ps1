param([string]$JsonPath,[string]$Out)
Add-Type -AssemblyName System.Drawing
$L=Get-Content $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$RED=[System.Drawing.Color]::FromArgb(229,50,45); $DARK=[System.Drawing.Color]::FromArgb(13,31,26)
$TEAL=[System.Drawing.Color]::FromArgb(61,219,180); $WHITE=[System.Drawing.Color]::White
$img=[System.Drawing.Image]::FromFile("$($PSScriptRoot)\gf-7-send.png")
$b=New-Object System.Drawing.Bitmap $img.Width,$img.Height
$g=[System.Drawing.Graphics]::FromImage($b); $g.SmoothingMode='AntiAlias'; $g.TextRenderingHint='ClearTypeGridFit'
$g.DrawImage($img,0,0,$img.Width,$img.Height); $img.Dispose()
function Chip($x,$y,$t,$bg,$fg,$sz){ $f=New-Object System.Drawing.Font "Segoe UI",$sz,([System.Drawing.FontStyle]::Bold)
  $m=$g.MeasureString($t,$f); $w=[int]$m.Width+20; $h=[int]$m.Height+10; $r=8
  $p=New-Object System.Drawing.Drawing2D.GraphicsPath
  $p.AddArc($x,$y,$r,$r,180,90);$p.AddArc(($x+$w-$r),$y,$r,$r,270,90);$p.AddArc(($x+$w-$r),($y+$h-$r),$r,$r,0,90);$p.AddArc($x,($y+$h-$r),$r,$r,90,90);$p.CloseFigure()
  $g.FillPath((New-Object System.Drawing.SolidBrush $bg),$p)
  $g.DrawString($t,$f,(New-Object System.Drawing.SolidBrush $fg),($x+10),($y+5)) }
$pen=New-Object System.Drawing.Pen $RED,3; $g.DrawRectangle($pen,797,80,40,21)
$ap=New-Object System.Drawing.Pen $RED,2; $ap.CustomEndCap=New-Object System.Drawing.Drawing2D.AdjustableArrowCap 5,5
$g.DrawLine($ap,817,101,520,175)
Chip 470 168 $L.title $RED $WHITE 13
Chip 470 213 $L.detail $DARK $TEAL 12
$g.Dispose(); $b.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png); $b.Dispose(); "saved $Out"
