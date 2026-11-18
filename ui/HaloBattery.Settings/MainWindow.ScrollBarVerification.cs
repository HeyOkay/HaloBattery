using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media.Animation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private static FrameworkElement? TemplateRoot(Control control) => ScrollBarAppearance.TemplateRoot(control);
    private static ScrollBar? PageScrollBar(ScrollViewer scroll) => TemplateRoot(scroll)?.FindName("VerticalScrollBar") as ScrollBar;

    private async Task VerifyPageScrollBar(string destination, List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("devices", false);
        await Task.Delay(150);
        root.UpdateLayout();
        var scroll = pageHosts.Single().scroll;
        var bar = PageScrollBar(scroll);
        Check("page has a native vertical scrollbar with the global default", bar is not null && bar.ActualHeight > 0 && ScrollBarAppearance.GetEnabled(bar));
        var barRoot = TemplateRoot(bar!)!;
        Check("scrollbar has eight-DIP end insets without shrinking the viewport", bar!.Margin == new Thickness(0,8,0,8) &&
            Math.Abs(scroll.ViewportHeight - scroll.ActualHeight) <= 3 / Scale && scroll.ScrollableHeight > 0,
            new { bar.Margin.Top, bar.Margin.Bottom, scroll.ViewportHeight, scroll.ActualHeight, scroll.ScrollableHeight });
        var conscious = VisualStateManager.GetVisualStateGroups(barRoot).Single(g => g.Name == "ConsciousStates");
        IEnumerable<Timeline> Timelines(Timeline timeline)
        {
            yield return timeline;
            if (timeline is Storyboard storyboard)
                foreach (var child in storyboard.Children)
                    foreach (var nested in Timelines(child)) yield return nested;
        }
        var hover = conscious.States.Where(s => s.Storyboard is not null).Select(s => s.Storyboard)
            .Concat(conscious.Transitions.Where(t => t.Storyboard is not null).Select(t => t.Storyboard)).ToArray();
        Check("all native hover and reduced-motion states start without delay", hover.Length >= 3 &&
            hover.SelectMany(s => Timelines(s!)).All(t => t.BeginTime == TimeSpan.Zero));
        Check("native hover width animations retain their original duration", conscious.States.Single(s => s.Name == "Expanded").Storyboard.Children
            .OfType<DoubleAnimationUsingKeyFrames>().Any(a => a.KeyFrames.Any(k => k.KeyTime.TimeSpan.TotalMilliseconds >= 100)));
        VisualStateManager.GoToState(bar, "MouseIndicator", false);
        VisualStateManager.GoToState(bar, "Expanded", true);
        await Task.Delay(220);
        root.UpdateLayout();
        var thumb = (Thumb)barRoot.FindName("VerticalThumb");
        var thumbVisual = (Microsoft.UI.Xaml.Shapes.Rectangle)TemplateRoot(thumb)!;
        foreach (var name in new[] { "VerticalSmallDecrease", "VerticalSmallIncrease" })
        {
            var button = (RepeatButton)barRoot.FindName(name);
            var arrow = (FontIcon)TemplateRoot(button)!.FindName("Arrow");
            var arrowCenter = arrow.TransformToVisual(button).TransformPoint(new Windows.Foundation.Point(arrow.ActualWidth / 2, arrow.ActualHeight / 2));
            Check(name + ": arrow is centered in both axes", Math.Abs(arrowCenter.X - button.ActualWidth / 2) <= .01 &&
                Math.Abs(arrowCenter.Y - button.ActualHeight / 2) <= .01, new { arrowCenter.X, arrowCenter.Y, button.ActualWidth, button.ActualHeight });
            var arrowAxis = arrow.TransformToVisual(barRoot).TransformPoint(new Windows.Foundation.Point(arrow.ActualWidth / 2, arrow.ActualHeight / 2));
            var thumbAxis = thumb.TransformToVisual(barRoot).TransformPoint(new Windows.Foundation.Point(thumb.ActualWidth / 2, thumb.ActualHeight / 2));
            Check(name + ": arrow shares the expanded thumb axis without a half-pixel offset", !arrow.UseLayoutRounding && Math.Abs(arrowAxis.X - thumbAxis.X) <= .01,
                new { arrowAxis = arrowAxis.X, thumbAxis = thumbAxis.X, scale = Scale });
            Check(name + ": arrow matches expanded thumb fill width", Math.Abs(arrow.ActualWidth - (thumb.ActualWidth - thumbVisual.StrokeThickness)) <= .5 / Scale + .001 &&
                arrow.FontSize == 6 && !arrow.IsTextScaleFactorEnabled && button.ActualHeight >= 12,
                new { arrow.ActualWidth, arrow.FontSize, thumbWidth = thumb.ActualWidth, thumbVisual.StrokeThickness });
        }
        await VerificationCapture(destination, "scrollbar-expanded");
        if (Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported())
            await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "scrollbar-expanded.composited.png"));
        var expanded = thumb.ActualWidth;
        VisualStateManager.GoToState(bar, "Collapsed", true);
        await Task.Delay(220);
        root.UpdateLayout();
        Check("scrollbar contracts without its former half-second hold", thumb.ActualWidth < expanded - 1,
            new { expanded, contracted = thumb.ActualWidth });
        VisualStateManager.GoToState(bar, "Expanded", true);
        await Task.Delay(220);
        root.UpdateLayout();
        Check("scrollbar expands without its former 400ms hold", Math.Abs(thumb.ActualWidth - expanded) <= .1);
    }
}
