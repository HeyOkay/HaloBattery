using System.Diagnostics;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyCardSpotlights(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Point RootPoint(CardView card, double x, double y) => card.TransformToVisual(root).TransformPoint(new Point(x, y));
        void Move(Point point) { QueueCardSpotlight(point); FlushCardSpotlights(); }
        Navigate("devices", false);
        await Task.Delay(180);
        root.UpdateLayout();
        ResetCardSpotlights();
        var metrics = spotlightMetrics = new SpotlightMetrics();
        try
        {
            var (content, scroll) = pageHosts.Single();
            var first = cards.First();
            var latest = RootPoint(first, 42, 18);
            for (var i = 0; i < 50; i++) QueueCardSpotlight(RootPoint(first, 42, 18 + (49 - i) * .1));
            Check("fifty pointer packets request one pending frame without brush writes", spotlightFrame is not null && metrics.Flushes == 0 && metrics.BrushWrites == 0);
            FlushCardSpotlights();
            Check("one frame uses the latest pointer coordinate", metrics.Flushes == 1 && first.SpotlightPosition is { } at &&
                Math.Abs(at.X - 42) < .01 && Math.Abs(at.Y - 18) < .01, new { metrics.Flushes, metrics.BrushWrites });
            var writes = metrics.BrushWrites;
            Move(latest);
            Check("unchanged pointer does not rewrite gradient properties", metrics.BrushWrites == writes);
            Move(RootPoint(first, -20, first.ActualHeight / 2));
            Check("reveal remains visible outside a nearby card edge", first.SpotlightPosition is { } outside && Math.Abs(outside.X + 20) < .01);
            Move(RootPoint(first, -CardView.SpotlightRadius - 2, first.ActualHeight / 2));
            Check("reveal stops beyond the gradient support", first.SpotlightPosition is null);
            var second = cards[1];
            var bottom = RootPoint(first, first.ActualWidth / 2, first.ActualHeight);
            var nextTop = RootPoint(second, second.ActualWidth / 2, 0);
            Move(new Point(bottom.X, (bottom.Y + nextTop.Y) / 2));
            Check("a card gap keeps both adjacent edges illuminated", first.SpotlightPosition is not null && second.SpotlightPosition is not null);

            Move(RootPoint(second, 30, 20));
            var before = second.SpotlightPosition!.Value;
            var geometryBuilds = metrics.GeometryBuilds;
            scroll.ChangeView(null, 40, null, true);
            await Task.Delay(120);
            FlushCardSpotlights();
            Check("a stationary pointer follows native scrolling without rebuilding card geometry", scroll.VerticalOffset > 0 &&
                second.SpotlightPosition is { } after && Math.Abs(after.Y - before.Y - scroll.VerticalOffset) < 1 / Scale &&
                metrics.GeometryBuilds == geometryBuilds, new { scroll.VerticalOffset, cacheBuilds = metrics.GeometryBuilds, previousBuilds = geometryBuilds });

            // Moving a preceding card changes following positions even when the
            // following card's own size stays unchanged.
            var margin = first.Margin;
            first.Margin = new Thickness(margin.Left, margin.Top, margin.Right, margin.Bottom + 12);
            root.UpdateLayout();
            FlushCardSpotlights();
            Check("layout changes invalidate relative card positions", metrics.GeometryBuilds > geometryBuilds &&
                second.SpotlightPosition is { } shifted && Math.Abs(shifted.Y - before.Y - scroll.VerticalOffset + 12) < 1 / Scale);
            first.Margin = margin;
            root.UpdateLayout();
            FlushCardSpotlights();

            var priorFirstHeight = first.ActualHeight;
            var priorBuilds = metrics.GeometryBuilds;
            var errorWasVisible = pageError!.Visibility;
            var errorWasOpen = pageError.IsOpen;
            pageError.Message = "Fixture layout shift before the first card";
            pageError.Visibility = Visibility.Visible;
            pageError.IsOpen = true;
            root.UpdateLayout();
            FlushCardSpotlights();
            Check("a non-card sibling invalidates bounds without resizing the card", pageError.ActualHeight > 0 &&
                Math.Abs(first.ActualHeight - priorFirstHeight) < .01 && metrics.GeometryBuilds > priorBuilds);
            pageError.Visibility = errorWasVisible;
            pageError.IsOpen = errorWasOpen;
            root.UpdateLayout();
            Move(RootPoint(second, 30, 20));

            var bar = PageScrollBar(scroll)!;
            var thumb = TemplateRoot(bar)!.FindName("VerticalThumb");
            Check("native thumb descendants are identified as scrollbar input", IsSpotlightScrollBarSource(thumb) && !IsSpotlightScrollBarSource(second));
            Check("only a scrollbar thumb requests continuous drag frames", IsScrollBarThumbSource(thumb) &&
                !IsScrollBarThumbSource(bar) && !IsScrollBarThumbSource(second) && !IsScrollBarThumbSource(new Thumb()));
            BeginSpotlightScrollDrag(16, false);
            Check("scrollbar track input does not run a frame clock", scrollDragFrame is null);
            EndSpotlightScrollDrag(16);
            BeginSpotlightScrollDrag(17, true);
            var dragFrame = scrollDragFrame;
            BeginSpotlightScrollDrag(17, true);
            Check("repeated thumb packets retain one frame subscription", dragFrame is not null && ReferenceEquals(dragFrame, scrollDragFrame));
            await Task.Delay(100);
            Check("native thumb drag keeps the XAML frame clock active", metrics.DragFrameTicks > 0, new { metrics.DragFrameTicks });
            ResetCardSpotlights(preserveScrollDrag: true);
            Check("leaving the window keeps a captured thumb suppressed and paced", spotlightDragPointer == 17 && scrollDragFrame is not null);
            writes = metrics.BrushWrites;
            var flushes = metrics.Flushes;
            var clock = Stopwatch.StartNew();
            for (var i = 0; i < 2000; i++) QueueCardSpotlight(new Point(i % 500, i % 300));
            clock.Stop();
            Check("two thousand captured drag packets cause no spotlight frames or brush writes", spotlightFrame is null &&
                spotlightLit.Count == 0 && metrics.BrushWrites == writes && metrics.Flushes == flushes,
                new { packets = 2000, brushWrites = metrics.BrushWrites - writes, spotlightFlushes = metrics.Flushes - flushes,
                    handlerMs = clock.Elapsed.TotalMilliseconds, note = "Synthetic handler work, not presented FPS or injected pointer input." });
            EndSpotlightScrollDrag(18);
            Check("another pointer cannot end captured-drag suppression or pacing", spotlightDragPointer == 17 && scrollDragFrame is not null);
            EndSpotlightScrollDrag(17);
            var ticksAfterRelease = metrics.DragFrameTicks;
            await Task.Delay(80);
            Check("releasing the thumb removes the continuous frame clock", scrollDragFrame is null && metrics.DragFrameTicks == ticksAfterRelease);
            Move(RootPoint(second, 30, 20));
            Check("normal reveal resumes after releasing the thumb", spotlightDragPointer is null && second.SpotlightPosition is not null);

            scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
            await Task.Delay(100);
            var last = cards.Last();
            Move(RootPoint(last, 30, last.ActualHeight / 2));
            var viewport = scroll.TransformToVisual(content).TransformBounds(new Rect(0, 0, scroll.ActualWidth, scroll.ActualHeight));
            Check("offscreen cards do not retain reveal gradients", spotlightLit.Count > 0 && spotlightLit.All(card =>
            {
                var bounds = card.TransformToVisual(content).TransformBounds(new Rect(0, 0, card.ActualWidth, card.ActualHeight));
                return bounds.Bottom >= viewport.Top && bounds.Top <= viewport.Bottom;
            }), new { allCards = cards.Count, illuminatedCards = spotlightLit.Count });
            var oldCards = cards.ToArray();
            QueueCardSpotlight(latest);
            BeginSpotlightScrollDrag(19, true);
            Navigate("general", false);
            Check("navigation clears the old reveal and deferred frame", spotlightFrame is null && spotlightPointer is null &&
                scrollDragFrame is null && spotlightDragPointer is null && spotlightLit.Count == 0 &&
                oldCards.All(card => card.SpotlightPosition is null) && ReferenceEquals(spotlightScroll, pageHosts.Single().scroll));
            await Task.Delay(100);
            Move(RootPoint(cards.First(), 30, 20));
            BeginSpotlightScrollDrag(20, true);
            await HideSettingsWindowAsync();
            Check("hiding clears reveal and all pending pointer work", spotlightFrame is null && scrollDragFrame is null &&
                spotlightPointer is null && spotlightLit.Count == 0 && cards.All(card => card.SpotlightPosition is null));
            ShowSettingsWindow(null, null);
            Check("reopening retained content leaves reveal ready without a stale pointer", spotlightDragPointer is null && spotlightFrame is null && scrollDragFrame is null);
        }
        finally { ResetCardSpotlights(); spotlightMetrics = null; }
    }
}
