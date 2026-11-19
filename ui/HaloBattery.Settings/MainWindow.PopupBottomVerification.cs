using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyPopupBottomInset(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        foreach (var (page, label, name) in new[] {
            ("notifications", "preferences.low", "short threshold dropdown"),
            ("devices", "settings.pictogram", "long pictogram dropdown"),
            ("devices", "menu.device_low", "device threshold dropdown") })
        {
            Navigate(page, false);
            await Task.Delay(120);
            root.UpdateLayout();
            var box = activeControlSpotlights.Select(surface => surface.Owner).OfType<ComboBox>()
                .Single(control => AutomationProperties.GetName(control) == T(label));
            var (content, pageScroll) = pageHosts.Single();
            var y = box.TransformToVisual(content).TransformPoint(new Point()).Y;
            pageScroll.ChangeView(null, Math.Clamp(y - 60, 0, pageScroll.ScrollableHeight), null, true);
            await Task.Delay(80);
            var selected = box.SelectedIndex;
            var maximumHeight = box.MaxDropDownHeight;
            if (label == "settings.pictogram") box.MaxDropDownHeight = 180;
            try
            {
                box.IsDropDownOpen = true;
                for (var attempt = 0; popupSpotlightChild is null && attempt < 30; attempt++) await Task.Delay(25);
                var child = popupSpotlightChild ?? throw new InvalidOperationException("Popup not attached");
                var scroll = popupSpotlightScrolls.First();
                if (label == "settings.pictogram")
                    for (var attempt = 0; scroll.ScrollableHeight <= 0 && attempt < 30; attempt++) await Task.Delay(25);
                scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
                await Task.Delay(120);
                root.UpdateLayout();
                var item = box.ContainerFromIndex(box.Items.Count - 1) as ComboBoxItem
                    ?? throw new InvalidOperationException("Final dropdown item was not realized");
                var presenter = VerificationDescendants(child).OfType<ItemsPresenter>().Single();
                Check(name + " retains compact dropdown padding", box.Resources["ComboBoxDropdownContentMargin"] is Thickness { Left: 0, Top: 4, Right: 0, Bottom: 0 });
                var viewport = VerificationDescendants(scroll).OfType<ScrollContentPresenter>().First();
                var clip = viewport.TransformToVisual(child).TransformBounds(new Rect(0, 0, viewport.ActualWidth, viewport.ActualHeight));
                var bounds = item.TransformToVisual(child).TransformBounds(new Rect(0, 0, item.ActualWidth, item.ActualHeight));
                const double epsilon = .1;
                Check(name + " actual native geometry", true, new { bounds, clip, item.Margin, presenterMargin = presenter.Margin, scale = root.XamlRoot.RasterizationScale, scroll.VerticalOffset, scroll.ScrollableHeight });
                Check(name + " final item is fully inside its real content viewport", bounds.Left >= clip.Left - epsilon &&
                    bounds.Top >= clip.Top - epsilon && bounds.Right <= clip.Right + epsilon && bounds.Bottom <= clip.Bottom + epsilon);
                Check(name + " measured bottom clearance", true, new { clearance = clip.Bottom - bounds.Bottom });
                var presenterBounds = presenter.TransformToVisual(child).TransformBounds(new Rect(0, 0, presenter.ActualWidth, presenter.ActualHeight));
                Check(name + " native inner presenter diagnostic", true, new { bounds, presenterBounds, presenter.Margin });
                var border = child as Border ?? VerificationDescendants(child).OfType<Border>().First(control => control.Name == "PopupBorder");
                var toBorder = item.TransformToVisual(border);
                var toChild = item.TransformToVisual(child);
                bool Inside(Point local)
                {
                    var at = toChild.TransformPoint(local);
                    if (at.X < clip.Left - epsilon || at.X > clip.Right + epsilon || at.Y < clip.Top - epsilon || at.Y > clip.Bottom + epsilon) return false;
                    at = toBorder.TransformPoint(local);
                    if (at.X < -epsilon || at.Y < -epsilon || at.X > border.ActualWidth + epsilon || at.Y > border.ActualHeight + epsilon) return false;
                    var r = Math.Min(border.CornerRadius.TopLeft, Math.Min(border.ActualWidth, border.ActualHeight) / 2);
                    var dx = at.X - Math.Clamp(at.X, r, border.ActualWidth - r);
                    var dy = at.Y - Math.Clamp(at.Y, r, border.ActualHeight - r);
                    return dx * dx + dy * dy <= (r + epsilon) * (r + epsilon);
                }
                var radius = Math.Min(item.CornerRadius.TopLeft, Math.Min(item.ActualWidth, item.ActualHeight) / 2);
                var fits = true;
                foreach (var (cx, cy, start) in new[] { (radius, radius, 180),
                    (item.ActualWidth - radius, radius, 270),
                    (item.ActualWidth - radius, item.ActualHeight - radius, 0),
                    (radius, item.ActualHeight - radius, 90) })
                    for (var step = 0; step <= 8; step++)
                    {
                        var angle = (start + step * 90.0 / 8) * Math.PI / 180;
                        fits &= Inside(new Point(cx + radius * Math.Cos(angle), cy + radius * Math.Sin(angle)));
                    }
                Check(name + " complete rounded outline fits both viewport and popup", fits);
                QueuePopupControlSpotlight(new Point(bounds.X + bounds.Width / 2, bounds.Y + bounds.Height / 2));
                FlushCardSpotlights();
                if (!controlSpotlights.TryGetValue(item, out var rowSurface)) throw new InvalidOperationException("No row reveal registered");
                var shapeVisual = rowSurface.HighlightVisual ?? throw new InvalidOperationException("No row highlight visual");
                var overlay = Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementChildVisual(child) as Microsoft.UI.Composition.ContainerVisual;
                Check(name + " reveal is hosted outside the clipping items presenter", overlay is not null &&
                    shapeVisual.Parent?.Comment == overlay.Comment &&
                    Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementChildVisual(item) is null &&
                    Math.Abs(shapeVisual.Offset.X - bounds.X) < epsilon && Math.Abs(shapeVisual.Offset.Y - bounds.Y) < epsilon);
                var primitive = (Microsoft.UI.Composition.CompositionSpriteShape)shapeVisual.Shapes[0];
                var geometry = (Microsoft.UI.Composition.CompositionRoundedRectangleGeometry)primitive.Geometry;
                var inset = 1 / child.XamlRoot.RasterizationScale;
                var stroke = primitive.StrokeThickness / 2;
                Check(name + " antialiased stroke stays inside its row without changing layout",
                    Math.Abs(shapeVisual.Size.X - item.ActualWidth) < epsilon &&
                    Math.Abs(shapeVisual.Size.Y - item.ActualHeight) < epsilon &&
                    geometry.Offset.X - stroke >= inset - epsilon && geometry.Offset.Y - stroke >= inset - epsilon &&
                    geometry.Offset.X + geometry.Size.X + stroke <= item.ActualWidth - inset + epsilon &&
                    geometry.Offset.Y + geometry.Size.Y + stroke <= item.ActualHeight - inset + epsilon &&
                    item.Margin == new Thickness(4, 0, 4, 4) && item.MinHeight == 30 && item.CornerRadius == new CornerRadius(15));
                VisualStateManager.GoToState(item, "PointerOver", false);
                var antialias = .5 / child.XamlRoot.RasterizationScale;
                var paint = new Rect(shapeVisual.Offset.X + geometry.Offset.X - stroke - antialias,
                    shapeVisual.Offset.Y + geometry.Offset.Y - stroke - antialias,
                    geometry.Size.X + 2 * (stroke + antialias), geometry.Size.Y + 2 * (stroke + antialias));
                Check(name + " complete antialiased stroke fits the popup overlay viewport", paint.Left >= clip.Left - epsilon &&
                    paint.Right <= clip.Right + epsilon && paint.Top >= clip.Top - epsilon && paint.Bottom <= clip.Bottom + epsilon);
                Check(name + " final item reveals without altering selection", controlSpotlights.TryGetValue(item, out var surface) &&
                    surface.HasVisual && surface.IsVisible && box.SelectedIndex == selected);
            }
            finally
            {
                box.IsDropDownOpen = false;
                box.MaxDropDownHeight = maximumHeight;
                ClosePopupSpotlights();
                ResetCardSpotlights();
            }
        }
    }
}
