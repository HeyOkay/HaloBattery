using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private Point? spotlightPointer;
    private uint? spotlightDragPointer;
    private EventHandler<object>? spotlightFrame;
    private EventHandler<object>? scrollDragFrame;
    private EventHandler<object>? pointerFrameClock;
    private long pointerFrameUntil;
    private int pointerFramesRemaining;
    private ScrollViewer? spotlightScroll;
    private StackPanel? spotlightGeometryContent;
    private bool spotlightGeometryDirty = true;
    private readonly List<(CardView card, Rect bounds)> spotlightBounds = new();
    private HashSet<CardView> spotlightLit = new(), spotlightNext = new();
#if HALO_BATTERY_VERIFICATION
    private SpotlightMetrics? spotlightMetrics;
    private sealed class SpotlightMetrics
    {
        internal long Flushes, GeometryBuilds, ControlGeometryBuilds, BrushWrites, DragFrameTicks, ControlBrushWrites, PointerClockTicks;
    }
#endif

    private void InitializeCardSpotlights()
    {
        PointerEventHandler observePointer = (_, args) =>
        {
            // A captured native thumb already owns this pointer. Avoid even
            // coordinate conversion while it scrolls, including outside the bar.
            if (spotlightDragPointer == args.Pointer.PointerId) return;
            var point = args.GetCurrentPoint(root);
            if (point.Properties.IsLeftButtonPressed && IsSpotlightScrollBarSource(args.OriginalSource))
                BeginSpotlightScrollDrag(args.Pointer.PointerId, IsScrollBarThumbSource(args.OriginalSource));
            else QueueCardSpotlight(point.Position);
        };
        root.AddHandler(UIElement.PointerMovedEvent, observePointer, true);
        root.AddHandler(UIElement.PointerEnteredEvent, observePointer, true);
        root.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler((_, args) =>
        {
            if (IsSpotlightScrollBarSource(args.OriginalSource) && args.GetCurrentPoint(root).Properties.IsLeftButtonPressed)
                BeginSpotlightScrollDrag(args.Pointer.PointerId, IsScrollBarThumbSource(args.OriginalSource));
        }), true);
        void Released(object sender, PointerRoutedEventArgs args) => EndSpotlightScrollDrag(args.Pointer.PointerId);
        root.AddHandler(UIElement.PointerReleasedEvent, new PointerEventHandler(Released), true);
        root.AddHandler(UIElement.PointerCanceledEvent, new PointerEventHandler(Released), true);
        root.AddHandler(UIElement.PointerCaptureLostEvent, new PointerEventHandler(Released), true);
        root.PointerExited += (_, args) =>
        {
            var at = args.GetCurrentPoint(root).Position;
            if (at.X < 0 || at.Y < 0 || at.X >= root.ActualWidth || at.Y >= root.ActualHeight)
                ResetCardSpotlights(preserveScrollDrag: true);
        };
        root.Unloaded += (_, _) => ResetCardSpotlights();
        Activated += (_, args) => { if (args.WindowActivationState == WindowActivationState.Deactivated) { ClosePopupSpotlights(); ResetCardSpotlights(); } };
        Closed += (_, _) =>
        {
            ResetCardSpotlights();
            ClearControlSpotlights();
            ResetContentWheelObservation();
            if (spotlightScroll is not null) spotlightScroll.ViewChanged -= SpotlightViewChanged;
            spotlightScroll = null;
        };
    }

    private static bool IsSpotlightScrollBarSource(object? source)
    {
        for (var node = source as DependencyObject; node is not null; node = VisualTreeHelper.GetParent(node))
            if (node is ScrollBar) return true;
        return false;
    }

    private static bool IsScrollBarThumbSource(object? source)
    {
        var thumb = false;
        for (var node = source as DependencyObject; node is not null; node = VisualTreeHelper.GetParent(node))
        {
            if (node is Thumb) thumb = true;
            if (node is ScrollBar) return thumb;
        }
        return false;
    }

    private void BeginSpotlightScrollDrag(uint pointer, bool isThumb)
    {
        if (spotlightDragPointer != pointer)
        {
            ResetCardSpotlights();
            spotlightDragPointer = pointer;
        }
        if (isThumb && scrollDragFrame is null && !closing && !IsSettingsWindowHidden)
        {
            // Rendering subscribers keep XAML's frame clock active. Scope this
            // to a captured native thumb; do not redraw cards or set scroll offsets.
            scrollDragFrame = (_, _) => {
#if HALO_BATTERY_VERIFICATION
                if (spotlightMetrics is { } metrics) metrics.DragFrameTicks++;
#endif
            };
            CompositionTarget.Rendering += scrollDragFrame;
        }
    }

    // Keep XAML's frame clock running through a moving pointer gesture,
    // as for native thumb dragging. The existing coalescer still does all
    // effect work once per frame; this callback performs no layout or drawing.
    private void KeepPointerFrameClock()
    {
        if (closing || IsSettingsWindowHidden || spotlightDragPointer is not null) return;
        pointerFrameUntil = Environment.TickCount64 + 120;
        pointerFramesRemaining = 2;
        if (pointerFrameClock is not null) return;
        pointerFrameClock = (_, _) =>
        {
#if HALO_BATTERY_VERIFICATION
            if (spotlightMetrics is { } metrics) metrics.PointerClockTicks++;
#endif
            if (closing || IsSettingsWindowHidden) { StopPointerFrameClock(); return; }
            // A delayed first frame must not also be the final frame that
            // flushes the brush update. Allow two completed callbacks first.
            if (pointerFramesRemaining > 0) { pointerFramesRemaining--; return; }
            if (Environment.TickCount64 >= pointerFrameUntil) StopPointerFrameClock();
        };
        CompositionTarget.Rendering += pointerFrameClock;
    }

    private void StopPointerFrameClock()
    {
        if (pointerFrameClock is not null) CompositionTarget.Rendering -= pointerFrameClock;
        pointerFrameClock = null;
        pointerFrameUntil = 0;
        pointerFramesRemaining = 0;
    }

    private void StopScrollDragFrame()
    {
        if (scrollDragFrame is null) return;
        CompositionTarget.Rendering -= scrollDragFrame;
        scrollDragFrame = null;
    }

    private void EndSpotlightScrollDrag(uint pointer)
    {
        if (spotlightDragPointer != pointer) return;
        spotlightDragPointer = null;
        StopScrollDragFrame();
    }

    private void AttachSpotlightScroll()
    {
        if (spotlightScroll is not null) spotlightScroll.ViewChanged -= SpotlightViewChanged;
        spotlightScroll = pageHosts.FirstOrDefault().scroll;
        if (spotlightScroll is not null) spotlightScroll.ViewChanged += SpotlightViewChanged;
        spotlightGeometryDirty = true;
    }

    private void SpotlightViewChanged(object? sender, ScrollViewerViewChangedEventArgs args) => RequestSpotlightFrame();

    internal void InvalidateCardSpotlightGeometry()
    {
        controlSpotlightGeometryDirty = true;
        InvalidateCardSpotlightBounds();
    }

    internal void InvalidateCardSpotlightBounds()
    {
        spotlightGeometryDirty = true;
        MarkPopupSpotlightGeometryDirty();
        RequestSpotlightFrame();
    }

    private void QueueCardSpotlight(Point point)
    {
        if (spotlightDragPointer is not null || HasOpenPopupSpotlights) return;
        spotlightPointer = point;
        KeepPointerFrameClock();
        RequestSpotlightFrame();
    }

    private void RequestSpotlightFrame()
    {
        if (spotlightFrame is not null || (spotlightPointer is null && !HasPopupSpotlightPointer) || spotlightDragPointer is not null || closing || IsSettingsWindowHidden) return;
        spotlightFrame = (_, _) => FlushCardSpotlights();
        CompositionTarget.Rendering += spotlightFrame;
    }

    private void StopSpotlightFrame()
    {
        if (spotlightFrame is null) return;
        CompositionTarget.Rendering -= spotlightFrame;
        spotlightFrame = null;
    }

    private void SetCardSpotlight(CardView card, Point? point)
    {
#if HALO_BATTERY_VERIFICATION
        if (card.SetSpotlight(point) && spotlightMetrics is { } metrics) metrics.BrushWrites++;
#else
        card.SetSpotlight(point);
#endif
    }

    private void ResetCardSpotlights(bool preserveScrollDrag = false)
    {
        StopPointerFrameClock();
        StopSpotlightFrame();
        spotlightPointer = null;
        if (!preserveScrollDrag)
        {
            spotlightDragPointer = null;
            StopScrollDragFrame();
        }
        foreach (var card in spotlightLit) SetCardSpotlight(card, null);
        spotlightLit.Clear();
        spotlightNext.Clear();
        spotlightBounds.Clear();
        ResetControlSpotlights();
        ResetPopupSpotlights();
        spotlightGeometryContent = null;
        spotlightGeometryDirty = true;
    }

    private static bool NearSpotlight(Rect bounds, Point point)
    {
        var x = Math.Max(0, Math.Max(bounds.Left - point.X, point.X - bounds.Right));
        var y = Math.Max(0, Math.Max(bounds.Top - point.Y, point.Y - bounds.Bottom));
        return x * x + y * y <= CardView.SpotlightRadius * CardView.SpotlightRadius;
    }

    private void FlushCardSpotlights()
    {
        StopSpotlightFrame();
        if (spotlightDragPointer is not null || closing || IsSettingsWindowHidden) return;
        FlushPopupSpotlights();
        if (HasOpenPopupSpotlights || spotlightPointer is not { } pointer || pageHosts.Count == 0) return;
        var (content, scroll) = pageHosts[0];
        if (!content.IsLoaded || !scroll.IsLoaded) return;
#if HALO_BATTERY_VERIFICATION
        if (spotlightMetrics is { } metrics) metrics.Flushes++;
#endif
        var contentChanged = !ReferenceEquals(content, spotlightGeometryContent);
        if (spotlightGeometryDirty || contentChanged)
        {
            spotlightBounds.Clear();
            foreach (var card in cards)
                if (card.IsLoaded && card.Visibility == Visibility.Visible)
                    spotlightBounds.Add((card, card.TransformToVisual(content).TransformBounds(new Rect(0, 0, card.ActualWidth, card.ActualHeight))));
            spotlightGeometryContent = content;
            spotlightGeometryDirty = false;
#if HALO_BATTERY_VERIFICATION
            if (spotlightMetrics is { } builds) builds.GeometryBuilds++;
#endif
        }
        if (controlSpotlightGeometryDirty || contentChanged)
        {
            RebuildControlSpotlightGeometry(content);
            controlSpotlightGeometryDirty = false;
#if HALO_BATTERY_VERIFICATION
            if (spotlightMetrics is { } controls) controls.ControlGeometryBuilds++;
#endif
        }
        // Two transforms per frame, rather than one native coordinate lookup
        // per card per pointer packet. Cached card positions survive scrolling.
        var rootToContent = root.TransformToVisual(content);
        var local = rootToContent.TransformPoint(pointer);
        var viewport = scroll.TransformToVisual(content).TransformBounds(new Rect(0, 0, scroll.ActualWidth, scroll.ActualHeight));
        foreach (var (card, bounds) in spotlightBounds)
        {
            if (bounds.Bottom < viewport.Top || bounds.Top > viewport.Bottom || !NearSpotlight(bounds, local)) continue;
            spotlightNext.Add(card);
            SetCardSpotlight(card, new Point(local.X - bounds.X, local.Y - bounds.Y));
        }
        foreach (var card in spotlightLit)
            if (!spotlightNext.Contains(card)) SetCardSpotlight(card, null);
        (spotlightLit, spotlightNext) = (spotlightNext, spotlightLit);
        spotlightNext.Clear();
        FlushControlSpotlights(local, pointer, viewport, rootToContent);
    }
}
