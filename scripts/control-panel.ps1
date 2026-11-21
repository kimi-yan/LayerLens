<#
.SYNOPSIS
    LayerLens 控制台：启动和停止服务，查看服务、任务、显卡和推理引擎的状态。

.DESCRIPTION
    双击顶层的「LayerLens 控制台.lnk」（带 Logo 的快捷方式）或 scripts\LayerLens 控制台.cmd 打开。
    服务在后台运行，关闭控制台时可以选择
    停止服务或让它继续运行。服务输出写到 data\logs\server.log。

    窗口里所有耗时的事（查询服务、结束进程、读日志）都在后台线程完成，界面不会卡住。
    按钮、状态灯和转圈动画是 C# 自绘控件，第一次打开时编译一次，缓存在 .local\control-panel。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -STA -File scripts\control-panel.ps1
#>
param([ValidateRange(1, 65535)][int]$Port = 8765)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, Microsoft.VisualBasic
$title = 'LayerLens 控制台'
if ($Port -ne 8765) { $title = "$title (端口 $Port)" }

# A second double click brings the open window forward instead of starting another panel.
$mutex = New-Object System.Threading.Mutex($false, "Local\LayerLens.ControlPanel.$Port")
if (-not $mutex.WaitOne(0)) {
    try { [Microsoft.VisualBasic.Interaction]::AppActivate($title) }
    catch { [void][System.Windows.Forms.MessageBox]::Show('LayerLens 控制台已经打开了，请在任务栏中找到它。', $title) }
    exit
}

$root = Split-Path -Parent $PSScriptRoot
$guide = Join-Path $root '使用指南.html'

# ---- Drawing library -------------------------------------------------------------------------

$uiSource = @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace LayerLensUi
{
    public static class Native
    {
        [DllImport("user32.dll")]
        public static extern bool SetProcessDPIAware();

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        public static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

        [DllImport("kernel32.dll")]
        public static extern IntPtr GetConsoleWindow();

        [DllImport("kernel32.dll")]
        public static extern uint GetConsoleProcessList(uint[] list, uint count);

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int cmd);

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
        [DllImport("user32.dll")]
        static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int count);
        [DllImport("user32.dll")]
        static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

        // The console host window of a process, so spawned windows can carry the project icon.
        public static IntPtr FindConsoleWindow(uint pid)
        {
            IntPtr found = IntPtr.Zero;
            EnumWindows(delegate(IntPtr h, IntPtr l)
            {
                uint owner; GetWindowThreadProcessId(h, out owner);
                if (owner == pid)
                {
                    StringBuilder sb = new StringBuilder(256);
                    GetClassName(h, sb, 256);
                    if (sb.ToString() == "ConsoleWindowClass") found = h;
                }
                return true;
            }, IntPtr.Zero);
            return found;
        }
    }

    public static class Theme
    {
        public static Color Hex(string value) { return ColorTranslator.FromHtml(value); }
        public static readonly Color Paper = Hex("#F6F5F1");
        public static readonly Color Panel = Hex("#FFFFFF");
        public static readonly Color Ink = Hex("#1C1D1A");
        public static readonly Color Ink2 = Hex("#4A4C46");
        public static readonly Color Ink3 = Hex("#85877F");
        public static readonly Color Line = Hex("#E2E0D8");
        public static readonly Color Lime = Hex("#B7E43D");
        public static readonly Color LimeHover = Hex("#ABD932");
        public static readonly Color LimeDown = Hex("#9DCB27");
        public static readonly Color Green = Hex("#4D7C0F");
        public static readonly Color Blue = Hex("#2F6FDB");
        public static readonly Color Warn = Hex("#B45309");
        public static readonly Color Danger = Hex("#C0392B");
        public static readonly Color Code = Hex("#22241F");
        public static readonly Color CodeInk = Hex("#D9DBCF");
        public static readonly Color Track = Hex("#ECEAE3");
        public static readonly Color DisabledFill = Hex("#F1F0EB");
        public static readonly Color DisabledInk = Hex("#B3B4AC");
    }

    public static class Shapes
    {
        public static GraphicsPath Round(RectangleF r, float radius)
        {
            GraphicsPath path = new GraphicsPath();
            float d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
            if (d <= 1) { path.AddRectangle(r); return path; }
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }

        public static Color Back(Control control)
        {
            return control.Parent != null ? control.Parent.BackColor : Theme.Paper;
        }
    }

    // One timer drives every spinner, and it stops when nothing is spinning.
    public static class Animator
    {
        static readonly List<Control> controls = new List<Control>();
        static Timer timer;
        static int tick;

        public static float Angle { get { return (tick * 11) % 360; } }

        public static void Add(Control control)
        {
            if (!controls.Contains(control)) controls.Add(control);
            if (timer == null)
            {
                timer = new Timer();
                timer.Interval = 30;
                timer.Tick += OnTick;
            }
            timer.Start();
        }

        public static void Remove(Control control)
        {
            controls.Remove(control);
            if (controls.Count == 0 && timer != null) timer.Stop();
        }

        static void OnTick(object sender, EventArgs e)
        {
            tick++;
            foreach (Control control in controls.ToArray())
            {
                if (control.IsDisposed) controls.Remove(control);
                else if (control.Visible) control.Invalidate();
            }
        }

        public static void Spinner(Graphics g, RectangleF r, Color color, float width)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (Pen track = new Pen(Color.FromArgb(48, color), width)) g.DrawEllipse(track, r);
            using (Pen pen = new Pen(color, width))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                g.DrawArc(pen, r, Angle, 110);
            }
        }
    }

    public enum ButtonKind { Primary, Secondary, Danger, Ghost }

    // A real Button underneath, so keyboard, accessibility and automation keep working.
    public class RoundButton : Button
    {
        bool hover, down, busy;
        ButtonKind kind = ButtonKind.Secondary;
        int radius = 8;

        public RoundButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            UseVisualStyleBackColor = false;
            Cursor = Cursors.Hand;
        }

        public ButtonKind Kind { get { return kind; } set { if (kind != value) { kind = value; Invalidate(); } } }
        public int Radius { get { return radius; } set { radius = value; Invalidate(); } }

        // While busy the button keeps its colour, shows a spinner and ignores clicks.
        public bool Busy
        {
            get { return busy; }
            set
            {
                if (busy == value) return;
                busy = value;
                if (busy) Animator.Add(this); else Animator.Remove(this);
                Cursor = busy ? Cursors.AppStarting : Cursors.Hand;
                Invalidate();
            }
        }

        protected override bool ShowFocusCues { get { return false; } }
        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { if (e.Button == MouseButtons.Left) { down = true; Invalidate(); } base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
        protected override void OnClick(EventArgs e) { if (!busy) base.OnClick(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Back(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            bool active = Enabled || busy;
            bool hot = hover && !busy;
            bool pressed = down && !busy;
            Color fill, border, fore;
            if (!active) { fill = Theme.DisabledFill; border = Theme.Line; fore = Theme.DisabledInk; }
            else if (kind == ButtonKind.Primary) { fill = pressed ? Theme.LimeDown : hot ? Theme.LimeHover : Theme.Lime; border = fill; fore = Theme.Ink; }
            else if (kind == ButtonKind.Danger) { fill = pressed ? Theme.Hex("#F8E1DD") : hot ? Theme.Hex("#FCEEEB") : Theme.Panel; border = Theme.Hex("#E7BBB4"); fore = Theme.Danger; }
            else if (kind == ButtonKind.Ghost) { fill = pressed ? Theme.Hex("#E7E5DD") : hot ? Theme.Hex("#EFEEE8") : Color.Empty; border = Color.Empty; fore = Theme.Ink2; }
            else { fill = pressed ? Theme.Hex("#EAE8E0") : hot ? Theme.Hex("#F4F3EE") : Theme.Panel; border = hot ? Theme.Hex("#C4C1B5") : Theme.Hex("#D8D5CB"); fore = Theme.Ink; }

            RectangleF r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath path = Shapes.Round(r, radius))
            {
                if (fill.A > 0) using (SolidBrush brush = new SolidBrush(fill)) g.FillPath(brush, path);
                if (border.A > 0) using (Pen pen = new Pen(border)) g.DrawPath(pen, path);
            }
            if (Focused && ShowKeyboardCues && active)
            {
                RectangleF ring = new RectangleF(2.5f, 2.5f, Width - 5.5f, Height - 5.5f);
                using (GraphicsPath path = Shapes.Round(ring, Math.Max(2, radius - 2)))
                using (Pen pen = new Pen(Color.FromArgb(150, Theme.Blue), 1.5f)) g.DrawPath(pen, path);
            }

            TextFormatFlags flags = TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding;
            Size text = TextRenderer.MeasureText(g, Text, Font, Size.Empty, flags);
            int spin = busy ? (int)(Font.Height * 0.85f) : 0;
            int gap = busy ? (int)(Font.Height * 0.45f) : 0;
            int x = (Width - text.Width - spin - gap) / 2;
            if (busy) Animator.Spinner(g, new RectangleF(x, (Height - spin) / 2f, spin, spin), fore, Math.Max(2f, spin / 7f));
            TextRenderer.DrawText(g, Text, Font, new Rectangle(x + spin + gap, 0, text.Width + 4, Height), fore, flags);
        }
    }

    public class Card : Panel
    {
        Color border = Theme.Line;
        int radius = 12;

        public Card()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = Theme.Panel;
        }

        public Color Border { get { return border; } set { border = value; Invalidate(); } }
        public int Radius { get { return radius; } set { radius = value; Invalidate(); } }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Back(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            RectangleF r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath path = Shapes.Round(r, radius))
            {
                using (SolidBrush brush = new SolidBrush(BackColor)) g.FillPath(brush, path);
                if (border.A > 0) using (Pen pen = new Pen(border)) g.DrawPath(pen, path);
            }
        }
    }

    // Modal dialog surface: a white card with a hairline border and the system drop shadow.
    // The whole surface drags like a title bar; child buttons keep their own mouse handling.
    public class DialogForm : Form
    {
        public DialogForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Theme.Panel;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        protected override CreateParams CreateParams
        {
            get { CreateParams cp = base.CreateParams; cp.ClassStyle |= 0x20000; return cp; } // CS_DROPSHADOW
        }

        protected override void WndProc(ref Message m)
        {
            base.WndProc(ref m);
            if (m.Msg == 0x84 && m.Result.ToInt64() == 1) m.Result = (IntPtr)2; // HTCLIENT -> HTCAPTION
        }

        protected override void OnPaintBackground(PaintEventArgs e) { e.Graphics.Clear(BackColor); }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (Pen pen = new Pen(Theme.Line)) e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
        }
    }

    // Status light: a dot with a soft halo, or a spinner while something is in progress.
    public class StatusMark : Control
    {
        Color tone = Theme.Ink3;
        bool spin;

        public StatusMark()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        public Color Tone { get { return tone; } set { if (tone != value) { tone = value; Invalidate(); } } }
        public bool Spin
        {
            get { return spin; }
            set { if (spin == value) return; spin = value; if (spin) Animator.Add(this); else Animator.Remove(this); Invalidate(); }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Back(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            if (spin)
            {
                float w = Math.Max(2.5f, Width / 9f);
                Animator.Spinner(g, new RectangleF(w, w, Width - 2 * w - 1, Height - 2 * w - 1), tone, w);
                return;
            }
            using (SolidBrush halo = new SolidBrush(Color.FromArgb(40, tone))) g.FillEllipse(halo, 0, 0, Width - 1, Height - 1);
            float inset = Width * 0.27f;
            using (SolidBrush dot = new SolidBrush(tone)) g.FillEllipse(dot, inset, inset, Width - 2 * inset - 1, Height - 2 * inset - 1);
        }
    }

    public class Pill : Control
    {
        Color fill = Theme.Track;
        bool spin;

        public Pill()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        public Color Fill { get { return fill; } set { if (fill != value) { fill = value; Invalidate(); } } }
        public bool Spin
        {
            get { return spin; }
            set { if (spin == value) return; spin = value; if (spin) Animator.Add(this); else Animator.Remove(this); Invalidate(); }
        }

        int IconSize { get { return (int)(Font.Height * 0.7f); } }
        int Pad { get { return (int)(Font.Height * 0.7f); } }
        const TextFormatFlags Flags = TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding;

        // Sizes the pill to its text and keeps its right edge at the given x.
        public void Fit(int right)
        {
            Size text = TextRenderer.MeasureText(Text, Font, Size.Empty, Flags);
            int width = Pad * 2 + IconSize + Pad / 2 + text.Width;
            if (Left != right - width || Width != width) SetBounds(right - width, Top, width, Height);
        }

        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Back(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (GraphicsPath path = Shapes.Round(new RectangleF(0, 0, Width - 1, Height - 1), Height / 2f))
            using (SolidBrush brush = new SolidBrush(fill)) g.FillPath(brush, path);
            float icon = IconSize;
            float x = Pad;
            float y = (Height - icon) / 2f;
            if (spin) Animator.Spinner(g, new RectangleF(x, y, icon, icon), ForeColor, Math.Max(1.6f, icon / 5f));
            else
            {
                float dot = icon * 0.55f;
                using (SolidBrush brush = new SolidBrush(ForeColor)) g.FillEllipse(brush, x + (icon - dot) / 2, y + (icon - dot) / 2, dot, dot);
            }
            TextRenderer.DrawText(g, Text, Font, new Rectangle((int)(x + icon + Pad / 2), 0, Width, Height), ForeColor, Flags);
        }
    }

    // One line of text that ends in an ellipsis instead of wrapping; Truncated tells when it was cut.
    public class TextLine : Control
    {
        const TextFormatFlags Flags = TextFormatFlags.SingleLine | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding;

        public TextLine()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
        }

        public bool Truncated { get { return TextRenderer.MeasureText(Text, Font, Size.Empty, Flags).Width > Width; } }

        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
        protected override void OnForeColorChanged(EventArgs e) { Invalidate(); base.OnForeColorChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            TextRenderer.DrawText(e.Graphics, Text, Font, ClientRectangle, ForeColor, Flags);
        }
    }

    public class Meter : Control
    {
        float amount;
        Color tone = Theme.Blue;

        public Meter()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        }

        public Color Tone { get { return tone; } set { if (tone != value) { tone = value; Invalidate(); } } }
        public float Value
        {
            get { return amount; }
            set { float v = Math.Max(0f, Math.Min(1f, value)); if (v != amount) { amount = v; Invalidate(); } }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.Clear(Shapes.Back(this));
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float h = Height - 1;
            using (GraphicsPath path = Shapes.Round(new RectangleF(0, 0, Width - 1, h), h / 2))
            using (SolidBrush brush = new SolidBrush(Theme.Track)) g.FillPath(brush, path);
            if (amount <= 0) return;
            float w = Math.Max(h, (Width - 1) * amount);
            using (GraphicsPath path = Shapes.Round(new RectangleF(0, 0, w, h), h / 2))
            using (SolidBrush brush = new SolidBrush(tone)) g.FillPath(brush, path);
        }
    }
}
'@

# Compiling takes a second or two, so the library is built once per source version and reused.
function Import-UiLibrary {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = -join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($uiSource))[0..5] | ForEach-Object { $_.ToString('x2') })
    $dir = Join-Path $root '.local\control-panel'
    $dll = Join-Path $dir "LayerLensUi-$hash.dll"
    $references = 'System.Windows.Forms', 'System.Drawing'
    try {
        if (-not (Test-Path -LiteralPath $dll)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            Get-ChildItem -LiteralPath $dir -Filter 'LayerLensUi-*.dll' | Remove-Item -Force -ErrorAction SilentlyContinue
            Add-Type -TypeDefinition $uiSource -ReferencedAssemblies $references -OutputAssembly $dll -OutputType Library
        }
        Add-Type -LiteralPath $dll
    }
    catch {
        if (-not ('LayerLensUi.RoundButton' -as [type])) { Add-Type -TypeDefinition $uiSource -ReferencedAssemblies $references }
    }
}
Import-UiLibrary
[void][LayerLensUi.Native]::SetProcessDPIAware()
# Own taskbar group, so the button carries the project icon instead of powershell.exe's.
[void][LayerLensUi.Native]::SetCurrentProcessExplicitAppUserModelID("LayerLens.ControlPanel.$Port")
[System.Windows.Forms.Application]::EnableVisualStyles()
$theme = [LayerLensUi.Theme]

# A console dedicated to this panel (double click or -File launch) is hidden so the taskbar only
# shows the panel window with the project icon. A shared terminal is left alone.
$console = [LayerLensUi.Native]::GetConsoleWindow()
if ($console -ne [IntPtr]::Zero) {
    $attached = New-Object uint32[] 4
    if ([LayerLensUi.Native]::GetConsoleProcessList($attached, 4) -le 1) { [void][LayerLensUi.Native]::ShowWindow($console, 0) }
}

# The top-level double-click entry is the logo shortcut; recreate it when missing (fresh checkout).
$lnkPath = Join-Path $root 'LayerLens 控制台.lnk'
if (-not (Test-Path -LiteralPath $lnkPath)) {
    $shortcutScript = Join-Path $root 'scripts\New-ConsoleShortcut.ps1'
    if (Test-Path -LiteralPath $shortcutScript) {
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $shortcutScript + '"') | Out-Null
    }
}

# ---- Background work -------------------------------------------------------------------------

function Encode([string]$Command) { [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($Command)) }
function Quote([string]$Path) { "'" + $Path.Replace("'", "''") + "'" }

$startCommand = '[Console]::OutputEncoding = [Text.Encoding]::UTF8; & ' + (Quote (Join-Path $root 'Start-LayerLens.ps1')) + " -Port $Port"
$logDir = Join-Path $root 'data\logs'
# Panels for other ports keep their own log so they never show each other's output.
$logName = if ($Port -eq 8765) { 'server' } else { "server-$Port" }
# State shared with the background threads. The UI thread only reads Status and never waits.
$shared = [hashtable]::Synchronized(@{
    Port = $Port; Base = "http://127.0.0.1:$Port"; Root = $root; LogDir = $logDir; LogName = $logName
    Log = Join-Path $logDir "$logName.log"; ErrorLog = Join-Path $logDir "$logName.err.log"
    BackendPython = Join-Path $root '.local\backend-venv\Scripts\python.exe'
    Encoded = Encode $startCommand
    Status = $null; Version = 0; Gen = 0; Lock = New-Object object; Quit = $false
    Wake = New-Object System.Threading.AutoResetEvent($false)
    Server = $null; StartedAt = $null; SeenRunning = $false; Stopping = $false; Exited = $false; FirstRun = $false
    Wrappers = @(); WrappersAt = [datetime]::MinValue
})

# Functions available to every background thread.
$common = @'
$ErrorActionPreference = 'Stop'

function Get-Json([string]$Path, [int]$Timeout = 2000) {
    $request = [System.Net.HttpWebRequest]::Create($shared.Base + $Path)
    $request.Timeout = $Timeout
    $request.ReadWriteTimeout = $Timeout
    $request.Proxy = $null
    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
        return $reader.ReadToEnd() | ConvertFrom-Json
    }
    finally { $response.Close() }
}

function Test-PortBusy {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $attempt = $client.BeginConnect('127.0.0.1', $shared.Port, $null, $null)
        return $attempt.AsyncWaitHandle.WaitOne(300) -and $client.Connected
    }
    catch { return $false }
    finally { $client.Close() }
}

# Processes listening on the port, from netstat (fast; a WMI query takes seconds in a fresh thread).
function Get-PortOwners {
    $local = ':' + $shared.Port + '$'
    @(& netstat.exe -ano -p TCP 2>$null | ForEach-Object {
        $fields = -split $_
        if ($fields.Count -ge 5 -and $fields[1] -match $local -and $fields[2] -match ':0$') { [int]$fields[4] }
    } | Select-Object -Unique)
}

# Launcher shells started by any control panel for this checkout and port, found by their exact command.
function Get-Wrappers {
    @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($shared.Encoded) })
}

function Test-Writable([string]$Path) {
    try { $stream = [System.IO.File]::Open($Path, 'OpenOrCreate', 'Write', 'None'); $stream.Close(); return $true }
    catch { return $false }
}

function Read-Tail([string]$Path, [int]$Lines) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return @() }
    $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
    try {
        $partial = $stream.Length -gt 65536
        if ($partial) { [void]$stream.Seek(-65536, 'End') }
        $text = (New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)).ReadToEnd()
    }
    finally { $stream.Close() }
    # PowerShell writes errors to a redirected stderr as CLIXML; keep only the readable messages.
    if ($text.StartsWith('#< CLIXML')) {
        $text = (([regex]::Matches($text, '<S S="Error">(.*?)</S>') | ForEach-Object { $_.Groups[1].Value }) -join '') -replace '_x000D__x000A_', "`n" -replace '_x000A_', "`n"
        $text = [System.Net.WebUtility]::HtmlDecode($text)
    }
    $all = @($text -split "`r?`n")
    if ($partial -and $all.Count) { $all = @($all | Select-Object -Skip 1) }
    @($all | Where-Object { $_.Trim() } | Select-Object -Last $Lines)
}

function Test-RealCommand([string]$Name) {
    # The Microsoft Store alias for python.exe only opens the Store, so it does not count.
    @(Get-Command $Name -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch '\\WindowsApps\\' }).Count -gt 0
}

function Get-StartBlocker {
    if (-not (Test-Path -LiteralPath $shared.BackendPython) -and -not (Test-RealCommand 'python')) {
        return '没有找到 Python。请先安装 Python 3.11 或更高版本（安装时勾选 Add python.exe to PATH），然后重新打开控制台。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $shared.Root 'frontend\dist\index.html')) -and -not (Test-RealCommand 'npm.cmd')) {
        return '没有找到 Node.js，无法构建工作台页面。请先安装 Node.js 20.19 或更高版本，然后重新打开控制台。'
    }
    return ''
}

function Start-Server {
    New-Item -ItemType Directory -Force -Path $shared.LogDir | Out-Null
    # A server started by an earlier panel may still hold the log open; write next to it instead.
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $name = $shared.LogName
    $log = Join-Path $shared.LogDir "$name.log"
    $errors = Join-Path $shared.LogDir "$name.err.log"
    if (-not (Test-Writable $log)) { $log = Join-Path $shared.LogDir "$name-$stamp.log" }
    if (-not (Test-Writable $errors)) { $errors = Join-Path $shared.LogDir "$name-$stamp.err.log" }
    $shared.Log = $log
    $shared.ErrorLog = $errors
    $shared.FirstRun = -not (Test-Path -LiteralPath $shared.BackendPython) -or -not (Test-Path -LiteralPath (Join-Path $shared.Root 'frontend\dist\index.html'))
    $env:PYTHONUTF8 = '1'
    $process = Start-Process powershell.exe -PassThru -WindowStyle Hidden -WorkingDirectory $shared.Root `
        -RedirectStandardOutput $log -RedirectStandardError $errors `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $shared.Encoded
    $shared.StartedAt = Get-Date
    $shared.SeenRunning = $false
    $shared.Exited = $false
    $shared.Server = $process
}

# Ends the launcher and uvicorn process trees (including running engine subprocesses). Returns how many were found.
function Stop-Server {
    $shared.Stopping = $true
    try {
        $ids = @($shared.Wrappers | ForEach-Object { $_.ProcessId })
        $server = $shared.Server
        if ($server -and -not $server.HasExited) { $ids = @($server.Id) + $ids }
        # Only end the process on the port when it answers as LayerLens, never some other program.
        $layerLens = $false
        try { [void](Get-Json '/api/v1/capabilities' 1500); $layerLens = $true } catch { }
        if ($layerLens) { $ids += Get-PortOwners }
        $ids = @($ids | Select-Object -Unique)
        $ErrorActionPreference = 'Continue'
        foreach ($id in $ids) { & taskkill.exe /PID $id /T /F 2>$null | Out-Null }
        for ($i = 0; $i -lt 30 -and (Test-PortBusy); $i++) { Start-Sleep -Milliseconds 200 }
        $shared.Server = $null
        $shared.StartedAt = $null
        $shared.Wrappers = @()
        if ($ids.Count -and (Test-Path -LiteralPath $shared.Log)) {
            try { [System.IO.File]::AppendAllText($shared.Log, "`r`n--- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 已从控制台停止服务 ---`r`n", [System.Text.Encoding]::UTF8) } catch { }
        }
        return $ids.Count
    }
    finally { $shared.Stopping = $false }
}

# One snapshot of everything the window shows.
function Get-Status([switch]$Quick) {
    $status = @{ State = 'stopped'; Active = @(); Engines = @(); Gpu = $null; WorkerAlive = $true; ExitCode = $null }
    try {
        $capabilities = Get-Json '/api/v1/capabilities'
        $status.State = 'running'
        $status.Engines = @($capabilities.engines)
        $status.WorkerAlive = [bool]$capabilities.worker_alive
        if ($shared.Server) { $shared.SeenRunning = $true }
        $shared.Exited = $false
        try { $status.Active = @((Get-Json '/api/v1/task-center?filter=active').jobs) } catch { }
        try { $status.Gpu = (Get-Json '/api/v1/system' 4000).gpu } catch { }
    }
    catch {
        $server = $shared.Server
        if ($server -and -not $server.HasExited) { $status.State = 'starting' }
        elseif ($server -and -not $shared.Stopping) {
            if ($shared.SeenRunning) { $shared.Exited = $true; $shared.Server = $null; $shared.StartedAt = $null }
            else { $status.State = 'failed'; $status.ExitCode = $server.ExitCode }
        }
        elseif (-not $shared.Stopping) {
            # Looking for a launcher from an earlier panel costs a WMI query, so it is done every few seconds.
            if (-not $Quick -and ((Get-Date) - $shared.WrappersAt).TotalSeconds -gt 4) { $shared.Wrappers = @(Get-Wrappers); $shared.WrappersAt = Get-Date }
            if ($shared.Wrappers.Count) {
                $status.State = 'starting'
                if (-not $shared.StartedAt) { $shared.StartedAt = $shared.Wrappers[0].CreationDate }
            }
            elseif (Test-PortBusy) { $status.State = 'busy' }
        }
    }
    $status.StartedAt = $shared.StartedAt
    $status.FirstRun = $shared.FirstRun
    $status.Exited = $shared.Exited
    $lines = @(Read-Tail $shared.Log 60)
    if ($status.State -eq 'failed') { $lines += @(Read-Tail $shared.ErrorLog 20) }
    $status.Log = $lines -join "`r`n"
    return $status
}

# Publishes a snapshot unless an operation finished while it was being taken (Gen moved on).
function Publish-Status($Snapshot, $Gen) {
    [System.Threading.Monitor]::Enter($shared.Lock)
    try {
        if ($null -eq $Gen) { $shared.Gen++ }
        elseif ($Gen -ne $shared.Gen) { return }
        $shared.Status = $Snapshot
        $shared.Version++
    }
    finally { [System.Threading.Monitor]::Exit($shared.Lock) }
}
'@

$pollLoop = @'
while (-not $shared.Quit) {
    $gen = $shared.Gen
    try { $snapshot = Get-Status } catch { $snapshot = @{ State = 'error'; Error = $_.Exception.Message } }
    Publish-Status $snapshot $gen
    [void]$shared.Wake.WaitOne($(if ($snapshot.State -eq 'starting') { 700 } else { 1500 }))
}
'@

$operations = @{
    start = '$result = @{ Error = Get-StartBlocker }; if (-not $result.Error) { Start-Server }; Publish-Status (Get-Status -Quick) $null; $result'
    stop = '$result = @{ Killed = Stop-Server }; $result.StillBusy = Test-PortBusy; Publish-Status (Get-Status -Quick) $null; $result'
    restart = '[void](Stop-Server); $result = @{ Error = Get-StartBlocker }; if (-not $result.Error) { Start-Server }; Publish-Status (Get-Status -Quick) $null; $result'
}

$script:ops = New-Object System.Collections.ArrayList
function Start-Background([string]$Body, [scriptblock]$Done) {
    $ps = [powershell]::Create()
    [void]$ps.AddScript('param($shared)' + "`r`n" + $common + "`r`n" + $Body).AddArgument($shared)
    [void]$script:ops.Add(@{ Ps = $ps; Handle = $ps.BeginInvoke(); Done = $Done })
}

function Complete-Ops {
    foreach ($op in @($script:ops)) {
        if (-not $op.Handle.IsCompleted) { continue }
        [void]$script:ops.Remove($op)
        $result = $null
        $failure = $null
        try { $result = @($op.Ps.EndInvoke($op.Handle)) | Select-Object -Last 1 }
        catch { $failure = $(if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }) }
        $op.Ps.Dispose()
        & $op.Done $result $failure
    }
}

# ---- Window ----------------------------------------------------------------------------------

$scale = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero).DpiX / 96
function S([double]$Value) { [int][Math]::Round($Value * $scale) }
$fontName = 'Microsoft YaHei UI'
function Font([double]$Size, [string]$Style = 'Regular') { New-Object System.Drawing.Font($fontName, $Size, [System.Drawing.FontStyle]$Style) }
$fonts = @{
    Title = Font 15 'Bold'; Sub = Font 9.5; State = Font 17 'Bold'; Body = Font 10.5; Small = Font 9.5
    Section = Font 9.5 'Bold'; Button = Font 10.5; Main = Font 11.5 'Bold'; Pill = Font 9 'Bold'
    Dialog = Font 13 'Bold'; Badge = New-Object System.Drawing.Font('Segoe UI', 15, [System.Drawing.FontStyle]::Bold)
}

function New-Label([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height, $Font, $Color, $Parent) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.SetBounds((S $X), (S $Y), (S $Width), (S $Height))
    $label.Font = $Font
    $label.ForeColor = $Color
    $label.BackColor = [System.Drawing.Color]::Transparent
    $label.TextAlign = 'MiddleLeft'
    $label.AutoEllipsis = $true
    $label.UseMnemonic = $false
    $Parent.Controls.Add($label)
    $label
}

function New-Button([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height, [string]$Kind, $Parent, $Font = $fonts.Button) {
    $button = New-Object LayerLensUi.RoundButton
    $button.Text = $Text
    $button.SetBounds((S $X), (S $Y), (S $Width), (S $Height))
    $button.Font = $Font
    $button.Kind = $Kind
    $button.Radius = S 8
    $Parent.Controls.Add($button)
    $button
}

function New-Card([int]$X, [int]$Y, [int]$Width, [int]$Height, $Parent) {
    $card = New-Object LayerLensUi.Card
    $card.SetBounds((S $X), (S $Y), (S $Width), (S $Height))
    $card.Radius = S 12
    $Parent.Controls.Add($card)
    $card
}

# The project mark: the workbench header SVG geometry (frontend/index.html) on an ink tile,
# same as assets/LayerLens.ico. Coordinates are the SVG's 24-unit viewBox.
function Draw-Logo($Graphics, [float]$Size) {
    $Graphics.SmoothingMode = 'AntiAlias'
    $tile = [LayerLensUi.Shapes]::Round((New-Object System.Drawing.RectangleF(0, 0, ($Size - 1), ($Size - 1))), ($Size * 0.22))
    $brush = New-Object System.Drawing.SolidBrush($theme::Hex('#22241F'))
    $Graphics.FillPath($brush, $tile)
    $brush.Dispose(); $tile.Dispose()
    $lime = $theme::Hex('#C8F169')
    $ink = $theme::Hex('#22241F')
    $pens = @()
    foreach ($alpha in 1.0, 0.65, 0.35) {
        $color = [System.Drawing.Color]::FromArgb(255,
            [int]($ink.R + ($lime.R - $ink.R) * $alpha),
            [int]($ink.G + ($lime.G - $ink.G) * $alpha),
            [int]($ink.B + ($lime.B - $ink.B) * $alpha))
        $pen = New-Object System.Drawing.Pen($color, [Math]::Max(1.2, $Size * 1.8 / 24))
        $pen.StartCap = 'Round'; $pen.EndCap = 'Round'; $pen.LineJoin = 'Round'
        $pens += $pen
    }
    $strokes = @(
        @{ Flat = @(12, 3, 21, 8.5, 12, 14, 3, 8.5); Close = $true; Tone = 0 },
        @{ Flat = @(3, 13, 12, 18.5, 21, 13); Close = $false; Tone = 1 },
        @{ Flat = @(3, 17.5, 12, 22, 21, 17.5); Close = $false; Tone = 2 })
    foreach ($stroke in $strokes) {
        $pts = New-Object System.Drawing.PointF[] ($stroke.Flat.Count / 2)
        for ($i = 0; $i -lt $pts.Count; $i++) {
            $pts[$i] = New-Object System.Drawing.PointF(($Size * $stroke.Flat[2 * $i] / 24), ($Size * $stroke.Flat[2 * $i + 1] / 24))
        }
        if ($stroke.Close) { $Graphics.DrawPolygon($pens[$stroke.Tone], $pts) }
        else { $Graphics.DrawLines($pens[$stroke.Tone], $pts) }
    }
    foreach ($pen in $pens) { $pen.Dispose() }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = $title
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = $theme::Paper
$form.Font = $fonts.Body
[System.Windows.Forms.Control].GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'NonPublic,Instance').SetValue($form, $true, $null)
# Window and taskbar icon: the multi-size project icon, with the drawn mark as fallback.
$icoPath = Join-Path $root 'assets\LayerLens.ico'
$script:iconSmall = $null
$script:iconBig = $null
try {
    if (Test-Path -LiteralPath $icoPath) {
        $form.Icon = New-Object System.Drawing.Icon($icoPath)
        $script:iconSmall = New-Object System.Drawing.Icon($icoPath, 16, 16)
        $script:iconBig = New-Object System.Drawing.Icon($icoPath, 32, 32)
    }
    else {
        $bitmap = New-Object System.Drawing.Bitmap(32, 32)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        Draw-Logo $graphics 32
        $graphics.Dispose()
        $form.Icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
    }
}
catch { }

# Shrink only the log area on small screens so every button stays visible.
$logHeight = 150
$workingHeight = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height - (S 60)
if ((S 756) -gt $workingHeight) { $logHeight = [Math]::Max(70, 150 - [int](((S 756) - $workingHeight) / $scale)) }
$form.ClientSize = New-Object System.Drawing.Size((S 680), (S (606 + $logHeight)))

# Header
$header = New-Object System.Windows.Forms.Panel
$header.SetBounds(0, 0, (S 680), (S 84))
$header.BackColor = $theme::Panel
$header.Add_Paint({
    param($sender, $e)
    $pen = New-Object System.Drawing.Pen($theme::Line)
    $e.Graphics.DrawLine($pen, 0, $sender.Height - 1, $sender.Width, $sender.Height - 1)
    $pen.Dispose()
})
$form.Controls.Add($header)
$mark = New-Object System.Windows.Forms.Panel
$mark.SetBounds((S 28), (S 24), (S 36), (S 36))
$mark.BackColor = $theme::Panel
$mark.Add_Paint({ param($sender, $e); Draw-Logo $e.Graphics $sender.Width })
$header.Controls.Add($mark)
[void](New-Label 'LayerLens 控制台' 76 16 380 32 $fonts.Title $theme::Ink $header)
[void](New-Label '层析镜 · 启动、停止和查看图片拆层服务' 77 48 420 24 $fonts.Sub $theme::Ink3 $header)
$guideButton = New-Button '使用指南' 548 22 108 40 'Ghost' $header

# Status card
$hero = New-Card 24 104 632 140 $form
$statusMark = New-Object LayerLensUi.StatusMark
$statusMark.SetBounds((S 28), (S 30), (S 28), (S 28))
$hero.Controls.Add($statusMark)
$stateLabel = New-Label '正在检查…' 68 20 360 38 $fonts.State $theme::Ink $hero
$detailLabel = New-Label '' 70 60 360 66 $fonts.Body $theme::Ink2 $hero
$detailLabel.TextAlign = 'TopLeft'
$toggleButton = New-Button '请稍候' 452 22 156 50 'Primary' $hero $fonts.Main
$openButton = New-Button '打开工作台' 452 82 156 40 'Secondary' $hero

# Details
[void](New-Label '运行状态' 28 258 200 24 $fonts.Section $theme::Ink3 $form)
$info = New-Card 24 284 632 206 $form
$rows = @{}
$rowNames = [ordered]@{ address = '工作台地址'; tasks = '任务'; gpu = '显卡'; qwen = '通用拆层'; seethrough = '二次元角色拆层' }
$i = 0
foreach ($key in $rowNames.Keys) {
    $y = 8 + $i * 38
    $name = New-Object LayerLensUi.TextLine
    $name.SetBounds((S 22), (S $y), (S 124), (S 38))
    $name.Font = $fonts.Body
    $name.ForeColor = $theme::Ink3
    $name.Text = $rowNames[$key]
    $info.Controls.Add($name)
    $value = New-Object LayerLensUi.TextLine
    $value.SetBounds((S 148), (S $y), (S 330), (S 38))
    $value.Font = $fonts.Body
    $value.ForeColor = $theme::Ink3
    $value.Text = '—'
    $info.Controls.Add($value)
    $row = @{ Value = $value; Y = $y }
    if ($key -in 'tasks', 'gpu') {
        $meter = New-Object LayerLensUi.Meter
        $meter.SetBounds((S 492), (S ($y + 15)), (S 120), (S 8))
        $meter.Visible = $false
        $info.Controls.Add($meter)
        $row.Meter = $meter
    }
    if ($key -in 'qwen', 'seethrough') {
        $row.Value.Width = S 290
        $pill = New-Object LayerLensUi.Pill
        $pill.Font = $fonts.Pill
        $pill.SetBounds((S 540), (S ($y + 7)), (S 72), (S 24))
        $pill.Visible = $false
        $info.Controls.Add($pill)
        $row.Pill = $pill
        $row.Install = New-Button '安装' 0 ($y + 4) 68 30 'Primary' $info
        $row.Install.Tag = $key
        $row.Install.Visible = $false
    }
    if ($i -lt 4) {
        $line = New-Object System.Windows.Forms.Panel
        $line.SetBounds((S 18), (S ($y + 38)), (S 596), 1)
        $line.BackColor = $theme::Hex('#EEECE5')
        $info.Controls.Add($line)
    }
    $rows[$key] = $row
    $i++
}
$rows.address.Value.Cursor = 'Hand'
$copyButton = New-Button '复制' 548 13 64 30 'Ghost' $info
$copyButton.Visible = $false
$tips = New-Object System.Windows.Forms.ToolTip
$tips.SetToolTip($rows.address.Value, '在浏览器中打开')
$tips.SetToolTip($copyButton, '复制工作台地址')

# Tools
$doctorButton = New-Button '环境自检' 24 506 202 42 'Secondary' $form
$logButton = New-Button '打开日志文件' 239 506 202 42 'Secondary' $form
$folderButton = New-Button '打开项目文件夹' 454 506 202 42 'Secondary' $form
$tips.SetToolTip($doctorButton, '检查显卡、内存、磁盘和两个引擎，结果显示在新窗口里')

# Recent log
[void](New-Label '最近日志' 28 562 200 24 $fonts.Section $theme::Ink3 $form)
$logCard = New-Card 24 588 632 $logHeight $form
$logCard.BackColor = $theme::Code
$logCard.Border = $theme::Code
$logCard.Radius = S 10
$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.WordWrap = $false
$logBox.ScrollBars = 'None'
$logBox.BorderStyle = 'None'
$logBox.BackColor = $theme::Code
$logBox.ForeColor = $theme::CodeInk
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9.5)
$logBox.SetBounds((S 16), (S 12), (S 600), (S ($logHeight - 24)))
$logCard.Controls.Add($logBox)

# ---- View ------------------------------------------------------------------------------------

$script:pending = $null       # launching / stopping / restarting while an operation runs
$script:viewState = 'checking'
$script:installers = @{}
$script:iconPending = @{}     # spawned console pids waiting for their window to appear
$script:seenVersion = -1
$script:lastPaint = 0
$script:copiedAt = 0
$script:closeAfterStop = $false
$engineNames = @{ qwen = '通用拆层'; seethrough = '二次元角色拆层' }
$pillTones = @{
    ok = @($theme::Hex('#E7F2D3'), $theme::Hex('#3F6A0A')); warn = @($theme::Hex('#FBEED8'), $theme::Hex('#9A5B07'))
    info = @($theme::Hex('#E5EDFC'), $theme::Blue); mute = @($theme::Hex('#EFEEE9'), $theme::Ink3)
}

function Set-Text($Control, [string]$Text) { if ($Control.Text -ne $Text) { $Control.Text = $Text } }
function Set-Fore($Control, $Color) { if ($Control.ForeColor -ne $Color) { $Control.ForeColor = $Color } }
function Set-Shown($Control, [bool]$Visible) { if ($Control.Visible -ne $Visible) { $Control.Visible = $Visible } }
function Set-Tip($Control, [string]$Text) { if ($tips.GetToolTip($Control) -ne $Text) { $tips.SetToolTip($Control, $Text) } }

# Spawned PowerShell windows (doctor, installer) get the project icon once their console exists.
# The wait happens on the UI timer, never as a blocking call.
function Set-ConsoleIconLater([int]$ProcessId) { $script:iconPending[$ProcessId] = [Environment]::TickCount + 6000 }

function Grant-ConsoleIcons {
    foreach ($pid2 in @($script:iconPending.Keys)) {
        $hwnd = [LayerLensUi.Native]::FindConsoleWindow([uint32]$pid2)
        if ($hwnd -ne [IntPtr]::Zero) {
            try {
                $small = if ($script:iconSmall) { $script:iconSmall.Handle } else { $form.Icon.Handle }
                $big = if ($script:iconBig) { $script:iconBig.Handle } else { $form.Icon.Handle }
                [void][LayerLensUi.Native]::SendMessage($hwnd, 0x80, [IntPtr]::Zero, $small)   # WM_SETICON / ICON_SMALL
                [void][LayerLensUi.Native]::SendMessage($hwnd, 0x80, [IntPtr]::One, $big)     # WM_SETICON / ICON_BIG
            }
            catch { }
            $script:iconPending.Remove($pid2)
        }
        elseif ([Environment]::TickCount -gt $script:iconPending[$pid2]) { $script:iconPending.Remove($pid2) }
    }
}

function Show-State([string]$Mode, $Tone, [string]$Title, [string]$Detail) {
    $statusMark.Spin = $Mode -eq 'spin'
    $statusMark.Tone = $Tone
    Set-Text $stateLabel $Title
    Set-Text $detailLabel $Detail
}

function Set-Toggle([string]$Text, [string]$Kind, [bool]$Enabled = $true, [bool]$Busy = $false) {
    Set-Text $toggleButton $Text
    $toggleButton.Kind = $Kind
    $toggleButton.Busy = $Busy
    if ($toggleButton.Enabled -ne $Enabled) { $toggleButton.Enabled = $Enabled }
}

function Set-Pill($Pill, [string]$Text, [string]$Tone, [bool]$Spin) {
    Set-Shown $Pill $true
    Set-Text $Pill $Text
    $Pill.Fill = $pillTones[$Tone][0]
    Set-Fore $Pill $pillTones[$Tone][1]
    $Pill.Spin = $Spin
    $Pill.Fit((S 612))
}

function Test-Installing([string]$Id) {
    $process = $script:installers[$Id]
    return [bool]($process -and -not $process.HasExited)
}

function Update-View {
    $s = $shared.Status
    $state = if ($script:pending) { $script:pending } elseif ($s) { $s.State } else { 'checking' }
    if ($state -eq 'running' -and -not $s.WorkerAlive) { $state = 'abnormal' }
    $script:viewState = $state
    $readyCount = if ($s) { @($s.Engines | Where-Object { $_.ready }).Count } else { 0 }

    switch ($state) {
        'checking' { Show-State 'spin' $theme::Ink3 '正在检查…' '正在读取服务状态。'; Set-Toggle '请稍候' 'Secondary' $false $true }
        'error' { Show-State 'spin' $theme::Ink3 '正在检查…' ('读取状态时出错，正在重试：' + $s.Error); Set-Toggle '请稍候' 'Secondary' $false $true }
        'launching' { Show-State 'spin' $theme::Blue '正在启动…' '正在启动后台服务。'; Set-Toggle '正在启动' 'Primary' $true $true }
        'restarting' { Show-State 'spin' $theme::Blue '正在重新启动…' '先停止服务，再重新启动。'; Set-Toggle '正在重启' 'Primary' $true $true }
        'stopping' { Show-State 'spin' $theme::Ink2 '正在停止…' '正在结束服务和正在运行的推理进程，通常几秒内完成。'; Set-Toggle '正在停止' 'Danger' $true $true }
        'starting' {
            $seconds = if ($s.StartedAt) { [int][Math]::Max(0, ((Get-Date) - $s.StartedAt).TotalSeconds) } else { 0 }
            $detail = if ($s.FirstRun) { '第一次启动要安装后端依赖并构建工作台页面，需要几分钟，进度见下方日志。' } else { '通常十几秒内完成。' }
            Show-State 'spin' $theme::Blue "正在启动 · $seconds 秒" $detail
            Set-Toggle '取消启动' 'Danger'
        }
        'running' {
            if ($readyCount) { Show-State 'dot' $theme::Green '运行中' '服务在后台运行，关闭本窗口不会停止它。点「打开工作台」开始使用。' }
            else { Show-State 'dot' $theme::Warn '运行中 · 未安装引擎' '工作台可以打开，但还不能新建拆层任务。在下方给需要的引擎点「安装」。' }
            Set-Toggle '停止服务' 'Danger'
        }
        'abnormal' { Show-State 'dot' $theme::Danger '运行异常' '处理任务的后台进程停止了，新任务不会被处理。点「重新启动」即可恢复。'; Set-Toggle '重新启动' 'Primary' }
        'failed' {
            Show-State 'dot' $theme::Danger '启动失败' "启动脚本退出了（退出码 $($s.ExitCode)）。错误原因在下方日志里，处理后点「重新启动」。"
            Set-Toggle '重新启动' 'Primary'
        }
        'busy' { Show-State 'dot' $theme::Danger "端口 $Port 被占用" "另一个程序正在使用 $Port 端口。关闭它之后，这里会自动恢复。"; Set-Toggle '无法启动' 'Secondary' $false }
        default {
            $detail = if ($s.Exited) { '服务意外退出了，原因见下方日志。点「启动服务」可以重新启动。' }
                elseif (-not (Test-Path -LiteralPath $shared.BackendPython)) { '点「启动服务」在后台运行 LayerLens。第一次启动要准备环境，需要几分钟。' }
                else { '点「启动服务」在后台运行 LayerLens，启动后用浏览器打开工作台。' }
            Show-State 'dot' $theme::Ink3 '已停止' $detail
            Set-Toggle '启动服务' 'Primary'
        }
    }

    $live = $state -in 'running', 'abnormal'
    if ($openButton.Enabled -ne $live) { $openButton.Enabled = $live }
    Set-Shown $copyButton $live
    if ([Environment]::TickCount - $script:copiedAt -gt 1500) { Set-Text $copyButton '复制' }

    if ($live) {
        Set-Text $rows.address.Value "$($shared.Base)/"
        Set-Fore $rows.address.Value $theme::Blue

        $active = @($s.Active)
        $current = $active | Where-Object { $_.status -eq 'running' } | Select-Object -First 1
        $queued = @($active | Where-Object { $_.status -eq 'queued' }).Count
        $parts = @()
        $fraction = -1
        if ($current) {
            $text = "正在处理 $($current.filename)"
            if ($current.progress.total_steps) {
                $fraction = $current.progress.step / $current.progress.total_steps
                $text += ' · {0:N0}%' -f ($fraction * 100)
            }
            $parts += $text
        }
        if ($queued) { $parts += "排队 $queued 个" }
        Set-Text $rows.tasks.Value $(if ($parts) { $parts -join '，' } else { '没有进行中的任务' })
        Set-Fore $rows.tasks.Value $(if ($parts) { $theme::Ink } else { $theme::Ink3 })
        Set-Shown $rows.tasks.Meter ($fraction -ge 0)
        if ($fraction -ge 0) { $rows.tasks.Meter.Value = $fraction }

        if ($s.Gpu) {
            $share = $s.Gpu.used_mib / [Math]::Max(1, $s.Gpu.total_mib)
            Set-Text $rows.gpu.Value ('{0} · {1:N1} / {2:N1} GB' -f ($s.Gpu.name -replace '^NVIDIA (GeForce )?', ''), ($s.Gpu.used_mib / 1024), ($s.Gpu.total_mib / 1024))
            Set-Fore $rows.gpu.Value $theme::Ink
            $rows.gpu.Meter.Value = $share
            $rows.gpu.Meter.Tone = $(if ($share -gt 0.9) { $theme::Warn } else { $theme::Blue })
            Set-Shown $rows.gpu.Meter $true
        }
        else {
            Set-Text $rows.gpu.Value '没有检测到 NVIDIA 显卡'
            Set-Fore $rows.gpu.Value $theme::Warn
            Set-Shown $rows.gpu.Meter $false
        }
    }
    else {
        Set-Text $rows.address.Value '—'
        Set-Fore $rows.address.Value $theme::Ink3
        foreach ($key in 'tasks', 'gpu') {
            Set-Text $rows[$key].Value '服务运行后显示'
            Set-Fore $rows[$key].Value $theme::Ink3
            Set-Shown $rows[$key].Meter $false
        }
    }

    foreach ($id in 'qwen', 'seethrough') {
        $row = $rows[$id]
        $engine = if ($live) { $s.Engines | Where-Object { $_.id -eq $id } | Select-Object -First 1 }
        $installing = Test-Installing $id
        $showInstall = $false
        if ($engine -and $engine.ready) {
            $working = $current -and $current.engine_id -eq $id
            Set-Text $row.Value $engine.name
            Set-Fore $row.Value $theme::Ink2
            Set-Tip $row.Value ('运行方式：' + $engine.runtime)
            if ($working) { Set-Pill $row.Pill '正在推理' 'info' $true } else { Set-Pill $row.Pill '已就绪' 'ok' $false }
        }
        elseif ($installing) {
            Set-Text $row.Value '安装窗口已打开，完成后这里会自动更新'
            Set-Fore $row.Value $theme::Ink2
            Set-Pill $row.Pill '安装中' 'info' $true
        }
        elseif ($engine) {
            $missing = @($engine.missing_parts | ForEach-Object { $_.label }) -join '、'
            Set-Text $row.Value $(if ($missing) { "缺少$missing" } else { '缺少所需文件' })
            Set-Fore $row.Value $theme::Ink3
            Set-Pill $row.Pill '未安装' 'warn' $false
            $showInstall = $true
        }
        else {
            Set-Text $row.Value '服务运行后显示'
            Set-Fore $row.Value $theme::Ink3
            Set-Shown $row.Pill $false
            $row.Pill.Spin = $false
        }
        if ($showInstall) {
            $x = $row.Pill.Left - (S 8) - $row.Install.Width
            if ($row.Install.Left -ne $x) { $row.Install.Left = $x }
        }
        Set-Shown $row.Install $showInstall
    }

    # Cut-off values (long file names, GPU names) show in full on hover.
    foreach ($key in 'tasks', 'gpu') { Set-Tip $rows[$key].Value $(if ($rows[$key].Value.Truncated) { $rows[$key].Value.Text } else { '' }) }

    $text = if ($s -and $s.Log) { $s.Log } elseif ($state -eq 'checking') { '' } else { '还没有日志。启动服务后，这里会显示服务输出。' }
    if ($logBox.Text -ne $text) {
        $logBox.Text = $text
        $logBox.SelectionStart = $logBox.TextLength
        $logBox.ScrollToCaret()
    }
}

# ---- Actions ---------------------------------------------------------------------------------

# Themed modal dialog that replaces the native MessageBox. Buttons is a left-to-right list of
# @{ Text; Kind; Result; Default }; the function returns the Result of the button that was used.
function Show-Dialog {
    param(
        [string]$Heading,
        [string]$Body,
        [ValidateSet('info', 'question', 'warn', 'error')][string]$Tone = 'info',
        [Parameter(Mandatory)][array]$Buttons
    )
    $badges = @{
        info = @($theme::Hex('#E5EDFC'), $theme::Blue, 'i'); question = @($theme::Hex('#E5EDFC'), $theme::Blue, '?')
        warn = @($theme::Hex('#FBEED8'), $theme::Warn, '!'); error = @($theme::Hex('#F8E1DD'), $theme::Danger, '!')
    }
    $badge = $badges[$Tone]
    $singleFlags = [System.Windows.Forms.TextFormatFlags]::SingleLine -bor [System.Windows.Forms.TextFormatFlags]::NoPrefix -bor [System.Windows.Forms.TextFormatFlags]::NoPadding
    $wrapFlags = [System.Windows.Forms.TextFormatFlags]::WordBreak -bor [System.Windows.Forms.TextFormatFlags]::NoPrefix

    # Size the buttons first so a long row can widen the dialog instead of clipping.
    $pad = S 24; $gap = S 10; $footerH = S 40
    $widths = @($Buttons | ForEach-Object {
        $size = [System.Windows.Forms.TextRenderer]::MeasureText($_.Text, $fonts.Button, [System.Drawing.Size]::Empty, $singleFlags)
        [Math]::Max((S 84), $size.Width + (S 32))
    })
    $rowWidth = ($widths | Measure-Object -Sum).Sum + $gap * ($Buttons.Count - 1)
    $width = [Math]::Max((S 460), $rowWidth + 2 * $pad)

    $iconSize = S 40; $textX = S 80; $textW = $width - $textX - $pad
    $titleH = S 28; $bodyY = $pad + $titleH + (S 4)
    $measured = [System.Windows.Forms.TextRenderer]::MeasureText($Body, $fonts.Body, (New-Object System.Drawing.Size($textW, 10000)), $wrapFlags)
    $bodyH = [Math]::Max((S 22), $measured.Height + (S 2))
    $footerY = $bodyY + $bodyH + (S 22)
    $height = $footerY + $footerH + $pad

    $dialog = New-Object LayerLensUi.DialogForm
    $dialog.Font = $fonts.Body
    $dialog.ClientSize = New-Object System.Drawing.Size($width, $height)
    $dialog.Add_Paint({
        param($sender, $e)
        $g = $e.Graphics
        $g.SmoothingMode = 'AntiAlias'
        $brush = New-Object System.Drawing.SolidBrush($badge[0])
        $g.FillEllipse($brush, $pad, $pad, $iconSize, $iconSize)
        $brush.Dispose()
        $centerFlags = [System.Windows.Forms.TextFormatFlags]::HorizontalCenter -bor [System.Windows.Forms.TextFormatFlags]::VerticalCenter -bor [System.Windows.Forms.TextFormatFlags]::SingleLine -bor [System.Windows.Forms.TextFormatFlags]::NoPrefix
        [System.Windows.Forms.TextRenderer]::DrawText($g, $badge[2], $fonts.Badge, (New-Object System.Drawing.Rectangle($pad, $pad, $iconSize, $iconSize)), $badge[1], $centerFlags)
        $titleFlags = [System.Windows.Forms.TextFormatFlags]::SingleLine -bor [System.Windows.Forms.TextFormatFlags]::VerticalCenter -bor [System.Windows.Forms.TextFormatFlags]::EndEllipsis -bor [System.Windows.Forms.TextFormatFlags]::NoPrefix
        [System.Windows.Forms.TextRenderer]::DrawText($g, $Heading, $fonts.Dialog, (New-Object System.Drawing.Rectangle($textX, $pad, $textW, $titleH)), $theme::Ink, $titleFlags)
        [System.Windows.Forms.TextRenderer]::DrawText($g, $Body, $fonts.Body, (New-Object System.Drawing.Rectangle($textX, $bodyY, $textW, $bodyH)), $theme::Ink2, $wrapFlags)
    }.GetNewClosure())

    $x = $width - $pad - $rowWidth
    $accept = $null; $cancel = $null; $cancelResult = $Buttons[-1].Result
    for ($i = 0; $i -lt $Buttons.Count; $i++) {
        $spec = $Buttons[$i]
        $button = New-Object LayerLensUi.RoundButton
        $button.Text = $spec.Text
        $button.Kind = $spec.Kind
        $button.Font = $fonts.Button
        $button.Radius = S 8
        $button.SetBounds($x, $footerY, $widths[$i], $footerH)
        $button.Add_Click({ $dialog.Tag = $spec.Result; $dialog.DialogResult = 'OK' }.GetNewClosure())
        $dialog.Controls.Add($button)
        if ($spec.Default) { $accept = $button }
        if ($spec.Kind -eq 'Ghost') { $cancel = $button; $cancelResult = $spec.Result }
        if ($i -eq $Buttons.Count - 1) { if (-not $accept) { $accept = $button }; if (-not $cancel) { $cancel = $button } }
        $x += $widths[$i] + $gap
    }
    $dialog.AcceptButton = $accept
    $dialog.CancelButton = $cancel
    # Esc and Alt+F4 land on the cancel button's result.
    $dialog.Tag = $cancelResult
    $dialog.Location = New-Object System.Drawing.Point(
        ($form.Left + ($form.Width - $dialog.Width) / 2),
        ($form.Top + ($form.Height - $dialog.Height) / 2))
    $dialog.Add_Shown({ $dialog.ActiveControl = $accept }.GetNewClosure())
    [void]$dialog.ShowDialog($form)
    $result = [string]$dialog.Tag
    $dialog.Dispose()
    return $result
}

function Show-Message([string]$Text, [string]$Caption = '提示', [string]$Icon = 'Information') {
    $tone = switch ($Icon) { 'Warning' { 'warn' } 'Error' { 'error' } default { 'info' } }
    [void](Show-Dialog $Caption $Text $tone @(@{ Text = '知道了'; Kind = 'Primary'; Result = 'ok'; Default = $true }))
}

function Confirm-Stop([string]$Action) {
    $active = if ($shared.Status) { @($shared.Status.Active) } else { @() }
    if (-not $active.Count) { return $true }
    $running = @($active | Where-Object { $_.status -eq 'running' }).Count
    $queued = $active.Count - $running
    $answer = Show-Dialog '还有任务没有完成' `
        "正在处理 $running 个，排队 $queued 个。${Action}后，这些任务可能被记为中断，之后可以在任务中心重试。" `
        'warn' @(
            @{ Text = '取消'; Kind = 'Ghost'; Result = 'no'; Default = $true },
            @{ Text = "仍要$Action"; Kind = 'Danger'; Result = 'yes' })
    return $answer -eq 'yes'
}

function Invoke-Operation([string]$Pending, [string]$Name) {
    if ($script:pending) { return }
    $script:pending = $Pending
    Update-View
    Start-Background $operations[$Name] {
        param($result, $failure)
        $script:pending = $null
        Update-View
        if ($failure) { Show-Message ('操作没有完成：' + $failure) '操作失败' 'Error' }
        elseif ($result.Error) { Show-Message $result.Error '还不能启动' 'Warning' }
        elseif ($result.ContainsKey('StillBusy') -and $result.StillBusy -and -not $result.Killed) {
            Show-Message "端口 $Port 上的服务不是从这个项目文件夹启动的，控制台无法停止它。请到启动它的窗口里按 Ctrl+C 关闭。" '无法停止' 'Warning'
        }
        if ($script:closeAfterStop) { $script:closeAfterStop = $false; $form.Close() }
    }
}

$toggleButton.Add_Click({
    try {
        switch ($script:viewState) {
            { $_ -in 'stopped', 'failed' } { Invoke-Operation 'launching' 'start' }
            'starting' { Invoke-Operation 'stopping' 'stop' }
            'running' { if (Confirm-Stop '停止服务') { Invoke-Operation 'stopping' 'stop' } }
            'abnormal' { if (Confirm-Stop '重新启动服务') { Invoke-Operation 'restarting' 'restart' } }
        }
    }
    catch { Show-Message $_.Exception.Message '操作失败' 'Error' }
})

$openWorkbench = { if ($script:viewState -in 'running', 'abnormal') { Start-Process "$($shared.Base)/" } }
$openButton.Add_Click($openWorkbench)
$rows.address.Value.Add_Click($openWorkbench)
$copyButton.Add_Click({
    try { [System.Windows.Forms.Clipboard]::SetText("$($shared.Base)/"); $copyButton.Text = '已复制'; $script:copiedAt = [Environment]::TickCount }
    catch { Show-Message '复制失败，请手动输入地址。' }
})
$guideButton.Add_Click({ if (Test-Path -LiteralPath $guide) { Start-Process $guide } else { Show-Message '没有找到 使用指南.html。' } })
$folderButton.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + $root + '"') })
$logButton.Add_Click({
    if (Test-Path -LiteralPath $shared.Log) { Start-Process notepad.exe -ArgumentList ('"' + $shared.Log + '"') }
    else { Show-Message '还没有日志文件，启动一次服务后就会生成。' }
})

$doctorButton.Add_Click({
    $python = if (Test-Path -LiteralPath $shared.BackendPython) { $shared.BackendPython } else {
        (Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1).Source
    }
    if (-not $python) { Show-Message '环境自检需要 Python。请先安装 Python 3.11 或更高版本。'; return }
    $command = '[Console]::OutputEncoding = [Text.Encoding]::UTF8; $host.UI.RawUI.WindowTitle = ''LayerLens 环境自检''; & ' +
        (Quote $python) + ' ' + (Quote (Join-Path $root 'scripts\doctor.py')) + "; Write-Host ''; Write-Host '检查完成，可以关闭这个窗口。'"
    $doctor = Start-Process powershell.exe -PassThru -WorkingDirectory $root -ArgumentList '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', (Encode $command)
    Set-ConsoleIconLater $doctor.Id
})

$installClick = {
    param($sender)
    $id = $sender.Tag
    $name = $engineNames[$id]
    if (Test-Installing $id) {
        try { [Microsoft.VisualBasic.Interaction]::AppActivate($script:installers[$id].Id) } catch { }
        return
    }
    $engine = $shared.Status.Engines | Where-Object { $_.id -eq $id } | Select-Object -First 1
    $size = if ($engine.setup.download_gib) { "需要下载约 $($engine.setup.download_gib) GB，占用约 $($engine.setup.disk_gib) GB 磁盘，耗时取决于网速。" } else { '需要下载几十 GB 的模型文件。' }
    $answer = Show-Dialog "安装$name" `
        "将打开一个窗口安装「$name」。$size`r`n`r`n安装期间工作台照常可用。中途断网或关闭窗口也没关系，再点一次「安装」会接着下载。" `
        'info' @(
            @{ Text = '取消'; Kind = 'Ghost'; Result = 'cancel' },
            @{ Text = '开始安装'; Kind = 'Primary'; Result = 'ok'; Default = $true })
    if ($answer -ne 'ok') { return }
    # try/catch keeps the window open on failure so the error stays readable.
    $command = '[Console]::OutputEncoding = [Text.Encoding]::UTF8; $host.UI.RawUI.WindowTitle = ''LayerLens 安装' + $name + '''; ' +
        'try { & ' + (Quote (Join-Path $root 'scripts\setup-engine.ps1')) + " -Engine $id; Write-Host ''; Write-Host '安装完成。回到控制台查看状态，或刷新工作台页面。' -ForegroundColor Green } " +
        "catch { Write-Host ''; Write-Host ('安装没有完成：' + `$_) -ForegroundColor Red; Write-Host '处理后可以在控制台再点一次「安装」，已下载的文件不会重下。' }; " +
        "Write-Host ''; Read-Host '按回车键关闭窗口' | Out-Null"
    $script:installers[$id] = Start-Process powershell.exe -PassThru -WorkingDirectory $root `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', (Encode $command)
    Set-ConsoleIconLater $script:installers[$id].Id
    Update-View
}
$rows.qwen.Install.Add_Click($installClick)
$rows.seethrough.Install.Add_Click($installClick)

$form.Add_FormClosing({
    param($sender, $e)
    if ($script:pending) {
        $e.Cancel = $true
        Show-Message '正在启动或停止服务，请等它完成后再关闭。'
        return
    }
    if ($script:viewState -notin 'running', 'starting', 'abnormal') { return }
    $answer = Show-Dialog '服务还在运行' `
        '只关闭控制台，服务会继续在后台运行，正在处理的任务不受影响。停止服务会结束后台进程，未完成的任务将被记为中断。' `
        'question' @(
            @{ Text = '取消'; Kind = 'Ghost'; Result = 'cancel' },
            @{ Text = '停止服务并退出'; Kind = 'Danger'; Result = 'yes' },
            @{ Text = '仅关闭控制台'; Kind = 'Primary'; Result = 'no'; Default = $true })
    if ($answer -eq 'no') { return }
    $e.Cancel = $true
    if ($answer -eq 'yes' -and (Confirm-Stop '停止服务')) {
        $script:closeAfterStop = $true
        Invoke-Operation 'stopping' 'stop'
    }
})

# The UI timer only reads snapshots; everything slow happens on the background threads.
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120
$timer.Add_Tick({
    try {
        Complete-Ops
        Grant-ConsoleIcons
        $version = $shared.Version
        if ($version -ne $script:seenVersion -or [Environment]::TickCount - $script:lastPaint -gt 1000) {
            $script:seenVersion = $version
            $script:lastPaint = [Environment]::TickCount
            Update-View
        }
    }
    catch { Set-Text $detailLabel ('刷新界面时出错：' + $_.Exception.Message) }
})

$poller = [powershell]::Create()
[void]$poller.AddScript('param($shared)' + "`r`n" + $common + "`r`n" + $pollLoop).AddArgument($shared)
$form.Add_Shown({
    $form.ActiveControl = $toggleButton
    Update-View
    $script:pollHandle = $poller.BeginInvoke()
    $timer.Start()
})

[void]$form.ShowDialog()
$timer.Stop()
$shared.Quit = $true
[void]$shared.Wake.Set()
if ($script:pollHandle) { [void]$script:pollHandle.AsyncWaitHandle.WaitOne(3000) }
$poller.Dispose()
$mutex.ReleaseMutex()
