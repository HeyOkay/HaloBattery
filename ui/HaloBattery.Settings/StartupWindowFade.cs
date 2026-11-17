using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml.Media;

namespace HaloBattery.Settings;

/// <summary>Temporary whole-HWND opacity; restore native styles after entrance.</summary>
internal sealed class StartupWindowFade : IDisposable
{
    private const int ExtendedStyle=-20,Layered=0x00080000;
    private readonly nint window;
    private readonly bool wasLayered;
    private readonly byte targetAlpha=255;
    private readonly uint originalKey,originalFlags;
    private readonly TaskCompletionSource<bool> completed=new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly Stopwatch clock=new();
    private EventHandler<object>? rendering;
    private DispatcherQueueTimer? deadline;
    private bool disposed;
    internal bool HadIntermediateAlpha { get; private set; }
    internal int Frames { get; private set; }
    internal bool NativeStyleRestored { get; private set; }
    internal bool FinishedNormally { get; private set; }

    internal StartupWindowFade(nint hwnd)
    {
        window=hwnd;
        var style=GetWindowLongW(window,ExtendedStyle);
        wasLayered=(style&Layered)!=0;
        if(wasLayered && !GetLayeredWindowAttributes(window,out originalKey,out targetAlpha,out originalFlags))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        if(!wasLayered)
        {
            SetWindowLongW(window,ExtendedStyle,style|Layered);
            if((GetWindowLongW(window,ExtendedStyle)&Layered)==0)throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        // Applied while still cloaked: the first exposed HWND frame starts at
        // zero opacity, including Mica, caption controls and the splash logo.
        // https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes
        if(!SetLayeredWindowAttributes(window,0,0,2))
        {
            RestoreNativeStyle();
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    internal Task RunAsync(DispatcherQueue queue)
    {
        if(disposed)return completed.Task;
        clock.Start();
        rendering=(_,_)=>
        {
            var progress=Math.Clamp(clock.Elapsed.TotalMilliseconds/200,0,1);
            var eased=progress*progress*(3-2*progress);
            var alpha=(byte)Math.Round(targetAlpha*eased);
            if(!SetLayeredWindowAttributes(window,0,alpha,2)){Dispose();return;}
            Frames++;
            HadIntermediateAlpha|=alpha>0 && alpha<targetAlpha;
            if(progress>=1){FinishedNormally=true;Dispose();}
        };
        CompositionTarget.Rendering+=rendering;
        // A minimized/occluded render loop must not leave the HWND transparent.
        deadline=queue.CreateTimer();deadline.Interval=TimeSpan.FromMilliseconds(500);
        deadline.Tick+=OnDeadline;deadline.Start();
        return completed.Task;
    }

    private void OnDeadline(DispatcherQueueTimer sender,object args)=>Dispose();

    private void RestoreNativeStyle()
    {
        if(!IsWindow(window))return;
        if(wasLayered)
            SetLayeredWindowAttributes(window,originalKey,targetAlpha,originalFlags);
        else
        {
            SetLayeredWindowAttributes(window,0,255,2);
            // Restore only the bit we own; preserve unrelated changes made by
            // the presenter while the short entrance animation was running.
            SetWindowLongW(window,ExtendedStyle,GetWindowLongW(window,ExtendedStyle)&~Layered);
        }
        NativeStyleRestored=((GetWindowLongW(window,ExtendedStyle)&Layered)!=0)==wasLayered;
    }

    public void Dispose()
    {
        if(disposed)return;
        disposed=true;
        if(rendering is not null){CompositionTarget.Rendering-=rendering;rendering=null;}
        if(deadline is not null){deadline.Stop();deadline.Tick-=OnDeadline;deadline=null;}
        RestoreNativeStyle();
        completed.TrySetResult(true);
    }

    [DllImport("user32.dll",ExactSpelling=true)]private static extern int GetWindowLongW(nint hwnd,int index);
    [DllImport("user32.dll",ExactSpelling=true,SetLastError=true)]private static extern int SetWindowLongW(nint hwnd,int index,int value);
    [DllImport("user32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetLayeredWindowAttributes(nint hwnd,uint colorKey,byte alpha,uint flags);
    [DllImport("user32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetLayeredWindowAttributes(nint hwnd,out uint colorKey,out byte alpha,out uint flags);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)]private static extern bool IsWindow(nint hwnd);
}
