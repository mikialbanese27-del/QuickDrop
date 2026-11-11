Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class QuickDropMouse {
 [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vKey);
}
'@

$ErrorActionPreference = 'Stop'
$endpoint = 'http://127.0.0.1:' + $env:QUICKDROP_PORT + '/queue-local'
$token = $env:QUICKDROP_TOKEN

$form = New-Object System.Windows.Forms.Form
$form.Text = 'QuickDrop'
$form.Width = 240
$form.Height = 110
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$work = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.Left = [int]($work.Right - $form.Width - 30)
$form.Top = [int]($work.Bottom - $form.Height - 90)
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.AllowDrop = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(24, 48, 70)

$label = New-Object System.Windows.Forms.Label
$label.Text = 'QUICKDROP' + [Environment]::NewLine + 'Rilascia qui per inviare al telefono'
$label.ForeColor = [System.Drawing.Color]::White
$label.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
$label.Dock = [System.Windows.Forms.DockStyle]::Fill
$label.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$label.AllowDrop = $true
$form.Controls.Add($label)

$dragEnter = {
 param($sender, $e)
 if ($e.Data.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop)) {
   $e.Effect = [System.Windows.Forms.DragDropEffects]::Copy
 } else { $e.Effect = [System.Windows.Forms.DragDropEffects]::None }
}
$dragDrop = {
 param($sender, $e)
 try {
   $paths = @($e.Data.GetData([System.Windows.Forms.DataFormats]::FileDrop))
   if ($paths.Count -gt 0) {
     $payload = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @{paths=$paths} -Compress -Depth 3))
     $req = [Net.HttpWebRequest]::Create($endpoint)
     $req.Method = 'POST'
     $req.Timeout = 4500
     $req.ContentType = 'application/json; charset=utf-8'
     $req.Headers.Add('X-QuickDrop-Token', $token)
     $req.ContentLength = $payload.Length
     $stream = $req.GetRequestStream()
     $stream.Write($payload, 0, $payload.Length)
     $stream.Dispose()
     $resp = $req.GetResponse()
     $resp.Dispose()
   }
 } catch {
   [System.Windows.Forms.MessageBox]::Show('Invio non riuscito: ' + $_.Exception.Message, 'QuickDrop') | Out-Null
 }
 $script:dropTime = [DateTime]::UtcNow
 $form.Hide()
}
$form.add_DragEnter($dragEnter)
$form.add_DragDrop($dragDrop)
$label.add_DragEnter($dragEnter)
$label.add_DragDrop($dragDrop)

$script:pressed = $false
$script:mouseStart = [System.Windows.Forms.Cursor]::Position
$script:dropTime = [DateTime]::MinValue
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 80
$timer.Add_Tick({
  $down = (([QuickDropMouse]::GetAsyncKeyState(1) -band 0x8000) -ne 0)
  $point = [System.Windows.Forms.Cursor]::Position
  if ($down -and -not $script:pressed) {
    $script:pressed = $true
    $script:mouseStart = $point
  }
  if ($down -and $script:pressed -and -not $form.Visible) {
    $dx = [Math]::Abs($point.X - $script:mouseStart.X)
    $dy = [Math]::Abs($point.Y - $script:mouseStart.Y)
    if (($dx + $dy) -gt 24 -and ([DateTime]::UtcNow - $script:dropTime).TotalMilliseconds -gt 700) {
      $form.Show()
      $form.BringToFront()
    }
  }
  if (-not $down) {
    $script:pressed = $false
    if ($form.Visible) { $form.Hide() }
  }
})
$form.Add_Shown({$form.Hide()})
$timer.Start()
[System.Windows.Forms.Application]::Run($form)
