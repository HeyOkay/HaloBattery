using Microsoft.UI.Xaml;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private long windowShowCount;
    private PreparedWindowView? firstPreparedView;
    private sealed record PreparedWindowView(int X,int Y,int Width,int Height,bool RailExpanded,double RailWidth,string Page,bool PageReady,int LabelCount,bool VisibleBeforeShow);

    internal void ShowSettingsWindow(string? page,string? deviceKey)
    {
        if(closing || !pagesInitialized) return;
        page??=current;
        var selectionChanged=deviceKey is not null && selectedDeviceKey!=deviceKey;
        if(deviceKey is not null) selectedDeviceKey=deviceKey;
        // Reopening the same view retains its exact controls, scroll and layout.
        // Explicit requests for another page/device are prepared while hidden.
        if(current!=page || selectionChanged && page=="devices")
            Navigate(page,!IsSettingsWindowHidden);
        firstPreparedView??=new(AppWindow.Position.X,AppWindow.Position.Y,AppWindow.Size.Width,AppWindow.Size.Height,
            expanded,rail.Width.Value,current,frame.Content is not null,State.Labels.Count,AppWindow.IsVisible);
        PrepareSettingsWindowForShow();
        RefreshBindings();
        AppWindow.Show();
        Activate();
        windowShowCount++;
        if(previewImage is not null) StartPreview();
        Bridge.SendVisibility(true);
    }

    private void OnSettingsWindowHidden()
    {
        StopRail();
        ResetCardSpotlights();
        ResetContentWheelObservation();
        rail.Width=new(expanded?228:64);
        foreach(var label in navLabels)label.Opacity=expanded?1:0;
        ResetPageEntry();
        ResetOverscrollForNavigation();
        wheelNavigationGuard.Reset();
        StopPageEntryWheelFilter();
        previewTimer?.Stop();
        previewPending=false;
        previewGeneration++;
        ClearHiddenCatalogSession();
        Bridge.SendVisibility(false);
    }

    private void ClearHiddenCatalogSession()
    {
        iconCatalogQueries.Clear();
        iconCatalogLicenseRequests.Clear();
        if(iconCatalog is not { } view)return;
        view.Active=false;
        InvalidateCatalogSearch(view,false);
        ClearCatalogPages(view);
        ResetCatalogSelection(view);
        view.Panel.IsExpanded=false;
        view.Search.Text="";
        view.Query="";
        view.Total=null;
        view.Start=view.NextStart=0;
        view.HasMore=false;
        view.Status.Text=T("settings.iconify_ready");
        view.Active=true;
    }
}
