using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern nint GetForegroundWindow();

    private async Task VerifySettingsActivation(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new {name,passed});
            if(!passed)throw new InvalidOperationException("Fixture check failed: "+name);
        }
        async Task<bool> ForegroundIs(nint target,int attempts=50)
        {
            for(var attempt=0;attempt<attempts;attempt++)
            {
                if(GetForegroundWindow()==target)return true;
                await Task.Delay(20);
            }
            return false;
        }
        var page=frame.Content;
        var selection=selectedDeviceKey;
        var originalHandle=hwnd;
        // Windows deliberately refuses foreground activation from an unattended
        // background launcher. Establish real foreground input before checking
        // the handoff between this fixture's own two windows.
        var prompt=Text("Foreground test - click anywhere in this window",14);
        prompt.HorizontalAlignment=HorizontalAlignment.Center;
        prompt.VerticalAlignment=VerticalAlignment.Top;prompt.Margin=new(0,8,0,0);
        Grid.SetRow(prompt,1);root.Children.Add(prompt);
        try {Check("interactive fixture receives foreground",await ForegroundIs(hwnd,1500));}
        finally {root.Children.Remove(prompt);}
        var other=new Window {Title="HaloBattery activation fixture",Content=new Grid()};
        other.AppWindow.Resize(new SizeInt32(320,240));
        var otherHandle=WinRT.Interop.WindowNative.GetWindowHandle(other);
        async Task CoverSettings()
        {
            other.Activate();SetForegroundWindow(otherHandle);
            Check("another fixture window owns foreground",await ForegroundIs(otherHandle));
        }
        try
        {
            await CoverSettings();
            ShowSettingsWindow(current,selection);
            Check("open Settings raised above another window",await ForegroundIs(hwnd));
            Check("foreground activation reuses native window and page",hwnd==originalHandle && ReferenceEquals(frame.Content,page) && selectedDeviceKey==selection);
            if(AppWindow.Presenter is OverlappedPresenter presenter)
            {
                presenter.Minimize();
                await CoverSettings();
                ShowSettingsWindow(current,selection);
                Check("minimized Settings restores and becomes foreground",await ForegroundIs(hwnd) && presenter.State==OverlappedPresenterState.Restored);
                presenter.Maximize();CaptureWindowState();
                presenter.Minimize();
                await CoverSettings();
                ShowSettingsWindow(current,selection);
                Check("maximized Settings retains maximize intent on activation",await ForegroundIs(hwnd) && presenter.State==OverlappedPresenterState.Maximized);
                presenter.Restore();
            }
            await CoverSettings();
            var previousRequest=showAfterStartup;
            pagesInitializing=true;
            try
            {
                ShowSettingsWindow(null,null);
                Check("show during startup handoff also activates window",await ForegroundIs(hwnd));
            }
            finally {pagesInitializing=false;showAfterStartup=previousRequest;}
        }
        finally {other.Close();}
    }
}
