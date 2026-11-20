using Microsoft.UI.Xaml.Controls;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifySettingsViewPersistence(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new { name,passed });
            if(!passed) throw new InvalidOperationException("Fixture check failed: "+name);
        }
        var originalPage=current;
        var originalSelection=selectedDeviceKey;
        var originalInitial=initialWindowState;
        try
        {
            Navigate("advanced",false);
            foreach(var card in cards.Where(card=>card.Expandable)) card.IsExpanded=true;
            await Task.Delay(80);
            var scroll=(ScrollViewer)((Page)frame.Content).Content;
            scroll.UpdateLayout();
            scroll.ChangeView(null,Math.Min(180,scroll.ScrollableHeight),null,true);
            await Task.Delay(80);
            CaptureWindowState();
            var remembered=normalWindowState;
            await FlushWindowStateAsync();
            Check("last page and selected device are included in native state",remembered.Page=="advanced" && remembered.SelectedDeviceKey==selectedDeviceKey &&
                remembered.ExpandedCards.Length==cards.Count && Math.Abs(remembered.ScrollOffset-scroll.VerticalOffset)<=1/Scale);
            Check("native view snapshot is durably saved",await windowStateStore!.LoadAsync()==remembered);

            Navigate("general",false);
            initialWindowState=remembered;
            var restoredPage=InitialSettingsPage(null,null);
            Navigate(restoredPage,false);
            await WaitForRestoredSettingsViewAsync();
            scroll=(ScrollViewer)((Page)frame.Content).Content;
            Check("cold-view helper restores saved page device and scroll",current==remembered.Page && selectedDeviceKey==remembered.SelectedDeviceKey &&
                Math.Abs(scroll.VerticalOffset-Math.Min(remembered.ScrollOffset,scroll.ScrollableHeight))<=1/Scale);
            Check("cold-view helper restores expanded cards",cards.Select((card,index)=>!card.Expandable ||
                card.IsExpanded==(remembered.ExpandedCards[index]=='1')).All(value=>value));

            var explicitDevice=State.Devices.LastOrDefault()?.Key;
            var explicitPage=InitialSettingsPage("devices",explicitDevice);
            Check("explicit backend page and device override saved view",explicitPage=="devices" && selectedDeviceKey==explicitDevice && pendingSettingsView is null);
            initialWindowState=remembered with { Page="devices",SelectedDeviceKey="missing:fixture-device",ScrollOffset=500 };
            var fallbackPage=InitialSettingsPage(null,null);
            Check("missing saved device falls back before page creation",fallbackPage=="devices" &&
                (selectedDeviceKey is null || State.Devices.Any(device=>device.Key==selectedDeviceKey)) && pendingSettingsView is null);
        }
        finally
        {
            CancelSettingsViewRestore();
            initialWindowState=originalInitial;
            selectedDeviceKey=originalSelection;
            Navigate(originalPage,false);
        }
    }
}
