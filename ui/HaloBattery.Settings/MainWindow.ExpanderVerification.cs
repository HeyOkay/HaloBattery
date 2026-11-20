using System.Diagnostics;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyProviderExpander(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("advanced", false);
        await Task.Delay(100);
        root.UpdateLayout();
        var expander = cards.Single(card => card.Expandable);
        var (content, scroll) = pageHosts.Single();
        var y = expander.TransformToVisual(content).TransformPoint(new Point()).Y;
        scroll.ChangeView(null, Math.Clamp(y - 80, 0, scroll.ScrollableHeight), null, true);
        await Task.Delay(100);
        root.UpdateLayout();
        var originalExpanded = expander.IsExpanded;
        var commands = Bridge.FixtureCommands.Count;
        var metrics = spotlightMetrics = new SpotlightMetrics();
        var rows = activeControlSpotlights.Where(surface => surface.Owner is SettingToggle).ToArray();
        var closedHeight = expander.ActualHeight;
        Point HeadPoint() => expander.TransformToVisual(root).TransformPoint(new Point(expander.ActualWidth - 50, 30));
        QueueCardSpotlight(HeadPoint());
        FlushCardSpotlights();
        async Task Animate(string phase, bool target)
        {
            var builds = metrics.ControlGeometryBuilds;
            var cardBuilds = metrics.GeometryBuilds;
            var intervals = new List<double>();
            var clock = Stopwatch.StartNew();
            double? previous = null;
            var bodyReveal = false;
            EventHandler<object> frameTick = (_, _) =>
            {
                if (!expander.IsAnimating) return;
                var now = clock.Elapsed.TotalMilliseconds;
                if (previous is { } last) intervals.Add(now - last);
                previous = now;
                bodyReveal |= rows.Any(surface => surface.IsVisible);
            };
            CompositionTarget.Rendering += frameTick;
            try
            {
                expander.Toggle();
                var deadline = Stopwatch.StartNew();
                while (expander.IsAnimating && deadline.ElapsedMilliseconds < 5000) await Task.Delay(16);
                Check(phase + " finishes its animation", !expander.IsAnimating && expander.IsExpanded == target);
                await Task.Delay(80);
                root.UpdateLayout();
                FlushCardSpotlights();
                Check(phase + " reuses control ancestry throughout height animation", metrics.ControlGeometryBuilds - builds <= 3,
                    new { providers = State.Providers.Count, controlCacheBuilds = metrics.ControlGeometryBuilds - builds,
                        cardBoundsBuilds = metrics.GeometryBuilds - cardBuilds, frameCallbacks = intervals.Count + (previous is null ? 0 : 1) });
                if (target && AnimationsEnabled)
                    Check(phase + " retains live reveal inside the opening body clip", bodyReveal);
                checks.Add(new { name = phase + " animation timing", diagnostic = true,
                    elapsedMilliseconds = clock.Elapsed.TotalMilliseconds, callbackIntervalsMilliseconds = intervals,
                    note = "Own-fixture XAML callback timings without screen capture, not presented FPS." });
            }
            finally { CompositionTarget.Rendering -= frameTick; }
        }
        try
        {
            Check("provider rows are already laid out while collapsed", State.Providers.Count > 0 && rows.Length >= State.Providers.Count);
            await Animate("expand provider families", true);
            Check("expanded card exposes the complete provider list", expander.ActualHeight > closedHeight + 40 * State.Providers.Count);
            QueueCardSpotlight(HeadPoint()); FlushCardSpotlights();
            await Animate("collapse provider families", false);
            Check("collapse returns to its original header height", Math.Abs(expander.ActualHeight - closedHeight) <= 1 / Scale);
            Check("expansion changes no application settings or device state", Bridge.FixtureCommands.Count == commands);
        }
        finally
        {
            expander.IsExpanded = originalExpanded;
            ResetCardSpotlights();
            spotlightMetrics = null;
        }
    }
}
