using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyPopupControlSpotlights(List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var (content, scroll) = pageHosts.Single();
        var box = activeControlSpotlights.Select(surface => surface.Owner).OfType<ComboBox>()
            .OrderByDescending(control => control.Items.Count).First();
        var y = box.TransformToVisual(content).TransformPoint(new Point()).Y;
        scroll.ChangeView(null, Math.Clamp(y - 60, 0, scroll.ScrollableHeight), null, true);
        await Task.Delay(100);
        root.UpdateLayout();
        var selection = box.SelectedIndex;
        var maximumHeight = box.MaxDropDownHeight;
        box.MaxDropDownHeight = 180; // Guarantee a scrolling fixture independent of monitor placement.
        var metrics = spotlightMetrics!;
        try
        {
            box.IsDropDownOpen = true;
            for (var attempt = 0; popupSpotlightChild is null && attempt < 30; attempt++) await Task.Delay(25);
            root.UpdateLayout();
            Check("native dropdown attaches its own shared pointer scope", HasOpenPopupSpotlights && popupSpotlightChild is not null &&
                popupSpotlightHandlers.Count == 7);
            QueuePopupControlSpotlight(new Point());
            FlushCardSpotlights();
            Check("realized dropdown items register through the native item style", popupSpotlightBounds.Count > 0 &&
                popupSpotlightBounds.All(entry => entry.Surface.Owner is ComboBoxItem));
            var entry = popupSpotlightBounds.First(item =>
                IntersectRevealBounds(item.Bounds, popupSpotlightViewport) is { Width: > 0, Height: > 0 });
            var visible = IntersectRevealBounds(entry.Bounds, popupSpotlightViewport);
            var point = new Point(visible.X + visible.Width / 2, visible.Y + visible.Height / 2);
            var writes = metrics.ControlBrushWrites;
            for (var index = 0; index < 100; index++) QueuePopupControlSpotlight(point);
            Check("dropdown pointer input shares one coalesced frame", spotlightFrame is not null && metrics.ControlBrushWrites == writes);
            FlushCardSpotlights();
            Check("dropdown item reveals on its first pointer sample", entry.Surface.HasVisual && entry.Surface.IsVisible &&
                entry.Surface.SpotlightPosition is not null);
            Check("reveal retains native selection and rounded item geometry", box.SelectedIndex == selection &&
                entry.Surface.Owner.CornerRadius == new CornerRadius(15));
            writes = metrics.ControlBrushWrites;
            QueuePopupControlSpotlight(point);
            FlushCardSpotlights();
            Check("stationary dropdown pointer produces no redundant brush writes", metrics.ControlBrushWrites == writes);
            QueueCardSpotlight(new Point(200, 200));
            Check("dropdown input cannot relight the page beneath it", spotlightPointer is null && controlSpotlightLit.Count == 0);

            ResetPopupSpotlights();
            QueueInitialPopupSpotlight(new Point(-1, -1));
            Check("late popup attachment ignores a cursor outside the popup", !HasPopupSpotlightPointer && spotlightFrame is null);
            QueueInitialPopupSpotlight(point);
            Check("late popup attachment seeds the first reveal without pointer movement", HasPopupSpotlightPointer && spotlightFrame is not null);
            FlushCardSpotlights();
            Check("initial stationary popup pointer lights the real item", entry.Surface.IsVisible);

            for (var attempt = 0; !popupSpotlightScrolls.Any(control => control.ScrollableHeight > 0) && attempt < 30; attempt++) await Task.Delay(25);
            var popupScroll = popupSpotlightScrolls.FirstOrDefault(control => control.ScrollableHeight > 0);
            Check("long native dropdown retains its scrolling viewport", popupScroll is not null);
            popupScroll!.ChangeView(null, popupScroll.ScrollableHeight, null, true);
            await Task.Delay(120);
            FlushCardSpotlights();
            entry = popupSpotlightBounds.Last(item =>
                IntersectRevealBounds(item.Bounds, popupSpotlightViewport) is { Width: > 0, Height: > 0 });
            visible = IntersectRevealBounds(entry.Bounds, popupSpotlightViewport);
            point = new Point(visible.X + visible.Width / 2, visible.Y + visible.Height / 2);
            QueuePopupControlSpotlight(point);
            FlushCardSpotlights();
            Check("dropdown reveal follows scrolling and newly realized items", entry.Surface.IsVisible);

            BeginSpotlightScrollDrag(614, true);
            writes = metrics.ControlBrushWrites;
            for (var index = 0; index < 2000; index++) QueuePopupControlSpotlight(point);
            FlushCardSpotlights();
            Check("native dropdown scrollbar capture suppresses all reveal work", spotlightFrame is null &&
                popupSpotlightLit.Count == 0 && metrics.ControlBrushWrites == writes);
            EndSpotlightScrollDrag(614);
            QueuePopupControlSpotlight(point);
            FlushCardSpotlights();
            Check("dropdown reveal resumes immediately after scrollbar release", entry.Surface.IsVisible);
            var itemSurfaces = popupSpotlightBounds.Select(item => item.Surface).ToArray();
            box.IsDropDownOpen = false;
            await Task.Delay(100);
            Check("closing native dropdown clears its pointer, handlers and reveal", !HasOpenPopupSpotlights &&
                !HasPopupSpotlightPointer && popupSpotlightChild is null && popupSpotlightHandlers.Count == 0 &&
                popupSpotlightScrolls.Count == 0 && itemSurfaces.All(surface => !surface.IsVisible));
            box.IsDropDownOpen = true;
            for (var attempt = 0; popupSpotlightChild is null && attempt < 30; attempt++) await Task.Delay(25);
            Check("reopening retained dropdown attaches one handler set", popupSpotlightChild is not null && popupSpotlightHandlers.Count == 7);
        }
        finally
        {
            EndSpotlightScrollDrag(614);
            box.IsDropDownOpen = false;
            box.MaxDropDownHeight = maximumHeight;
            ClosePopupSpotlights();
            ResetCardSpotlights();
        }
    }
}
