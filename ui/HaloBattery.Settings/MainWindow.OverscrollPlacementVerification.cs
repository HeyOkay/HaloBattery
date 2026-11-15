using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyOverscrollPlacement(string destination, List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        void CheckAnchors(string name, bool top, StackPanel content, ScrollViewer scroll)
        {
            overscrollPresence.TryGetScalar("Boundary", out var boundary);
            overscrollPresence.TryGetScalar("ContentEdge", out var edge);
            var origin = scroll.TransformToVisual(overscrollHost).TransformPoint(new Point()).Y;
            var expectedBoundary = top ? origin : current == "general"
                ? overscrollHost.ActualHeight : origin + scroll.ActualHeight;
            var heading = content.Children.OfType<TextBlock>().First();
            var expectedEdge = origin + (top
                ? heading.TransformToVisual(scroll).TransformPoint(new Point(0, HeadingTextTop(heading))).Y
                : cards.Where(c => c.IsLoaded && c.Visibility == Visibility.Visible)
                    .Max(c => c.TransformToVisual(scroll).TransformPoint(new Point(0, c.ActualHeight)).Y));
            Check(name + " anchor follows the actual content edge", Math.Abs(edge - expectedEdge) < 1 / Scale,
                new { edge, expectedEdge, boundary, scroll.ScrollableHeight });
            Check(name + " uses the expected page boundary", Math.Abs(boundary - expectedBoundary) < 1 / Scale,
                new { boundary, expectedBoundary, viewportBottom = origin + scroll.ActualHeight });
            if (!top && current == "general")
                Check(name + " ignores the fixed Exit footer as an anchor", generalFooter?.ActualHeight > 0 &&
                    boundary > origin + scroll.ActualHeight + 1 / Scale && edge < boundary,
                    new { boundary, edge, footerHeight = generalFooter?.ActualHeight });
        }
        foreach (var (page, top, suffix) in new[] { ("general", false, "bottom"), ("devices", true, "top"),
            ("devices", false, "bottom-devices") })
        {
            Navigate(page, false);
            await Task.Delay(120);
            root.UpdateLayout();
            var (content, scroll) = pageHosts.Single();
            scroll.ChangeView(null, top ? 0 : scroll.ScrollableHeight, null, true);
            await Task.Delay(80);
            HandleContentOverscroll(top ? 240 : -240, scroll.VerticalOffset, scroll.ScrollableHeight);
            StopOverscrollRendering();
            ClearPendingOverscrollNavigation();
            overscrollArc.StopAnimation("TrimEnd");
            overscrollArc.TrimEnd = .75f;
            overscrollPresence.StopAnimation("Presence");
            overscrollPresence.InsertScalar("Presence", 1);
            overscrollContent.Translation = Vector3.Zero;
            PositionOverscrollIndicator(newGesture: true);
            overscrollPresence.TryGetScalar("Boundary", out var boundary);
            overscrollPresence.TryGetScalar("ContentEdge", out var edge);
            CheckAnchors(suffix, top, content, scroll);
            Check("indicator stroke is slightly stronger without changing its base size", overscrollIndicator.Width == 16 &&
                overscrollRingVisual.Shapes.Cast<CompositionSpriteShape>().All(s => Math.Abs(s.StrokeThickness - 1.4f) < .001));
            foreach (var amount in new[] { 0, 16, 32 })
            {
                var pull = top ? amount : -amount;
                overscrollContent.Translation = new Vector3(0, pull, 0);
                await Task.Delay(100);
                var name = "overscroll-midpoint-" + suffix + "-" + amount;
                if (Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported())
                    await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, name + ".png"));
                var indicatorOrigin = overscrollIndicator.TransformToVisual(root).TransformPoint(new Point());
                var hostOrigin = overscrollHost.TransformToVisual(root).TransformPoint(new Point());
                checks.Add(new { name, diagnostic = true, scale = Scale, rootWidth = root.ActualWidth, rootHeight = root.ActualHeight,
                    expectedCenterX = indicatorOrigin.X + 8, expectedCenterY = hostOrigin.Y + (boundary + edge + pull) / 2,
                    boundary = hostOrigin.Y + boundary, contentEdge = hostOrigin.Y + edge + pull });
            }
            FreezeOverscrollMidpoint();
            await Task.Delay(60);
            overscrollContent.Translation = Vector3.Zero;
            await Task.Delay(100);
            var frozenName = "overscroll-midpoint-" + suffix + "-frozen";
            if (Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported())
                await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, frozenName + ".png"));
            var frozenOrigin = overscrollIndicator.TransformToVisual(root).TransformPoint(new Point());
            var frozenHost = overscrollHost.TransformToVisual(root).TransformPoint(new Point());
            checks.Add(new { name = frozenName, diagnostic = true, scale = Scale, rootWidth = root.ActualWidth, rootHeight = root.ActualHeight,
                expectedCenterX = frozenOrigin.X + 8, expectedCenterY = frozenHost.Y + (boundary + edge + (top ? 32 : -32)) / 2,
                boundary = frozenHost.Y + boundary, contentEdge = frozenHost.Y + edge + (top ? 32 : -32) });
            CancelOverscroll();
            await Task.Delay(150);
        }
        var originalSize = AppWindow.Size;
        var presenter = AppWindow.Presenter as Microsoft.UI.Windowing.OverlappedPresenter;
        var wasMaximized = presenter?.State == Microsoft.UI.Windowing.OverlappedPresenterState.Maximized;
        try
        {
            presenter?.Restore();
            Navigate("general", false);
            foreach (var height in new[] { 560, 860 })
            {
                AppWindow.Resize(new Windows.Graphics.SizeInt32(originalSize.Width, (int)Math.Round(height * Scale)));
                await Task.Delay(180);
                root.UpdateLayout();
                var (content, scroll) = pageHosts.Single();
                scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
                await Task.Delay(80);
                overscrollContent.Translation = Vector3.Zero;
                overscrollVisualDirection = 1;
                PositionOverscrollIndicator(newGesture: true);
                CheckAnchors("General at " + height + " DIP height", false, content, scroll);
            }
        }
        finally
        {
            AppWindow.Resize(originalSize);
            if (wasMaximized) presenter?.Maximize();
        }
    }
}
