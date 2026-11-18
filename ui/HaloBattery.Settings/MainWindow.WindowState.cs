using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private readonly DispatcherTimer windowStateTimer=new() { Interval=TimeSpan.FromMilliseconds(400) };
    private WindowStateStore? windowStateStore;
    private SettingsWindowState initialWindowState=new(),normalWindowState=new();
    private SettingsWindowState? savedWindowState;
    private long windowStateVersion,savedWindowStateVersion;
    private Task<bool>? windowStateWrite;
    private bool windowStateRestored,closingWindowState,allowWindowClose,quittingApplication,pagesInitializing;
    private bool applyingWindowState,initialPresenterApplied;
    internal bool IsSettingsWindowHidden { get; private set; }=true;

    private void InitializeWindowState(string directory,SettingsWindowState state)
    {
        // Bootstrap loaded this file before constructing a window. Applying it
        // here gives the first layout its final rail width and normal bounds.
        windowStateStore=new WindowStateStore(directory);
        RestoreWindowState(state);
        windowStateTimer.Tick+=async (_,_)=> { windowStateTimer.Stop(); await SaveWindowStateAsync(); };
        AppWindow.Closing+=OnSettingsWindowClosing;
        Closed+=(_,_)=> { windowStateTimer.Stop(); AppWindow.Closing-=OnSettingsWindowClosing; };
    }

    private void RestoreWindowState(SettingsWindowState state)
    {
        applyingWindowState=true;
        try
        {
            initialWindowState=state;
            expanded=state.RailExpanded;
            StopRail();
            rail.Width=new(expanded?228:64);
            var monitor=FindWindowMonitor(state);
            var work=monitor.Work;
            // Move the still-hidden HWND onto the destination monitor first.
            // GetDpiForWindow then reports that monitor's current DPI, including
            // changes since the preferences were saved. AppWindow coordinates
            // are physical screen pixels, not DIPs or WINDOWPLACEMENT workspace
            // coordinates (which would creep when a taskbar is at the top/left).
            // https://learn.microsoft.com/windows/windows-app-sdk/api/winrt/microsoft.ui.windowing.appwindow.moveandresize
            // https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getdpiforwindow
            AppWindow.MoveAndResize(ConstrainWindowBounds(state,work,state.Dpi/96.0,MinimumWindowWidthDip,AppWindow.Position));
            var dpi=(int)GetDpiForWindow(hwnd);
            if(dpi<48 || dpi>768) dpi=96;
            var scale=dpi/96.0;
            var bounds=ConstrainWindowBounds(state,work,scale,MinimumWindowWidthDip,AppWindow.Position);
            AppWindow.MoveAndResize(bounds);
            normalWindowState=state with { Width=(int)Math.Round(bounds.Width/scale),Height=(int)Math.Round(bounds.Height/scale),
                RailExpanded=expanded,X=bounds.X,Y=bounds.Y,Dpi=dpi,Monitor=monitor.Name };
            savedWindowState=state;
            windowStateRestored=true;
        }
        finally { applyingWindowState=false; }
    }

    private static RectInt32 ConstrainWindowBounds(SettingsWindowState state,RectInt32 work,double scale,double minimumWidth,PointInt32 fallback)
    {
        var width=Math.Min(work.Width,Math.Max((int)Math.Ceiling(minimumWidth*scale),(int)Math.Round(state.Width*scale)));
        var height=Math.Min(work.Height,Math.Max((int)Math.Ceiling(560*scale),(int)Math.Round(state.Height*scale)));
        return new RectInt32(Math.Clamp(state.X??fallback.X,work.X,work.X+work.Width-width),
            Math.Clamp(state.Y??fallback.Y,work.Y,work.Y+work.Height-height),width,height);
    }

    // Called after the splash tree is attached, immediately before first show.
    // Maximize may show an HWND, so it must never run in the constructor.
    internal void PrepareSettingsWindowForShow()
    {
        if(closing || closingWindowState) return;
        applyingWindowState=true;
        try
        {
            if(AppWindow.Presenter is OverlappedPresenter presenter)
            {
                if(!initialPresenterApplied && normalWindowState.Maximized) presenter.Maximize();
                else if(presenter.State==OverlappedPresenterState.Minimized)
                {
                    if(normalWindowState.Maximized) presenter.Maximize();
                    else presenter.Restore();
                }
            }
            initialPresenterApplied=true;
            IsSettingsWindowHidden=false;
        }
        finally { applyingWindowState=false; }
    }

    private void CaptureWindowState()
    {
        if(!windowStateRestored || applyingWindowState || IsSettingsWindowHidden || closing || closingWindowState || quittingApplication) return;
        CaptureSettingsViewState();
        var next=normalWindowState with { RailExpanded=expanded };
        if(AppWindow.Presenter is OverlappedPresenter presenter)
        {
            // Native flags also guard the short transition before WinUI updates
            // presenter state. Minimize retains the previous maximize intent.
            if(presenter.State==OverlappedPresenterState.Minimized || IsIconic(hwnd)) { }
            else if(presenter.State==OverlappedPresenterState.Maximized || IsZoomed(hwnd))
            {
                // A maximized window can move displays with Win+Shift+Arrow.
                // Preserve its normal bounds but remember the display on which
                // to maximize again after a process restart.
                var dpi=(int)GetDpiForWindow(hwnd);
                next=next with { Maximized=true,Monitor=CurrentWindowMonitor().Name,
                    Dpi=dpi is >=48 and <=768?dpi:next.Dpi };
            }
            else if(presenter.State==OverlappedPresenterState.Restored)
            {
                var dpi=(int)GetDpiForWindow(hwnd);
                var scale=dpi/96.0;
                if(dpi>=48 && dpi<=768)
                {
                    var width=(int)Math.Round(AppWindow.Size.Width/scale);
                    var height=(int)Math.Round(AppWindow.Size.Height/scale);
                    var position=AppWindow.Position;
                    if(width>=320 && height>=240 && width<=16384 && height<=16384 &&
                       position.X is >= -1000000 and <= 1000000 && position.Y is >= -1000000 and <= 1000000)
                        next=next with { Width=width,Height=height,RailExpanded=expanded,X=position.X,Y=position.Y,Dpi=dpi,Monitor=CurrentWindowMonitor().Name,Maximized=false };
                }
            }
        }
        if(next!=normalWindowState) { normalWindowState=next;windowStateVersion++; }
        windowStateTimer.Stop();
        if(normalWindowState!=savedWindowState) windowStateTimer.Start();
    }

    private Task<bool> SaveWindowStateAsync()
    {
        if(windowStateWrite is not null) return windowStateWrite;
        if(!windowStateRestored || normalWindowState==savedWindowState) return Task.FromResult(true);
        if(windowStateStore is null) return Task.FromResult(false);
        var completion=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        windowStateWrite=completion.Task;
        _=WriteWindowStateAsync(normalWindowState,windowStateVersion,completion);
        return completion.Task;
    }

    private async Task WriteWindowStateAsync(SettingsWindowState pending,long version,TaskCompletionSource<bool> completion)
    {
        var success=false;
        try
        {
            await windowStateStore!.SaveAsync(pending);
            savedWindowState=pending;
            savedWindowStateVersion=version;
            success=true;
        }
        catch(Exception error) when(error is IOException or UnauthorizedAccessException or ArgumentException)
        { Debug.WriteLine(error); }
        finally
        {
            windowStateWrite=null;
            completion.TrySetResult(success);
            if(success && !closing && !closingWindowState && !quittingApplication && normalWindowState!=savedWindowState) windowStateTimer.Start();
        }
    }

    private async Task<bool> FlushWindowStateAsync()
    {
        if(windowStateWrite is not null && !await windowStateWrite) return false;
        while(windowStateRestored && normalWindowState!=savedWindowState)
            if(!await SaveWindowStateAsync()) return false;
        return true;
    }

    private async Task SendUiCommandAsync(string command)
    {
        if(command!="quit") { Bridge.SendCommand(command,new { });return; }
        if(quittingApplication || closing || closingWindowState) return;
        CaptureWindowState();
        quittingApplication=true;
        windowStateTimer.Stop();
        try
        {
            await FlushWindowStateAsync();
            if(!closing && !closingWindowState) Bridge.SendCommand("quit",new { });
        }
        finally
        {
            quittingApplication=false;
            if(!closing && !closingWindowState && normalWindowState!=savedWindowState) windowStateTimer.Start();
        }
    }

    private void OnSettingsWindowClosing(AppWindow sender,AppWindowClosingEventArgs args)
    {
        if(allowWindowClose) return;
        args.Cancel=true;
        Close();
    }

    // Closing releases the companion process. Its native view state is restored
    // after the next launch's splash, independently of Python's configuration.
    public new void Close()=>_=ShutdownSettingsWindowAsync();

    internal async Task HideSettingsWindowAsync()
    {
        if(closing || closingWindowState) return;
        CaptureWindowState();
        windowStateTimer.Stop();
        IsSettingsWindowHidden=true;
        AppWindow.Hide();
        OnSettingsWindowHidden();
        await FlushWindowStateAsync();
    }

    internal async Task ShutdownSettingsWindowAsync()
    {
        if(closingWindowState || closing) return;
        CaptureWindowState();
        closingWindowState=true;
        windowStateTimer.Stop();
        try
        {
            await Task.Yield(); // Return from a canceled native Closing event first.
            await PrepareSettingsExitAsync();
            CaptureSettingsViewState();
            await FlushWindowStateAsync();
        }
        finally { allowWindowClose=true;if(!closing) base.Close(); }
    }

    private sealed record WindowMonitor(string Name,RectInt32 Work);
    private WindowMonitor FindWindowMonitor(SettingsWindowState state)
    {
        WindowMonitor? match=null;
        if(state.Monitor.Length>0)
        {
            MonitorEnumProc callback=(nint monitor,nint device,ref NativeWindowRect rect,nint data)=>
            {
                if(TryWindowMonitor(monitor,out var candidate) && candidate.Name==state.Monitor) match=candidate;
                return match is null;
            };
            EnumDisplayMonitors(0,0,callback,0);
        }
        if(match is not null) return match;
        if(state.X is int x && state.Y is int y)
        {
            var rect=new NativeWindowRect { Left=x,Top=y,Right=x+(int)Math.Round(state.Width*state.Dpi/96.0),Bottom=y+(int)Math.Round(state.Height*state.Dpi/96.0) };
            if(TryWindowMonitor(MonitorFromRect(ref rect,2),out var nearest)) return nearest;
        }
        return CurrentWindowMonitor();
    }

    private WindowMonitor CurrentWindowMonitor()
    {
        if(TryWindowMonitor(MonitorFromWindow(hwnd,2),out var monitor)) return monitor;
        return new("",DisplayArea.GetFromWindowId(AppWindow.Id,DisplayAreaFallback.Nearest).WorkArea);
    }

    private static bool TryWindowMonitor(nint monitor,out WindowMonitor value)
    {
        var info=new NativeMonitorInfo { Size=Marshal.SizeOf<NativeMonitorInfo>(),Device="" };
        if(monitor!=0 && GetMonitorInfo(monitor,ref info) && info.Work.Right>info.Work.Left && info.Work.Bottom>info.Work.Top)
        {
            value=new(info.Device,new RectInt32(info.Work.Left,info.Work.Top,info.Work.Right-info.Work.Left,info.Work.Bottom-info.Work.Top));
            return true;
        }
        value=new("",default);
        return false;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeWindowRect { internal int Left,Top,Right,Bottom; }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
    private struct NativeMonitorInfo
    {
        internal int Size;
        internal NativeWindowRect Monitor,Work;
        internal uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] internal string Device;
    }
    private delegate bool MonitorEnumProc(nint monitor,nint device,ref NativeWindowRect rect,nint data);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool EnumDisplayMonitors(nint device,nint clip,MonitorEnumProc callback,nint data);
    [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="GetMonitorInfoW")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool GetMonitorInfo(nint monitor,ref NativeMonitorInfo info);
    [DllImport("user32.dll")] private static extern nint MonitorFromRect(ref NativeWindowRect rect,uint flags);
    [DllImport("user32.dll")] private static extern nint MonitorFromWindow(nint window,uint flags);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool IsIconic(nint window);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool IsZoomed(nint window);
}
