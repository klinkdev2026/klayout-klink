param([string]$JsonPath,[string]$OutDir,[string]$Raw)
Add-Type -AssemblyName System.Drawing
$RED=[System.Drawing.Color]::FromArgb(229,50,45)
$TEAL=[System.Drawing.Color]::FromArgb(61,219,180)
$DARK=[System.Drawing.Color]::FromArgb(13,31,26)
$WHITE=[System.Drawing.Color]::White
New-Item -ItemType Directory -Force $OutDir | Out-Null
$L = Get-Content $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json

function New-Canvas($p){ $img=[System.Drawing.Image]::FromFile($p)
  $b=New-Object System.Drawing.Bitmap $img.Width,$img.Height
  $g=[System.Drawing.Graphics]::FromImage($b)
  $g.SmoothingMode='AntiAlias'; $g.TextRenderingHint='ClearTypeGridFit'
  $g.DrawImage($img,0,0,$img.Width,$img.Height); $img.Dispose(); return @{b=$b;g=$g} }
function Box($g,$x,$y,$w,$h,$c,$wd){ $p=New-Object System.Drawing.Pen $c,$wd; $g.DrawRectangle($p,$x,$y,$w,$h); $p.Dispose() }
function Arrow($g,$x1,$y1,$x2,$y2,$c,$wd){ $p=New-Object System.Drawing.Pen $c,$wd
  $p.CustomEndCap=New-Object System.Drawing.Drawing2D.AdjustableArrowCap 5,5; $g.DrawLine($p,$x1,$y1,$x2,$y2); $p.Dispose() }
function Chip($g,$x,$y,$t,$bg,$fg,$sz){ $f=New-Object System.Drawing.Font "Segoe UI",$sz,([System.Drawing.FontStyle]::Bold)
  $m=$g.MeasureString($t,$f); $px=10;$py=5; $w=[int]$m.Width+2*$px; $h=[int]$m.Height+2*$py; $r=8
  $path=New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddArc($x,$y,$r,$r,180,90); $path.AddArc(($x+$w-$r),$y,$r,$r,270,90)
  $path.AddArc(($x+$w-$r),($y+$h-$r),$r,$r,0,90); $path.AddArc($x,($y+$h-$r),$r,$r,90,90); $path.CloseFigure()
  $bb=New-Object System.Drawing.SolidBrush $bg; $g.FillPath($bb,$path)
  $fb=New-Object System.Drawing.SolidBrush $fg; $g.DrawString($t,$f,$fb,($x+$px),($y+$py))
  $bb.Dispose();$fb.Dispose();$f.Dispose();$path.Dispose() }
function Save($ctx,$o){ $ctx.g.Dispose(); $ctx.b.Save($o,[System.Drawing.Imaging.ImageFormat]::Png); $ctx.b.Dispose(); "  saved $o" }

# step-1 toolbar (src-clean)
$c=New-Canvas "$Raw\src-clean.png"; $g=$c.g
Box $g 723 80 66 21 $RED 3; Box $g 797 80 40 21 $RED 3; Box $g 839 80 50 21 $RED 3; Box $g 889 80 32 21 $RED 3
$lx=560
Chip $g $lx 175 $L.t_k $DARK $TEAL 12;    Arrow $g 756 101 ($lx+20) 175 $RED 2
Chip $g $lx 215 $L.t_send $DARK $WHITE 12; Arrow $g 817 101 ($lx+20) 215 $RED 2
Chip $g $lx 255 $L.t_gftgt $DARK $WHITE 12;Arrow $g 864 101 ($lx+20) 255 $RED 2
Chip $g $lx 295 $L.t_rec $DARK $WHITE 12;  Arrow $g 905 101 ($lx+20) 295 $RED 2
Chip $g 455 128 $L.t_title $RED $WHITE 12
Save $c "$OutDir\step-1-toolbar.png"

# step-3 send (src-send)
$c=New-Canvas "$Raw\src-send.png"; $g=$c.g
Box $g 797 80 40 21 $RED 3; Box $g 518 424 782 154 $TEAL 3
Chip $g 560 175 $L.s_title $RED $WHITE 13
Chip $g 560 220 $L.s_detail $DARK $WHITE 12; Arrow $g 817 101 640 220 $RED 2
Chip $g 545 590 $L.s_sel $TEAL $DARK 12
Save $c "$OutDir\step-3-send.png"

# step-4 gftgt (dst-empty)
$c=New-Canvas "$Raw\dst-empty.png"; $g=$c.g
Box $g 839 80 50 21 $RED 3
Chip $g 470 200 $L.g_title $RED $WHITE 13
Chip $g 470 245 $L.g_detail $DARK $TEAL 12; Arrow $g 864 101 490 245 $RED 2
Save $c "$OutDir\step-4-gftgt.png"

# step-5 before (dst-empty)
$c=New-Canvas "$Raw\dst-empty.png"; $g=$c.g
Chip $g 470 210 $L.b_before $RED $WHITE 13
Save $c "$OutDir\step-5-transfer-before.png"

# step-6 after (dst-after)
$c=New-Canvas "$Raw\dst-after.png"; $g=$c.g
Box $g 538 402 688 136 $TEAL 3
Chip $g 455 200 $L.a_title $RED $WHITE 13
Chip $g 455 245 $L.a_detail $DARK $TEAL 12; Arrow $g 740 300 750 398 $TEAL 2
Save $c "$OutDir\step-6-transfer-after.png"

# step-2 montage (src-clean + dst-empty)
$img1=[System.Drawing.Image]::FromFile("$Raw\src-clean.png")
$img2=[System.Drawing.Image]::FromFile("$Raw\dst-empty.png")
$sw=758; $sh=[int](838.0*$sw/1550.0)
$bmp=New-Object System.Drawing.Bitmap 1544,($sh+70)
$g=[System.Drawing.Graphics]::FromImage($bmp); $g.SmoothingMode='AntiAlias'; $g.TextRenderingHint='ClearTypeGridFit'
$g.Clear([System.Drawing.Color]::FromArgb(240,242,241))
$g.DrawImage($img1,8,62,$sw,$sh); $g.DrawImage($img2,778,62,$sw,$sh); $img1.Dispose();$img2.Dispose()
$f=$sw/1550.0; $bx1=[int](720*$f)+8; $by=[int](82*$f)+62; $bw=[int](70*$f); $bh=[int](22*$f)+6
$pen=New-Object System.Drawing.Pen $RED,3
$g.DrawEllipse($pen,($bx1-6),($by-4),($bw+14),($bh+6)); $bx2=[int](720*$f)+778
$g.DrawEllipse($pen,($bx2-6),($by-4),($bw+14),($bh+6)); $pen.Dispose()
Chip $g 380 ($by+$bh+2) 'K8765' $RED $WHITE 11
Chip $g 1150 ($by+$bh+2) 'K8767' $RED $WHITE 11
Chip $g 300 14 $L.banner $DARK $TEAL 13
$g.Dispose(); $bmp.Save("$OutDir\step-2-two-windows.png",[System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
"  saved $OutDir\step-2-two-windows.png"
"done"
