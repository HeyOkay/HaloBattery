using System.Runtime.CompilerServices;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    // Ephemeron keys do not keep replaced, never-loaded pager/footer controls
    // alive. Only controls currently in the visual tree enter the strong set.
    private readonly ConditionalWeakTable<Control, ControlSpotlight> controlSpotlights = new();
    private readonly HashSet<ControlSpotlight> activeControlSpotlights = new();
    private readonly List<ControlRevealBounds> controlSpotlightBounds = new();
    private HashSet<ControlSpotlight> controlSpotlightLit = new(), controlSpotlightNext = new();
    private long controlSpotlightEpoch;
    private bool controlSpotlightGeometryDirty = true;
    private readonly Dictionary<UIElement, Rect> controlRootClipBounds = new();
    private readonly Dictionary<ControlRevealClip, Rect> controlInnerClipBounds = new();
    private readonly Dictionary<CardView, Point> controlCardOrigins = new();

    private sealed record ControlRevealClip(UIElement Owner, RectangleGeometry Geometry,
        GeneralTransform? ToSpace, CardView? Anchor);
    private sealed record ControlRevealBounds(ControlSpotlight Surface, Rect Bounds, bool InContent, CardView? Anchor,
        UIElement[] Ancestors, ControlRevealClip[] Clips);

    internal void EnableControlSpotlight(Control control)
    {
        if (controlSpotlights.TryGetValue(control, out _)) return;
        var epoch = controlSpotlightEpoch;
        var surface = new ControlSpotlight(control, () => epoch == controlSpotlightEpoch && !closing,
            SetControlSpotlightActive, InvalidateCardSpotlightGeometry, () => Dark);
        controlSpotlights.Add(control, surface);
    }

    private void SetControlSpotlightActive(ControlSpotlight surface, bool active)
    {
        if (active) activeControlSpotlights.Add(surface);
        else
        {
            activeControlSpotlights.Remove(surface);
            controlSpotlightLit.Remove(surface);
            controlSpotlightNext.Remove(surface);
        }
        MarkPopupSpotlightGeometryDirty();
        InvalidateCardSpotlightGeometry();
    }

    private void ClearControlSpotlights()
    {
        ClosePopupSpotlights();
        controlSpotlightEpoch++;
        foreach (var surface in activeControlSpotlights.ToArray()) surface.Dispose();
        activeControlSpotlights.Clear();
        controlSpotlights.Clear();
        ResetControlSpotlights();
    }

    private void SetControlSpotlight(ControlSpotlight surface, Point? point)
    {
#if HALO_BATTERY_VERIFICATION
        if (surface.SetSpotlight(point) && spotlightMetrics is { } metrics) metrics.ControlBrushWrites++;
#else
        surface.SetSpotlight(point);
#endif
    }

    private void ResetControlSpotlights()
    {
        foreach (var surface in controlSpotlightLit.ToArray()) SetControlSpotlight(surface, null);
        controlSpotlightLit.Clear();
        controlSpotlightNext.Clear();
        controlSpotlightBounds.Clear();
        controlRootClipBounds.Clear();
        controlInnerClipBounds.Clear();
        controlCardOrigins.Clear();
        controlSpotlightGeometryDirty = true;
    }

    private void RebuildControlSpotlightGeometry(StackPanel content)
    {
        controlSpotlightBounds.Clear();
        var sharedClips = new Dictionary<(UIElement Owner, CardView? Anchor), ControlRevealClip>();
        foreach (var surface in activeControlSpotlights)
        {
            var control = surface.Owner;
            if (!control.IsLoaded || control.ActualWidth <= 0 || control.ActualHeight <= 0) continue;
            var ancestors = new List<UIElement>();
            var inContent = false;
            var inWindow = false;
            for (DependencyObject? node = control; node is not null; node = VisualTreeHelper.GetParent(node))
            {
                if (ReferenceEquals(node, content)) inContent = true;
                if (node is UIElement element) ancestors.Add(element);
                if (ReferenceEquals(node, root)) { inWindow = true; break; }
            }
            // Popup/dialog trees are not page controls. Their own templates and
            // keyboard/focus styling remain independent of the page reveal.
            if (!inWindow) continue;
            var contentIndex = ancestors.FindIndex(node => ReferenceEquals(node, content));
            var anchor = inContent ? ancestors.Take(contentIndex).OfType<CardView>().FirstOrDefault() : null;
            var space = inContent ? (UIElement?)anchor ?? content : root;
            var bounds = control.TransformToVisual(space).TransformBounds(
                new Rect(0, 0, control.ActualWidth, control.ActualHeight));
            var clips = new List<ControlRevealClip>();
            for (var index = 0; index < ancestors.Count; index++)
                if (ancestors[index].Clip is RectangleGeometry geometry)
                {
                    var inner = inContent && index <= contentIndex;
                    CardView? clipAnchor = null;
                    if (inner)
                        for (var parent = index; parent < contentIndex; parent++)
                            if (ancestors[parent] is CardView card) { clipAnchor = card; break; }
                    var key = (ancestors[index], clipAnchor);
                    if (!sharedClips.TryGetValue(key, out var clip))
                    {
                        clip = new(ancestors[index], geometry, inner
                            ? ancestors[index].TransformToVisual((UIElement?)clipAnchor ?? content) : null, clipAnchor);
                        sharedClips.Add(key, clip);
                    }
                    clips.Add(clip);
                }
            controlSpotlightBounds.Add(new(surface, bounds, inContent, anchor, ancestors.ToArray(), clips.ToArray()));
        }
        controlSpotlightGeometryDirty = false;
    }

    private static Rect IntersectRevealBounds(Rect first, Rect second)
    {
        var left = Math.Max(first.Left, second.Left);
        var top = Math.Max(first.Top, second.Top);
        var right = Math.Min(first.Right, second.Right);
        var bottom = Math.Min(first.Bottom, second.Bottom);
        return new Rect(left, top, Math.Max(0, right - left), Math.Max(0, bottom - top));
    }

    private void FlushControlSpotlights(Point contentPoint, Point rootPoint, Rect viewport, GeneralTransform rootToContent)
    {
        // Row geometry stays relative to its card while an expander changes
        // height. The card origins already refreshed for this frame also move
        // controls in following cards without another visual-tree walk.
        controlRootClipBounds.Clear();
        controlInnerClipBounds.Clear();
        controlCardOrigins.Clear();
        foreach (var (card, bounds) in spotlightBounds)
            controlCardOrigins[card] = new Point(bounds.X, bounds.Y);
        Rect RootClipBounds(ControlRevealClip clip)
        {
            if (!controlRootClipBounds.TryGetValue(clip.Owner, out var rect))
            {
                rect = clip.Owner.TransformToVisual(root).TransformBounds(clip.Geometry.Rect);
                controlRootClipBounds.Add(clip.Owner, rect);
            }
            return rect;
        }
        Rect InnerClipBounds(ControlRevealClip clip)
        {
            if (!controlInnerClipBounds.TryGetValue(clip, out var rect))
            {
                // Shared expander clips are transformed once, but their animated
                // Rect is read again on every frame.
                rect = clip.ToSpace!.TransformBounds(clip.Geometry.Rect);
                if (clip.Anchor is { } anchor)
                {
                    if (!controlCardOrigins.TryGetValue(anchor, out var origin)) return new Rect();
                    rect = new Rect(rect.X + origin.X, rect.Y + origin.Y, rect.Width, rect.Height);
                }
                controlInnerClipBounds.Add(clip, rect);
            }
            return rect;
        }
        foreach (var entry in controlSpotlightBounds)
        {
            var surface = entry.Surface;
            var control = surface.Owner;
            if (surface.Disposed) continue;
            // Fixed General footer controls are siblings of scrolling content.
            // Refresh this tiny root-space group; content-space geometry stays
            // cached through scrolling as it does for the existing cards.
            var bounds = entry.Bounds;
            if (entry.Anchor is { } anchor)
            {
                if (!controlCardOrigins.TryGetValue(anchor, out var origin)) continue;
                bounds = new Rect(bounds.X + origin.X, bounds.Y + origin.Y, bounds.Width, bounds.Height);
            }
            else if (!entry.InContent)
            {
                if (!control.IsLoaded) continue;
                bounds = control.TransformToVisual(root).TransformBounds(
                    new Rect(0, 0, control.ActualWidth, control.ActualHeight));
            }
            var point = entry.InContent ? contentPoint : rootPoint;
            var visible = entry.InContent ? IntersectRevealBounds(bounds, viewport) : bounds;
            if (visible.Width <= 0 || visible.Height <= 0 || !NearSpotlight(visible, point)) continue;
            if (!control.IsLoaded || !control.IsEnabled) continue;
            if (entry.Ancestors.Any(node => node.Visibility != Visibility.Visible || !node.IsHitTestVisible)) continue;
            foreach (var clip in entry.Clips)
            {
                // Expander clipping changes during its animation even when a
                // child control's own dimensions do not change.
                var clipBounds = clip.ToSpace is not null ? InnerClipBounds(clip) : RootClipBounds(clip);
                if (clip.ToSpace is null && entry.InContent) clipBounds = rootToContent.TransformBounds(clipBounds);
                visible = IntersectRevealBounds(visible, clipBounds);
                if (visible.Width <= 0 || visible.Height <= 0) break;
            }
            if (visible.Width <= 0 || visible.Height <= 0 || !NearSpotlight(visible, point)) continue;
            controlSpotlightNext.Add(surface);
            SetControlSpotlight(surface, new Point(point.X - bounds.X, point.Y - bounds.Y));
        }
        foreach (var surface in controlSpotlightLit)
            if (!controlSpotlightNext.Contains(surface)) SetControlSpotlight(surface, null);
        (controlSpotlightLit, controlSpotlightNext) = (controlSpotlightNext, controlSpotlightLit);
        controlSpotlightNext.Clear();
    }
}
