using System.ComponentModel;
using System.Runtime.InteropServices;

namespace HaloBattery.Settings;

/// <summary>Prepare the native window before exposing its first XAML frame.</summary>
internal sealed partial class StartupWindowBackground : IDisposable
{
    private const uint EraseBackground=0x0014,NonClientDestroy=0x0082;
    private const nuint SubclassId=0x48425350;
    private readonly SubclassProc callback;
    private nint window,brush;
    internal uint ColorRef { get; private set; }
    internal bool Attached=>window!=0;
    internal bool Cloaked { get; private set; }

    internal StartupWindowBackground(nint hwnd,bool dark)
    {
        callback=WindowProc;
        UpdateTheme(dark);
        if(!SetWindowSubclass(hwnd,callback,SubclassId,0))
        {
            DeleteObject(brush);brush=0;
            throw new Win32Exception(Marshal.GetLastWin32Error(),"Unable to prepare the window startup background.");
        }
        window=hwnd;
        // A GDI fill cannot conceal the compositor's intermediate theme frames.
        // DWM continues composing a cloaked HWND, allowing WinUI to prepare Mica
        // before the first frame is exposed to the user.
        // https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
        var enabled=1;
        Cloaked=DwmSetWindowAttribute(hwnd,13,ref enabled,sizeof(int))>=0;
    }

    internal void Reveal()
    {
        if(window==0 || !Cloaked)return;
        var disabled=0;
        Marshal.ThrowExceptionForHR(DwmSetWindowAttribute(window,13,ref disabled,sizeof(int)));
        Cloaked=false;
    }

#if HALO_BATTERY_VERIFICATION
    internal static bool IsAppCloaked(nint hwnd)=>DwmGetWindowAttribute(hwnd,14,out var flags,sizeof(int))>=0 && (flags&1)!=0;
#endif

    internal void UpdateTheme(bool dark)
    {
        var color=dark?0x00202020u:0x00F3F3F3u;
        if(brush!=0 && ColorRef==color)return;
        var replacement=CreateSolidBrush(color);
        if(replacement==0)throw new Win32Exception(Marshal.GetLastWin32Error());
        var previous=brush;brush=replacement;ColorRef=color;
        if(previous!=0)DeleteObject(previous);
    }

    private nint WindowProc(nint hwnd,uint message,nint wParam,nint lParam,nuint id,nuint data)
    {
        // Fill only this HWND, not WinUI's shared window-class brush. The supplied
        // erase DC belongs to Windows and must not be released by this callback.
        // https://learn.microsoft.com/windows/win32/winmsg/wm-erasebkgnd
        if(message==EraseBackground && wParam!=0 && brush!=0 && GetClientRect(hwnd,out var bounds) && FillRect(wParam,ref bounds,brush)!=0)return 1;
        if(message==NonClientDestroy)Dispose();
        return DefSubclassProc(hwnd,message,wParam,lParam);
    }

#if HALO_BATTERY_VERIFICATION
    // A fixture-only offscreen DC verifies the actual HWND callback's paint color,
    // independently of XAML brushes. It never samples the user's desktop.
    internal uint VerifyPaintColor()
    {
        if(window==0)throw new InvalidOperationException("Startup paint hook is not attached.");
        var dc=CreateCompatibleDC(0);
        if(dc==0)throw new Win32Exception(Marshal.GetLastWin32Error());
        var bitmap=CreateBitmap(1,1,1,32,0);
        nint previous=0;
        try
        {
            if(bitmap==0)throw new Win32Exception(Marshal.GetLastWin32Error());
            previous=SelectObject(dc,bitmap);
            if(previous==0 || previous==-1)throw new Win32Exception(Marshal.GetLastWin32Error());
            if(SendMessageW(window,EraseBackground,dc,0)==0)throw new InvalidOperationException("Native background was not painted.");
            return GetPixel(dc,0,0);
        }
        finally
        {
            if(previous!=0 && previous!=-1)SelectObject(dc,previous);
            if(bitmap!=0)DeleteObject(bitmap);
            DeleteDC(dc);
        }
    }

#endif

    public void Dispose()
    {
        if(window!=0){RemoveWindowSubclass(window,callback,SubclassId);window=0;}
        if(brush!=0){DeleteObject(brush);brush=0;}
    }

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate nint SubclassProc(nint hwnd,uint message,nint wParam,nint lParam,nuint id,nuint data);
    [StructLayout(LayoutKind.Sequential)]private struct NativeRect { public int Left,Top,Right,Bottom; }
    [DllImport("comctl32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowSubclass(nint hwnd,SubclassProc callback,nuint id,nuint data);
    [DllImport("comctl32.dll")] [return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool RemoveWindowSubclass(nint hwnd,SubclassProc callback,nuint id);
    [DllImport("comctl32.dll")]private static extern nint DefSubclassProc(nint hwnd,uint message,nint wParam,nint lParam);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)]private static extern bool GetClientRect(nint hwnd,out NativeRect bounds);
    [DllImport("user32.dll")]private static extern int FillRect(nint dc,ref NativeRect bounds,nint brush);
    [DllImport("gdi32.dll",SetLastError=true)]private static extern nint CreateSolidBrush(uint color);
    [DllImport("gdi32.dll")] [return:MarshalAs(UnmanagedType.Bool)]private static extern bool DeleteObject(nint handle);
#if HALO_BATTERY_VERIFICATION
    [DllImport("user32.dll",ExactSpelling=true)]private static extern nint SendMessageW(nint hwnd,uint message,nint wParam,nint lParam);
    [DllImport("gdi32.dll",SetLastError=true)]private static extern nint CreateCompatibleDC(nint dc);
    [DllImport("gdi32.dll",SetLastError=true)]private static extern nint CreateBitmap(int width,int height,uint planes,uint bitsPerPixel,nint bits);
    [DllImport("gdi32.dll",SetLastError=true)]private static extern nint SelectObject(nint dc,nint bitmap);
    [DllImport("gdi32.dll")]private static extern uint GetPixel(nint dc,int x,int y);
    [DllImport("gdi32.dll")] [return:MarshalAs(UnmanagedType.Bool)]private static extern bool DeleteDC(nint dc);
#endif
    [DllImport("dwmapi.dll")]private static extern int DwmSetWindowAttribute(nint hwnd,uint attribute,ref int value,int size);
#if HALO_BATTERY_VERIFICATION
    [DllImport("dwmapi.dll")]private static extern int DwmGetWindowAttribute(nint hwnd,uint attribute,out int value,int size);
#endif
}
