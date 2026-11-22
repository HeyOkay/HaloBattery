using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyControlSpotlights(List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Point At(Control control) => control.TransformToVisual(root).TransformPoint(
            new Point(control.ActualWidth / 2, control.ActualHeight / 2));
        void Move(Point point) { QueueCardSpotlight(point); FlushCardSpotlights(); }
        Navigate("devices", false);
        await Task.Delay(160);
        root.UpdateLayout();
        ResetCardSpotlights();
        var metrics = spotlightMetrics = new SpotlightMetrics();
        try
        {
            var selector = activeControlSpotlights.First(surface => surface.Owner is ComboBox);
            var owner = selector.Owner;
            Check("page controls allocate no reveal visual before proximity", activeControlSpotlights.All(surface => !surface.HasVisual));
            Check("page pills retain zero permanent border thickness", owner.BorderThickness == new Thickness(0));
            Check("window chrome is excluded from page-control reveal", hamburger is not null &&
                !controlSpotlights.TryGetValue(hamburger, out _) &&
                windowButtons.Children.OfType<Control>().All(button => !controlSpotlights.TryGetValue(button, out _)));
            var pointer = At(owner);
            for (var index = 0; index < 100; index++) QueueCardSpotlight(new Point(pointer.X + index * .001, pointer.Y));
            Check("control reveal coalesces pointer packets without immediate brush writes", spotlightFrame is not null && metrics.ControlBrushWrites == 0);
            FlushCardSpotlights();
            Check("dropdown gets an owned lazy visible stroke", selector.HasVisual && selector.IsVisible && selector.SpotlightPosition is not null);
            Check("reveal does not change the control's permanent border", owner.BorderThickness == new Thickness(0));

            owner.IsEnabled = false;
            FlushCardSpotlights();
            Check("disabled page controls lose their reveal", !selector.IsVisible && selector.SpotlightPosition is null);
            owner.IsEnabled = true;
            Move(At(owner));
            Check("reenabled page controls reveal again", selector.IsVisible);

            var margin = owner.Margin;
            var builds = metrics.GeometryBuilds;
            owner.Margin = new Thickness(margin.Left, margin.Top, margin.Right + 7, margin.Bottom);
            root.UpdateLayout();
            Move(At(owner));
            Check("same-size control movement invalidates cached local geometry", metrics.GeometryBuilds > builds &&
                selector.SpotlightPosition is { } moved && Math.Abs(moved.X - owner.ActualWidth / 2) < .1);
            owner.Margin = margin;
            root.UpdateLayout();

            var parent = (UIElement)VisualTreeHelper.GetParent(owner);
            var originalClip = parent.Clip;
            var clip = new RectangleGeometry { Rect = new Rect(0, 0, 10000, 10000) };
            try
            {
                parent.Clip = clip;
                InvalidateCardSpotlightGeometry();
                Move(At(owner));
                Check("a visible ancestor clip retains the control reveal", selector.IsVisible);
                builds = metrics.GeometryBuilds;
                clip.Rect = new Rect(0, 0, 0, 0);
                FlushCardSpotlights();
                Check("animated zero-height ancestor clips suppress reveal without layout scans", !selector.IsVisible && metrics.GeometryBuilds == builds);
            }
            finally { parent.Clip = originalClip; InvalidateCardSpotlightGeometry(); }

            Move(At(owner));
            BeginSpotlightScrollDrag(613, true);
            var writes = metrics.ControlBrushWrites;
            for (var index = 0; index < 2000; index++) QueueCardSpotlight(new Point(index % 300, index % 200));
            FlushCardSpotlights();
            Check("captured scrollbar drag hides control reveal and writes no brushes", controlSpotlightLit.Count == 0 &&
                metrics.ControlBrushWrites == writes && activeControlSpotlights.All(surface => !surface.IsVisible));
            EndSpotlightScrollDrag(613);
            Move(At(owner));
            Check("control reveal resumes after scrollbar release", selector.IsVisible);

            var (content, deviceScroll) = pageHosts.Single();
            async Task RevealControl(Control control)
            {
                var y = control.TransformToVisual(content).TransformPoint(new Point()).Y;
                deviceScroll.ChangeView(null, Math.Clamp(y - 60, 0, deviceScroll.ScrollableHeight), null, true);
                await Task.Delay(100);
                root.UpdateLayout();
                Move(At(control));
            }
            var textField = activeControlSpotlights.Single(surface => surface.Owner is TextBox && Microsoft.UI.Xaml.Automation.AutomationProperties.GetName(surface.Owner) == T("settings.rename"));
            await RevealControl(textField.Owner);
            Check("device-name editor receives reveal without adding a permanent border", textField.HasVisual &&
                textField.IsVisible && textField.Owner.BorderThickness == new Thickness(0));
            var toggle = activeControlSpotlights.First(surface => surface.Owner is SettingToggle);
            var toggleOwner = (SettingToggle)toggle.Owner;
            var checkedBefore = toggleOwner.IsChecked;
            await RevealControl(toggleOwner);
            Check("toggle track receives a rounded reveal without changing its state", toggle.HasVisual && toggle.IsVisible &&
                toggleOwner.CornerRadius == new CornerRadius(11) && toggleOwner.IsChecked == checkedBefore &&
                toggleOwner.BorderThickness == new Thickness(0));
            IEnumerable<DependencyObject> Descendants(DependencyObject node)
            {
                for (var index = 0; index < VisualTreeHelper.GetChildrenCount(node); index++)
                {
                    var child = VisualTreeHelper.GetChild(node, index);
                    yield return child;
                    foreach (var nested in Descendants(child)) yield return nested;
                }
            }
            var shadowHost = Descendants(toggleOwner).OfType<FrameworkElement>().Single(element => element.Name == "ThumbShadowHost");
            var shadow = Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementChildVisual(shadowHost);
            Check("toggle reveal preserves the separate native thumb-shadow visual", shadow is Microsoft.UI.Composition.SpriteVisual { Shadow: not null } &&
                Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementChildVisual(toggleOwner) is Microsoft.UI.Composition.ShapeVisual);

            // Build geometry at the top, then scroll without invalidation. The
            // viewport clip above content must use its current transform.
            deviceScroll.ChangeView(null, 0, null, true);
            await Task.Delay(100);
            root.UpdateLayout();
            Move(At(owner));
            builds = metrics.GeometryBuilds;
            var lower = activeControlSpotlights.Where(surface => surface.Owner is SettingToggle)
                .OrderByDescending(surface => surface.Owner.TransformToVisual(content).TransformPoint(new Point()).Y).First();
            await RevealControl(lower.Owner);
            Check("lower controls reveal on the first pointer sample after scrolling", lower.IsVisible && metrics.GeometryBuilds == builds);

            await VerifyPopupControlSpotlights(checks);

            var oldControls = activeControlSpotlights.ToArray();
            Navigate("general", false);
            await Task.Delay(120);
            root.UpdateLayout();
            Check("page navigation disposes old reveal resources", oldControls.All(surface => surface.Disposed && !surface.HasVisual));
            Move(new Point(0, 0));
            var fixedControl = controlSpotlightBounds.First(entry => !entry.InContent).Surface;
            var fixedPointer = At(fixedControl.Owner);
            Move(fixedPointer);
            var fixedPosition = fixedControl.SpotlightPosition;
            var scroll = pageHosts.Single().scroll;
            scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
            await Task.Delay(80);
            FlushCardSpotlights();
            Check("fixed General footer reveal stays in local coordinates through scrolling", fixedControl.IsVisible &&
                fixedControl.SpotlightPosition == fixedPosition);
            var active = activeControlSpotlights.Count;
            for (var index = 0; index < 50; index++) _ = Button("unused", true);
            Check("never-loaded controls do not enter the active renderer registry", activeControlSpotlights.Count == active);
            ResetCardSpotlights();
            Check("pointer reset hides every control stroke and stops frame work", spotlightFrame is null &&
                controlSpotlightLit.Count == 0 && activeControlSpotlights.All(surface => !surface.IsVisible));
        }
        finally { EndSpotlightScrollDrag(613); ResetCardSpotlights(); spotlightMetrics = null; }
    }
}
