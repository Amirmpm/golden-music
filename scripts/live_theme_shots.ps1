Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Drawing.Imaging;
using System.Threading;
public class Cap {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
  public struct R { public int L, T, Rt, B; }

  public static R Rect(IntPtr h) { R r; GetWindowRect(h, out r); return r; }

  public static void Grab(IntPtr h, string path) {
    ShowWindow(h, 9); SetForegroundWindow(h);
    Thread.Sleep(600);
    R r = Rect(h);
    int w = r.Rt - r.L, ht = r.B - r.T;
    using (var bmp = new Bitmap(w, ht))
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc();
      PrintWindow(h, dc, 2);
      g.ReleaseHdc(dc);
      bmp.Save(path, ImageFormat.Png);
    }
  }

  public static void Click(IntPtr h, int cx, int cy) {
    SetForegroundWindow(h);
    Thread.Sleep(350);
    R r = Rect(h);
    SetCursorPos(r.L + cx, r.T + cy);
    Thread.Sleep(220);
    mouse_event(2, 0, 0, 0, (UIntPtr)0); // down
    Thread.Sleep(90);
    mouse_event(4, 0, 0, 0, (UIntPtr)0); // up
  }
}
'@
$themes = @(
  @{n='royal_gold';    x=339; y=169},
  @{n='midnight_blue'; x=459; y=169},
  @{n='amethyst';      x=579; y=169},
  @{n='neon_rose';     x=699; y=169},
  @{n='emerald_night'; x=339; y=238},
  @{n='obsidian';      x=459; y=238},
  @{n='ivory';         x=339; y=339},
  @{n='azure';         x=459; y=339},
  @{n='lilac';         x=579; y=339},
  @{n='blush';         x=699; y=339},
  @{n='sage';          x=339; y=408},
  @{n='porcelain';     x=459; y=408}
)
$p = Get-Process | Where-Object { $_.MainWindowTitle -eq 'Golden Music' } | Select-Object -First 1
if (-not $p) { Write-Output 'WINDOW NOT FOUND'; exit 1 }
$h = $p.MainWindowHandle

# Read current theme from config so first capture matches what's on screen
$cfg = "$env:USERPROFILE\.goldenmusic\config.json"
$current = 'royal_gold'
try {
  $j = Get-Content $cfg -Raw | ConvertFrom-Json
  if ($j.theme) { $current = $j.theme }
} catch { }

New-Item -ItemType Directory -Force -Path 'E:\gm_themes' | Out-Null

# 1) capture CURRENT theme first (whatever is active)
[Cap]::Grab($h, "E:\gm_themes\_current.png")
Write-Output "current theme: $current -> _current.png"

# 2) if current is not royal_gold, save the live shot as that theme's file
if ($current -ne 'royal_gold') {
  Copy-Item 'E:\gm_themes\_current.png' "E:\gm_themes\$current.png" -Force
  Write-Output "saved $current.png (was current)"
}

# 3) open theme picker: brush button at ~(30, 545) in window
[Cap]::Click($h, 30, 545)
Start-Sleep -Milliseconds 900

foreach ($t in $themes) {
  if ($t.n -eq $current) { continue }   # already captured
  [Cap]::Click($h, $t.x, $t.y)
  Start-Sleep -Milliseconds 1100        # theme + repaint settle
  [Cap]::Grab($h, "E:\gm_themes\$($t.n).png")
  Write-Output "saved $($t.n).png"
}

Write-Output 'ALL THEMES CAPTURED'
