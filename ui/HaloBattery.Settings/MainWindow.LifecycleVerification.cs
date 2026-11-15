using System.Diagnostics;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyWindowLifecycle(string destination,List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new {name,passed});
            if(!passed)throw new InvalidOperationException("Fixture check failed: "+name);
        }
        Check("real model and selected page prepared before first show",firstPreparedView is {PageReady:true,LabelCount:>0,VisibleBeforeShow:false} && firstPreparedView.Page==State.Page);
        Check("saved rail was applied before first visible layout",firstPreparedView!.RailExpanded==initialWindowState.RailExpanded && firstPreparedView.RailWidth==(initialWindowState.RailExpanded?228:64));
        Check("normal window was sized before first show",firstPreparedView!.Width>0 && firstPreparedView.Height>0);
        if(initialWindowState.X is not null && !initialWindowState.Maximized)
            Check("saved position applied before first show",firstPreparedView!.X==normalWindowState.X && firstPreparedView.Y==normalWindowState.Y);
        Navigate("devices",false);
        root.UpdateLayout();
        await Task.Delay(80);
        var originalHwnd=hwnd;
        var retainedFrame=frame.Content;
        var retainedRoot=root.XamlRoot;
        var retainedCards=cards.ToArray();
        var scroll=(frame.Content as Page)?.Content as ScrollViewer;
        scroll?.ChangeView(null,Math.Min(90,scroll.ScrollableHeight),null,true);
        await Task.Delay(80);
        var offset=scroll?.VerticalOffset;
        var position=AppWindow.Position;
        var size=AppWindow.Size;
        var railState=expanded;
        var timings=new List<double>();
        for(var iteration=0;iteration<8;iteration++)
        {
            await HideSettingsWindowAsync();
            Check("hidden window retains HWND and page "+iteration,IsSettingsWindowHidden && !AppWindow.IsVisible && hwnd==originalHwnd && ReferenceEquals(frame.Content,retainedFrame));
            var rendered=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            EventHandler<object>? rendering=null;
            rendering=(_,_)=>{CompositionTarget.Rendering-=rendering;rendered.TrySetResult(true);};
            CompositionTarget.Rendering+=rendering;
            var watch=Stopwatch.StartNew();
            ShowSettingsWindow(null,null);
            try { await rendered.Task.WaitAsync(TimeSpan.FromSeconds(2)); }
            finally { CompositionTarget.Rendering-=rendering; }
            timings.Add(watch.Elapsed.TotalMilliseconds);
            Check("reopen shows retained controls without reconstruction "+iteration,
                AppWindow.IsVisible && !IsSettingsWindowHidden && hwnd==originalHwnd && ReferenceEquals(frame.Content,retainedFrame) &&
                ReferenceEquals(root.XamlRoot,retainedRoot) && cards.SequenceEqual(retainedCards));
            Check("generic reopen retains device page and initialized catalogue "+iteration,
                current=="devices" && (iconCatalog is null || iconCatalog.Current.Text.Length>0));
            Check("reopen preserves position size rail and scroll "+iteration,
                AppWindow.Position.X==position.X && AppWindow.Position.Y==position.Y &&
                AppWindow.Size.Width==size.Width && AppWindow.Size.Height==size.Height && expanded==railState &&
                (scroll is null || Math.Abs(scroll.VerticalOffset-offset!.Value)<1));
        }
        if(AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.Maximize();await Task.Delay(100);
            CaptureWindowState();
            await HideSettingsWindowAsync();ShowSettingsWindow(current,selectedDeviceKey);
            Check("maximized window reopens with same content and state",presenter.State==OverlappedPresenterState.Maximized && ReferenceEquals(frame.Content,retainedFrame));
            presenter.Minimize();await Task.Delay(80);
            ShowSettingsWindow(current,selectedDeviceKey);
            Check("minimized maximized window restores maximized",presenter.State==OverlappedPresenterState.Maximized);
            presenter.Restore();await Task.Delay(100);
        }
        await VerificationCapture(destination,"retained-window-reopened");
        checks.Add(new {name="warm show to next composition frame milliseconds",passed=true,samples=timings,maximum=timings.Max(),average=timings.Average()});
    }
}
