using System.Diagnostics;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyWindowInteractions(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        async Task WaitFor(string name, Func<bool> ready)
        {
            for (var i = 0; !ready() && i < 100; i++) await Task.Delay(20);
            Check(name, ready());
        }
        async Task Settle()
        {
            await Task.Delay(80);
            await WaitFor("window geometry queue settles", () => !windowChromePending && railTick is null);
            root.UpdateLayout();
            await Task.Delay(30);
        }
        var presenter = (OverlappedPresenter)AppWindow.Presenter;
        var wasMaximized = presenter.State == OverlappedPresenterState.Maximized;
        presenter.Restore();
        Navigate("general", false);
        await Settle();
        var originalPosition = AppWindow.Position;
        var originalSize = AppWindow.Size;
        var originalExpanded = expanded;
        var previousMetrics = windowInteractionMetrics;
        var metrics = windowInteractionMetrics = new WindowInteractionMetrics();
        var retainedPage = frame.Content;
        var scale = Scale;
        var monitor = CurrentWindowMonitor();
        var tolerance = 1 / Scale + .01;
        double ExpectedLeft(double viewport, double width)
        {
            var left = presenter.State == OverlappedPresenterState.Maximized
                ? (root.ActualWidth - width) / 2 - rail.Width.Value : (viewport - width) / 2;
            return Math.Clamp(left, 0, Math.Max(0, viewport - width));
        }
        void CheckGeometry(string phase)
        {
            var (content, scroll) = pageHosts.Single();
            var viewport = scroll.ViewportWidth;
            var width = Math.Min(ContentWidth, viewport);
            var inset = 44 * width / ContentWidth;
            Check(phase + ": page layout preserves width padding and centering", viewport > 0 &&
                Math.Abs(content.Width - width) <= tolerance && Math.Abs(content.Padding.Left - inset) <= tolerance &&
                Math.Abs(content.Padding.Right - inset) <= tolerance &&
                Math.Abs(content.Margin.Left - ExpectedLeft(viewport, width)) <= tolerance,
                new { viewport, content.Width, content.Padding, content.Margin });
            var footerViewport = generalFooter!.ActualWidth;
            var footerWidth = Math.Min(ContentWidth, footerViewport);
            Check(phase + ": fixed footer follows the same page geometry", generalFooter.Visibility == Visibility.Visible &&
                Math.Abs(generalFooterContent!.Width - footerWidth) <= tolerance &&
                Math.Abs(generalFooterContent.Padding.Left - 44 * footerWidth / ContentWidth) <= tolerance &&
                Math.Abs(generalFooterContent.Margin.Left - ExpectedLeft(footerViewport, footerWidth)) <= tolerance);
            Check(phase + ": rail interaction retains the page and scroll controls", ReferenceEquals(frame.Content, retainedPage) &&
                retainedPage is Page { Content: ScrollViewer retainedScroll } && ReferenceEquals(retainedScroll, scroll));
        }
        async Task ReverseRail(string phase, bool startExpanded)
        {
            if (expanded != startExpanded) { ToggleRail(); await Settle(); }
            var intervals = new List<double>();
            var clock = Stopwatch.StartNew();
            double? last = null;
            EventHandler<object> rendering = (_, _) =>
            {
                if (railTick is null) return;
                var now = clock.Elapsed.TotalMilliseconds;
                if (last is double previous) intervals.Add(now - previous);
                last = now;
            };
            CompositionTarget.Rendering += rendering;
            try
            {
                var initialFrames = metrics.RailFrames;
                ToggleRail();
                if (AnimationsEnabled)
                {
                    await WaitFor(phase + ": rail animation receives native frame ticks", () => metrics.RailFrames > initialFrames);
                    var visibleWidth = rail.Width.Value;
                    ToggleRail();
                    Check(phase + ": reversing begins at the visible rail width", Math.Abs(rail.Width.Value - visibleWidth) < .001);
                }
                else ToggleRail();
                await Settle();
                Check(phase + ": rapid reversal reaches its original endpoint", expanded == startExpanded &&
                    Math.Abs(rail.Width.Value - (startExpanded ? 228 : 64)) < .001);
                CheckGeometry(phase);
            }
            finally { CompositionTarget.Rendering -= rendering; }
            checks.Add(new
            {
                name = phase + ": rail frame intervals",
                diagnostic = true,
                milliseconds = intervals,
                note = "XAML callback intervals during fixture animation, not presented FPS or a guarantee of perceived smoothness."
            });
        }
        try
        {
            var chromeBefore = metrics.ChromeBatches;
            var layoutsBefore = metrics.LayoutCalls;
            var direction = originalPosition.X + originalSize.Width / 2 + 12 < monitor.Work.X + monitor.Work.Width ? 1 : -1;
            for (var i = 0; i < 4; i++)
            {
                var previousEvents = metrics.NativePositionEvents;
                AppWindow.Move(new PointInt32(originalPosition.X + direction * (i % 2 + 1) * 6, originalPosition.Y));
                await WaitFor("native move emits a position event", () => metrics.NativePositionEvents > previousEvents);
                await Task.Delay(20);
            }
            Check("same-monitor native moves preserve client geometry", CurrentWindowMonitor().Name == monitor.Name &&
                Scale == scale && AppWindow.Size.Width == originalSize.Width && AppWindow.Size.Height == originalSize.Height);
            Check("position-only moves perform no chrome batches or page layout calls", metrics.ChromeBatches == chromeBefore &&
                metrics.LayoutCalls == layoutsBefore,
                new { positionEvents = metrics.NativePositionEvents, chromeBatches = metrics.ChromeBatches - chromeBefore,
                    layoutCalls = metrics.LayoutCalls - layoutsBefore });
            AppWindow.Move(originalPosition);
            await Settle();

            chromeBefore = metrics.ChromeBatches;
            var pageLayoutsBefore = metrics.PageLayouts;
            for (var i = 0; i < 12; i++) QueueWindowChromeRefresh();
            await Settle();
            Check("duplicate chrome requests coalesce into one batch", metrics.ChromeBatches == chromeBefore + 1,
                new { batches = metrics.ChromeBatches - chromeBefore });

            chromeBefore = metrics.ChromeBatches;
            await HideSettingsWindowAsync();
            QueueWindowChromeRefresh(updateMinimum: true);
            Check("hidden DPI refresh remains pending without background layout", windowMinimumPending &&
                !windowChromePending && metrics.ChromeBatches == chromeBefore);
            ShowSettingsWindow(null, null);
            await Settle();
            Check("showing consumes the pending DPI refresh", !windowMinimumPending &&
                metrics.ChromeBatches == chromeBefore + 1);
            Check("unchanged chrome refresh preserves existing page geometry", metrics.PageLayouts == pageLayoutsBefore);

            var minimum = (int)Math.Ceiling(MinimumWindowWidthDip * Scale);
            var resizedWidth = originalSize.Width > minimum + 64 ? originalSize.Width - 48 : originalSize.Width + 48;
            pageLayoutsBefore = metrics.PageLayouts;
            AppWindow.Resize(new SizeInt32(resizedWidth, originalSize.Height));
            await Settle();
            Check("actual native resize still changes page layout", AppWindow.Size.Width != originalSize.Width && metrics.PageLayouts > pageLayoutsBefore);
            CheckGeometry("native resize");

            var wide = Math.Min(monitor.Work.Width, Math.Max(minimum, (int)Math.Round(1280 * Scale)));
            AppWindow.Resize(new SizeInt32(wide, originalSize.Height));
            await Settle();
            await ReverseRail("restored wide", true);
            AppWindow.Resize(new SizeInt32(Math.Min(monitor.Work.Width, minimum + 8), originalSize.Height));
            await Settle();
            await ReverseRail("restored narrow", false);
            Check("narrow rail case exercises reduced page width", pageHosts.Single().scroll.ViewportWidth < ContentWidth);
            presenter.Maximize();
            await Settle();
            await ReverseRail("maximized", true);
            checks.Add(new { name = "window interaction operation counts", diagnostic = true,
                metrics.NativePositionEvents, metrics.ChromeBatches, metrics.LayoutCalls, metrics.PageLayouts, metrics.RailFrames });
        }
        finally
        {
            StopRail();
            expanded = originalExpanded;
            rail.Width = new GridLength(originalExpanded ? 228 : 64);
            BuildNavigation();
            presenter.Restore();
            AppWindow.MoveAndResize(new RectInt32(originalPosition.X, originalPosition.Y, originalSize.Width, originalSize.Height));
            if (wasMaximized) presenter.Maximize();
            QueueWindowChromeRefresh();
            windowInteractionMetrics = previousMetrics;
        }
    }
}
