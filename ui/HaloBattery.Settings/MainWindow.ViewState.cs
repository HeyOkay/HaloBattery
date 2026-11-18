using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private SettingsWindowState? pendingSettingsView;
    private TaskCompletionSource<bool>? restoredSettingsView;
    private long settingsViewRestoreGeneration;

    // Called once with the latest queued show request, before building any page.
    // A normal tray reopen has no target; an explicit backend target takes priority.
    private string InitialSettingsPage(string? requestedPage,string? requestedDeviceKey)
    {
        var saved=initialWindowState;
        var page=requestedPage??saved.Page;
        if(!State.Pages.Any(item=>item.Id==page)) page="general";
        var key=requestedDeviceKey??saved.SelectedDeviceKey;
        selectedDeviceKey=State.Devices.Any(device=>device.Key==key)?key:
            State.Devices.Any(device=>device.Key==State.SelectedDeviceKey)?State.SelectedDeviceKey:State.Devices.FirstOrDefault()?.Key;
        pendingSettingsView=requestedPage is null && requestedDeviceKey is null && page==saved.Page &&
            (page is not ("devices" or "icons") || selectedDeviceKey==saved.SelectedDeviceKey)?saved:null;
        restoredSettingsView=new(TaskCreationOptions.RunContinuationsAsynchronously);
        if(pendingSettingsView is null) restoredSettingsView.TrySetResult(true);
        return page;
    }

    private void CaptureSettingsViewState()
    {
        if(!pagesInitialized || frame.Content is not Page { Content:ScrollViewer scroll } || pendingSettingsView is not null) return;
        var offset=double.IsFinite(scroll.VerticalOffset)?Math.Clamp(scroll.VerticalOffset,0,10000000):0;
        // Card order is stable within a page. Iconify's temporary browsing panel
        // always starts collapsed; its query, gallery and cache are never saved.
        var expansion=new string(cards.Take(256).Select(card=>card.Expandable && card.IsExpanded &&
            !ReferenceEquals(card,iconCatalog?.Panel)?'1':'0').ToArray());
        var next=normalWindowState with { Page=current,SelectedDeviceKey=selectedDeviceKey,ScrollOffset=offset,ExpandedCards=expansion };
        if(next==normalWindowState) return;
        normalWindowState=next;
        windowStateVersion++;
        windowStateTimer.Stop();
        if(!closing && !closingWindowState && !quittingApplication) windowStateTimer.Start();
    }

    private void RestoreSettingsViewStateAfterNavigation()
    {
        if(pendingSettingsView is not { } saved) return;
        if(current!=saved.Page || frame.Content is not Page { Content:ScrollViewer scroll })
        {
            CancelSettingsViewRestore();
            return;
        }
        if(saved.ExpandedCards.Length==cards.Count)
            for(var index=0;index<cards.Count;index++)
                if(cards[index].Expandable && !ReferenceEquals(cards[index],iconCatalog?.Panel))
                    cards[index].IsExpanded=saved.ExpandedCards[index]=='1';
        var generation=++settingsViewRestoreGeneration;
        _=RestoreSettingsScrollAsync(scroll,saved.ScrollOffset,generation);
    }

    private async Task RestoreSettingsScrollAsync(ScrollViewer scroll,double offset,long generation)
    {
        // The hidden first page must have its actual viewport before ChangeView.
        // Retry through initial card measurement; all work stays on the XAML thread.
        for(var attempt=0;attempt<50;attempt++)
        {
            if(generation!=settingsViewRestoreGeneration) return;
            if(closing) { restoredSettingsView?.TrySetResult(false);return; }
            if(scroll.IsLoaded && scroll.ViewportHeight>0)
            {
                scroll.UpdateLayout();
                var target=Math.Min(offset,scroll.ScrollableHeight);
                scroll.ChangeView(null,target,null,true);
                if(Math.Abs(scroll.VerticalOffset-target)<=1/Scale)
                {
                    pendingSettingsView=null;
                    restoredSettingsView?.TrySetResult(true);
                    CaptureSettingsViewState();
                    return;
                }
            }
            await Task.Delay(20);
        }
        // A removed device or shorter page can have a smaller extent. Always
        // release startup after a bounded wait rather than strand the splash.
        if(generation!=settingsViewRestoreGeneration) return;
        scroll.ChangeView(null,Math.Min(offset,scroll.ScrollableHeight),null,true);
        pendingSettingsView=null;
        restoredSettingsView?.TrySetResult(false);
        CaptureSettingsViewState();
    }

    private void CancelSettingsViewRestore()
    {
        settingsViewRestoreGeneration++;
        pendingSettingsView=null;
        restoredSettingsView?.TrySetResult(false);
    }

    internal Task WaitForRestoredSettingsViewAsync()=>restoredSettingsView is { } completion?completion.Task:Task.CompletedTask;
}
