param([string]$In,[string]$Out,[int]$H)
Add-Type -AssemblyName System.Drawing
$img=[System.Drawing.Image]::FromFile($In)
$c=New-Object System.Drawing.Bitmap $img.Width,$H
$g=[System.Drawing.Graphics]::FromImage($c)
$g.DrawImage($img,(New-Object System.Drawing.Rectangle 0,0,$img.Width,$H),(New-Object System.Drawing.Rectangle 0,0,$img.Width,$H),[System.Drawing.GraphicsUnit]::Pixel)
$g.Dispose(); $img.Dispose(); $c.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png); $c.Dispose(); "cropped $Out"
