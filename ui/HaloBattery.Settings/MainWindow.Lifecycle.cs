using Microsoft.UI.Xaml;
using System.Runtime.InteropServices;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
#if HALO_BATTERY_VERIFICATION
    private PreparedWindowView? firstPreparedView;
    private sealed record PreparedWindowView(int X,int Y,int Width,int Height,bool RailExpanded,double RailWidth,string Page,bool PageReady,int LabelCount,bool VisibleBeforeShow);
#endif

    internal void ShowSettingsWindow(string? page,string? deviceKey,long? requestId=null)
    {
        if(IsClosingSettings) return;
        if(pagesInitializing)
        {
            ActivateSettingsWindow();
            if(startupRevealing)
            {
                showAfterStartup=new(page,deviceKey,requestId);
                return;
            }
            pendingStartupPage=page;pendingStartupDeviceKey=deviceKey;
            startupNavigationVersion++;
            if(pagesInitialized && page is not null)
            {
                if(deviceKey is not null)selectedDeviceKey=deviceKey;
                Navigate(page,false);
            }
            // Acknowledge this show request even while loading. Python can
            // distinguish an accepted show from a request racing with close.
            Bridge.SendVisibility(true,requestId);
            return;
        }
        if(!pagesInitialized)return;
        page??=current;
        var selectionChanged=deviceKey is not null && selectedDeviceKey!=deviceKey;
        if(deviceKey is not null) selectedDeviceKey=deviceKey;
        // Reopening the same view retains its exact controls, scroll and layout.
        // Explicit requests for another page/device are prepared while hidden.
        if(current!=page || selectionChanged && page=="devices")
            Navigate(page,!IsSettingsWindowHidden);
#if HALO_BATTERY_VERIFICATION
        firstPreparedView??=new(AppWindow.Position.X,AppWindow.Position.Y,AppWindow.Size.Width,AppWindow.Size.Height,
            expanded,rail.Width.Value,current,frame.Content is not null,State.Labels.Count,AppWindow.IsVisible);
#endif
        RefreshBindings();
        ActivateSettingsWindow();
        if(previewImage is not null) StartPreview();
        Bridge.SendVisibility(true,requestId);
    }

    private void ActivateSettingsWindow()
    {
        if(IsClosingSettings)return;
        PrepareSettingsWindowForShow();
        AppWindow.Show();
        Activate();
        // The tray process transfers foreground permission on the user click.
        // Window.Activate alone is not a foreground request for background apps.
        // https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setforegroundwindow
        SetForegroundWindow(hwnd);
    }

    [DllImport("user32.dll")][return:MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(nint window);

    private void OnSettingsWindowHidden()
    {
        StopRail();
        ClosePopupSpotlights();
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
