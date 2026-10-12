# Select the Windows Terminal tab whose title matches -Target and bring its window to the front.
# Prints one JSON line: {result: ok|not_found|ambiguous|no_foreground, method, tabs}.
param([Parameter(Mandatory = $true)][string]$Target)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SessionTreeW32 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@

function Clean([string]$name) { ($name -replace '^[^\p{L}\p{N}]+', '').Trim() }
function Emit($obj) { $obj | ConvertTo-Json -Compress; exit 0 }

$A = [System.Windows.Automation.AutomationElement]
$winCond = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, "CASCADIA_HOSTING_WINDOW_CLASS")
$tabCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)

$want = Clean $Target
$all = @()
$hits = @()
foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $winCond)) {
  foreach ($t in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)) {
    $clean = Clean $t.Current.Name
    $all += $clean
    if ($clean -eq $want) { $hits += , @($w, $t) }
  }
}
if ($hits.Count -eq 0) { Emit @{ result = "not_found"; method = $null; tabs = $all } }
if ($hits.Count -gt 1) { Emit @{ result = "ambiguous"; method = $null; tabs = $all } }

$win = $hits[0][0]
$tab = $hits[0][1]
$tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
$hwnd = [IntPtr]$win.Current.NativeWindowHandle
if ([SessionTreeW32]::IsIconic($hwnd)) { [void][SessionTreeW32]::ShowWindow($hwnd, 9) }

function IsFront() { Start-Sleep -Milliseconds 250; return ([SessionTreeW32]::GetForegroundWindow() -eq $hwnd) }

[void][SessionTreeW32]::SetForegroundWindow($hwnd)
if (IsFront) { Emit @{ result = "ok"; method = "A"; tabs = @() } }
# A synthetic Alt press lifts Windows' foreground lock for the next call.
[SessionTreeW32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
[SessionTreeW32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[void][SessionTreeW32]::SetForegroundWindow($hwnd)
if (IsFront) { Emit @{ result = "ok"; method = "B"; tabs = @() } }
try { $win.SetFocus() } catch { }
if (IsFront) { Emit @{ result = "ok"; method = "C"; tabs = @() } }
Emit @{ result = "no_foreground"; method = $null; tabs = @() }
