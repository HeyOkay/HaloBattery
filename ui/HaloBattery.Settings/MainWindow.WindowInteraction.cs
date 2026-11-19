using Microsoft.UI.Xaml;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private bool windowChromePending,windowMinimumPending;
    private XamlRoot? windowLayoutRoot;
    private double windowLayoutScale;
#if HALO_BATTERY_VERIFICATION
    private WindowInteractionMetrics? windowInteractionMetrics;
    private sealed class WindowInteractionMetrics
    {
        internal long LayoutCalls,PageLayouts,ChromeBatches,NativePositionEvents,RailFrames;
    }
#endif

    private void QueueWindowChromeRefresh(bool updateMinimum=false)
    {
        if(closing) return;
        windowMinimumPending|=updateMinimum;
        if(IsSettingsWindowHidden) return;
        if(windowChromePending) return;
        windowChromePending=true;
        if(!DispatcherQueue.TryEnqueue(()=>
        {
            windowChromePending=false;
            if(closing) { windowMinimumPending=false;return; }
            if(IsSettingsWindowHidden) return;
            var minimum=windowMinimumPending;windowMinimumPending=false;
#if HALO_BATTERY_VERIFICATION
            if(windowInteractionMetrics is {} metrics) metrics.ChromeBatches++;
#endif
            UpdateMaximizeButton();
            if(minimum) UpdateMinimumWidth();
            else { PositionPill();UpdateDragRegion(); }
        })) { windowChromePending=false;windowMinimumPending=false; }
    }

    private void AttachWindowLayoutRoot()
    {
        if(ReferenceEquals(windowLayoutRoot,root.XamlRoot)) return;
        DetachWindowLayoutRoot();
        windowLayoutRoot=root.XamlRoot;
        if(windowLayoutRoot is null) return;
        windowLayoutScale=windowLayoutRoot.RasterizationScale;
        windowLayoutRoot.Changed+=OnWindowLayoutRootChanged;
    }

    private void DetachWindowLayoutRoot()
    {
        if(windowLayoutRoot is not null) windowLayoutRoot.Changed-=OnWindowLayoutRootChanged;
        windowLayoutRoot=null;
    }

    private void OnWindowLayoutRootChanged(XamlRoot sender,XamlRootChangedEventArgs args)
    {
        if(closing || sender.RasterizationScale==windowLayoutScale) return;
        windowLayoutScale=sender.RasterizationScale;
        CaptureWindowState();
        QueueWindowChromeRefresh(updateMinimum:true);
    }
}
