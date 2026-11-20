using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;
using System.Runtime.CompilerServices;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Composition;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private readonly ConditionalWeakTable<ComboBox, object> popupSpotlightBoxes = new();
    private readonly List<ScrollViewer> popupSpotlightScrolls = new();
    private readonly List<ScrollContentPresenter> popupSpotlightViewports = new();
    private readonly List<(RoutedEvent Event, PointerEventHandler Handler)> popupSpotlightHandlers = new();
    private readonly List<PopupRevealBounds> popupSpotlightBounds = new();
    private readonly HashSet<ControlSpotlight> popupSpotlightSurfaces = new();
    private readonly string popupSpotlightVisualTag = "HaloBattery.PopupSpotlights:" + Guid.NewGuid().ToString("N");
    private ContainerVisual? popupSpotlightVisual;
    private InsetClip? popupSpotlightClip;
    private HashSet<ControlSpotlight> popupSpotlightLit = new(), popupSpotlightNext = new();
    private WeakReference<ComboBox>? popupSpotlightOwner;
    private FrameworkElement? popupSpotlightChild, popupSpotlightPendingContainer;
    private RoutedEventHandler? popupSpotlightPendingLoaded;
    private Point? popupSpotlightPointer;
    private uint? popupSpotlightDrag;
    private long popupSpotlightEpoch, popupSpotlightAttachGeneration;
    private bool popupSpotlightGeometryDirty = true;
    private Rect popupSpotlightViewport;
    private sealed record PopupRevealBounds(ControlSpotlight Surface, Rect Bounds, UIElement[] Ancestors);
    private bool HasOpenPopupSpotlights => popupSpotlightOwner is not null && popupSpotlightOwner.TryGetTarget(out _);
    private bool HasPopupSpotlightPointer => popupSpotlightChild is not null && popupSpotlightPointer is not null;

    internal void EnableComboPopupSpotlights(ComboBox box)
    {
        if (popupSpotlightBoxes.TryGetValue(box, out _)) return;
        popupSpotlightBoxes.Add(box, new object());
        ComboPopupSpotlightRegistration.Install(this, box);
        var weak = new WeakReference<MainWindow>(this);
        var epoch = controlSpotlightEpoch;
        box.DropDownOpened += (sender, _) => { if (weak.TryGetTarget(out var ui) && epoch == ui.controlSpotlightEpoch &&
            !ui.closing && sender is ComboBox owner && owner.IsLoaded) ui.OpenPopupSpotlights(owner); };
        void Closed(object? sender, object args)
        {
            if (weak.TryGetTarget(out var ui) && ui.popupSpotlightOwner is { } current &&
                current.TryGetTarget(out var owner) && ReferenceEquals(owner, sender)) ui.ClosePopupSpotlights();
        }
        box.DropDownClosed += Closed;
        box.Unloaded += (sender, _) => Closed(sender, EventArgs.Empty);
    }
    private void OpenPopupSpotlights(ComboBox box)
    {
        ClosePopupSpotlights();
        ResetCardSpotlights();
        popupSpotlightOwner = new WeakReference<ComboBox>(box);
        popupSpotlightEpoch = controlSpotlightEpoch;
        AttachPopupSpotlights(popupSpotlightAttachGeneration, 0);
    }
    private void AttachPopupSpotlights(long generation, int attempt)
    {
        if (popupSpotlightChild is not null || generation != popupSpotlightAttachGeneration || popupSpotlightEpoch != controlSpotlightEpoch || closing ||
            popupSpotlightOwner is null || !popupSpotlightOwner.TryGetTarget(out var box) || !box.IsDropDownOpen) return;
        var container = box.ContainerFromIndex(Math.Max(0, box.SelectedIndex)) ?? box.ContainerFromIndex(0);
        if (container is FrameworkElement pending && !pending.IsLoaded && !ReferenceEquals(pending, popupSpotlightPendingContainer))
        {
            DetachPendingPopupSpotlightContainer();
            popupSpotlightPendingContainer = pending;
            popupSpotlightPendingLoaded = (_, _) => { DetachPendingPopupSpotlightContainer(); AttachPopupSpotlights(generation, 0); };
            pending.Loaded += popupSpotlightPendingLoaded;
        }
        var children = root.XamlRoot is { } xaml ? VisualTreeHelper.GetOpenPopupsForXamlRoot(xaml).Select(p => p.Child).OfType<FrameworkElement>() : Enumerable.Empty<FrameworkElement>();
        foreach (var child in children)
        {
            var ancestors = new List<DependencyObject>();
            for (var node = container; node is not null; node = VisualTreeHelper.GetParent(node))
            { ancestors.Add(node); if (ReferenceEquals(node, child)) break; }
            if (!ancestors.Any(node => ReferenceEquals(node, child))) continue;
            DetachPendingPopupSpotlightContainer();
            popupSpotlightChild = child;
            TryCreatePopupSpotlightOverlay(child);
            foreach (var scroll in ancestors.OfType<ScrollViewer>())
            {
                popupSpotlightScrolls.Add(scroll); scroll.ViewChanged += PopupSpotlightViewChanged;
                if (PopupContentViewport(scroll) is { } viewport)
                { popupSpotlightViewports.Add(viewport); viewport.SizeChanged += PopupSpotlightSizeChanged; }
            }
            child.SizeChanged += PopupSpotlightSizeChanged;
            child.Unloaded += PopupSpotlightUnloaded;
            void Hook(RoutedEvent routed, PointerEventHandler handler)
            { popupSpotlightHandlers.Add((routed, handler)); child.AddHandler(routed, handler, true); }
            Hook(UIElement.PointerMovedEvent, PopupSpotlightMoved); Hook(UIElement.PointerEnteredEvent, PopupSpotlightMoved);
            Hook(UIElement.PointerExitedEvent, PopupSpotlightExited); Hook(UIElement.PointerPressedEvent, PopupSpotlightPressed);
            Hook(UIElement.PointerReleasedEvent, PopupSpotlightReleased); Hook(UIElement.PointerCanceledEvent, PopupSpotlightReleased);
            Hook(UIElement.PointerCaptureLostEvent, PopupSpotlightReleased);
            MarkPopupSpotlightGeometryDirty();
            PrimePopupSpotlight(child);
            return;
        }
        // Containers can appear after DropDownOpened. No timers or frame polling.
        if (attempt < 3) DispatcherQueue.TryEnqueue(DispatcherQueuePriority.Low, () => AttachPopupSpotlights(generation, attempt + 1));
    }
    private void PrimePopupSpotlight(FrameworkElement child)
    {
        if (popupSpotlightPointer is not null || spotlightDragPointer is not null || closing || IsSettingsWindowHidden) return;
        try
        {
            // The native popup can enter under a stationary pointer before its
            // child handler is attached. Seed the normal coalescer once; no
            // polling, synthetic input, or per-item handlers are necessary.
            if (!GetBrandCursorPosition(out var screen) || child.XamlRoot is not { Content: { } island } xaml) return;
            var local = xaml.CoordinateConverter.ConvertScreenToLocal(new Windows.Graphics.PointInt32(screen.X, screen.Y));
            QueueInitialPopupSpotlight(island.TransformToVisual(child).TransformPoint(local));
        }
        catch (Exception error) when (error is System.Runtime.InteropServices.COMException or
            InvalidOperationException or ArgumentException)
        { System.Diagnostics.Debug.WriteLine("Popup reveal cursor initialization skipped: " + error); }
    }
    private void QueueInitialPopupSpotlight(Point point)
    {
        if (popupSpotlightChild is not { } child || popupSpotlightPointer is not null ||
            !double.IsFinite(point.X) || !double.IsFinite(point.Y) || point.X < 0 || point.Y < 0 ||
            point.X >= child.ActualWidth || point.Y >= child.ActualHeight) return;
        QueuePopupControlSpotlight(point);
    }
    private void DetachPendingPopupSpotlightContainer()
    {
        if (popupSpotlightPendingContainer is { } pending && popupSpotlightPendingLoaded is { } handler) pending.Loaded -= handler;
        popupSpotlightPendingContainer = null; popupSpotlightPendingLoaded = null;
    }
    private static bool PopupVisualFailure(Exception error) =>
        error is System.Runtime.InteropServices.COMException or NotSupportedException or NotImplementedException or
            InvalidCastException or InvalidOperationException or ArgumentException or TypeLoadException;
    private bool OwnsPopupSpotlightOverlay()
    {
        if (popupSpotlightVisual is null || popupSpotlightChild is null) return false;
        var attached = ElementCompositionPreview.GetElementChildVisual(popupSpotlightChild);
        return attached is not null && popupSpotlightVisual.Comment == popupSpotlightVisualTag &&
            (ReferenceEquals(attached, popupSpotlightVisual) || attached.Comment == popupSpotlightVisualTag);
    }
    private void TryCreatePopupSpotlightOverlay(FrameworkElement child)
    {
        try
        {
            // Keep the optional effect outside ItemsPresenter's implicit layout
            // clip, without changing the native popup's layout or child visuals.
            if (ElementCompositionPreview.GetElementChildVisual(child) is not null) return;
            var compositor = ElementCompositionPreview.GetElementVisual(child).Compositor;
            popupSpotlightVisual = compositor.CreateContainerVisual();
            popupSpotlightVisual.Comment = popupSpotlightVisualTag;
            popupSpotlightClip = compositor.CreateInsetClip();
            popupSpotlightVisual.Clip = popupSpotlightClip;
            if (ElementCompositionPreview.GetElementChildVisual(child) is not null)
            { ReleasePopupSpotlightOverlay(); return; }
            ElementCompositionPreview.SetElementChildVisual(child, popupSpotlightVisual);
        }
        catch (Exception error) when (PopupVisualFailure(error))
        { System.Diagnostics.Debug.WriteLine("Popup reveal overlay skipped: " + error); ReleasePopupSpotlightOverlay(); }
    }
    private void ReleasePopupSpotlightOverlay()
    {
        foreach (var surface in popupSpotlightSurfaces) surface.SetOverlay(null);
        popupSpotlightSurfaces.Clear();
        try
        {
            if (OwnsPopupSpotlightOverlay()) ElementCompositionPreview.SetElementChildVisual(popupSpotlightChild!, null);
        }
        catch (Exception error) when (PopupVisualFailure(error)) { System.Diagnostics.Debug.WriteLine(error); }
        try { popupSpotlightVisual?.Dispose(); }
        catch (Exception error) when (PopupVisualFailure(error)) { System.Diagnostics.Debug.WriteLine(error); }
        try { popupSpotlightClip?.Dispose(); }
        catch (Exception error) when (PopupVisualFailure(error)) { System.Diagnostics.Debug.WriteLine(error); }
        popupSpotlightVisual = null; popupSpotlightClip = null;
    }
    private static ScrollContentPresenter? PopupContentViewport(DependencyObject node)
    {
        if (node is ScrollContentPresenter viewport) return viewport;
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(node); index++)
            if (PopupContentViewport(VisualTreeHelper.GetChild(node, index)) is { } child) return child;
        return null;
    }
    private void PopupSpotlightMoved(object sender, PointerRoutedEventArgs args)
    {
        if (popupSpotlightChild is null || spotlightDragPointer is not null) return;
        QueuePopupControlSpotlight(args.GetCurrentPoint(popupSpotlightChild).Position);
    }
    private void PopupSpotlightExited(object sender, PointerRoutedEventArgs args)
    {
        if (popupSpotlightChild is not { } child) return;
        var point = args.GetCurrentPoint(child).Position;
        if (point.X < 0 || point.Y < 0 || point.X >= child.ActualWidth || point.Y >= child.ActualHeight) ResetPopupSpotlights();
    }
    private void PopupSpotlightPressed(object sender, PointerRoutedEventArgs args)
    {
        if (popupSpotlightChild is not { } child || !args.GetCurrentPoint(child).Properties.IsLeftButtonPressed ||
            !IsSpotlightScrollBarSource(args.OriginalSource)) return;
        BeginSpotlightScrollDrag(args.Pointer.PointerId, IsScrollBarThumbSource(args.OriginalSource));
        popupSpotlightDrag = args.Pointer.PointerId;
    }
    private void PopupSpotlightReleased(object sender, PointerRoutedEventArgs args)
    {
        if (popupSpotlightDrag != args.Pointer.PointerId) return;
        popupSpotlightDrag = null;
        EndSpotlightScrollDrag(args.Pointer.PointerId);
        if (popupSpotlightChild is { } child) QueuePopupControlSpotlight(args.GetCurrentPoint(child).Position);
    }
    internal void QueuePopupControlSpotlight(Point popupLocal)
    {
        if (!HasOpenPopupSpotlights || popupSpotlightChild is null || spotlightDragPointer is not null || closing || IsSettingsWindowHidden) return;
        popupSpotlightPointer = popupLocal;
        KeepPointerFrameClock();
        RequestSpotlightFrame();
    }
    private void PopupSpotlightViewChanged(object? sender, ScrollViewerViewChangedEventArgs args) => MarkPopupSpotlightGeometryDirty();
    private void PopupSpotlightSizeChanged(object sender, SizeChangedEventArgs args) => MarkPopupSpotlightGeometryDirty();
    private void PopupSpotlightUnloaded(object sender, RoutedEventArgs args) => ClosePopupSpotlights();
    private void MarkPopupSpotlightGeometryDirty()
    {
        popupSpotlightGeometryDirty = true;
        if (HasOpenPopupSpotlights) RequestSpotlightFrame();
    }
    private void ResetPopupSpotlights(bool preserveScope = true)
    {
        if (!preserveScope) { ClosePopupSpotlights(); return; }
        popupSpotlightPointer = null;
        foreach (var surface in popupSpotlightLit.ToArray()) SetControlSpotlight(surface, null);
        popupSpotlightLit.Clear(); popupSpotlightNext.Clear(); popupSpotlightBounds.Clear();
        popupSpotlightGeometryDirty = true;
    }
    private void ClosePopupSpotlights()
    {
        popupSpotlightAttachGeneration++;
        DetachPendingPopupSpotlightContainer();
        if (popupSpotlightDrag is { } pointer) EndSpotlightScrollDrag(pointer);
        popupSpotlightDrag = null;
        ResetPopupSpotlights();
        ReleasePopupSpotlightOverlay();
        if (popupSpotlightChild is { } child)
        {
            foreach (var (routed, handler) in popupSpotlightHandlers) child.RemoveHandler(routed, handler);
            child.SizeChanged -= PopupSpotlightSizeChanged; child.Unloaded -= PopupSpotlightUnloaded;
        }
        foreach (var scroll in popupSpotlightScrolls) scroll.ViewChanged -= PopupSpotlightViewChanged;
        foreach (var viewport in popupSpotlightViewports) viewport.SizeChanged -= PopupSpotlightSizeChanged;
        popupSpotlightHandlers.Clear(); popupSpotlightScrolls.Clear(); popupSpotlightViewports.Clear();
        popupSpotlightChild = null; popupSpotlightOwner = null;
    }
    private void RebuildPopupSpotlightGeometry(FrameworkElement child)
    {
        popupSpotlightBounds.Clear();
        popupSpotlightViewport = new Rect(0, 0, child.ActualWidth, child.ActualHeight);
        foreach (var scroll in popupSpotlightScrolls)
            popupSpotlightViewport = IntersectRevealBounds(popupSpotlightViewport, scroll.TransformToVisual(child).TransformBounds(new Rect(0, 0, scroll.ActualWidth, scroll.ActualHeight)));
        foreach (var viewport in popupSpotlightViewports)
            popupSpotlightViewport = IntersectRevealBounds(popupSpotlightViewport, viewport.TransformToVisual(child).TransformBounds(new Rect(0, 0, viewport.ActualWidth, viewport.ActualHeight)));
        if (popupSpotlightVisual is null || popupSpotlightClip is null) return;
        popupSpotlightVisual.Size = new Vector2((float)child.ActualWidth, (float)child.ActualHeight);
        popupSpotlightClip.LeftInset = (float)Math.Max(0, popupSpotlightViewport.Left);
        popupSpotlightClip.TopInset = (float)Math.Max(0, popupSpotlightViewport.Top);
        popupSpotlightClip.RightInset = (float)Math.Max(0, child.ActualWidth - popupSpotlightViewport.Right);
        popupSpotlightClip.BottomInset = (float)Math.Max(0, child.ActualHeight - popupSpotlightViewport.Bottom);
        foreach (var surface in activeControlSpotlights.ToArray())
        {
            var control = surface.Owner;
            if (surface.Disposed || control is not ComboBoxItem || !control.IsLoaded || control.ActualWidth <= 0 || control.ActualHeight <= 0) continue;
            var ancestors = new List<UIElement>();
            for (DependencyObject? node = control; node is not null; node = VisualTreeHelper.GetParent(node))
            { if (node is UIElement element) ancestors.Add(element); if (ReferenceEquals(node, child)) break; }
            if (!ancestors.Any(node => ReferenceEquals(node, child))) continue;
            var bounds = control.TransformToVisual(child).TransformBounds(new Rect(0, 0, control.ActualWidth, control.ActualHeight));
            surface.SetOverlay(popupSpotlightVisual, new Point(bounds.X, bounds.Y));
            popupSpotlightSurfaces.Add(surface);
            popupSpotlightBounds.Add(new(surface, bounds, ancestors.ToArray()));
        }
        popupSpotlightGeometryDirty = false;
    }
    private void FlushPopupSpotlights()
    {
        if (spotlightDragPointer is not null || closing || IsSettingsWindowHidden || popupSpotlightEpoch != controlSpotlightEpoch ||
            popupSpotlightChild is not { } child || popupSpotlightPointer is not { } point) return;
        try
        {
            if (!OwnsPopupSpotlightOverlay()) { ReleasePopupSpotlightOverlay(); return; }
            if (popupSpotlightGeometryDirty) RebuildPopupSpotlightGeometry(child);
        }
        catch (Exception error) when (PopupVisualFailure(error))
        { System.Diagnostics.Debug.WriteLine("Popup reveal overlay disabled: " + error); ReleasePopupSpotlightOverlay(); return; }
        foreach (var entry in popupSpotlightBounds)
        {
            var surface = entry.Surface;
            if (surface.Disposed || !surface.Owner.IsLoaded || !surface.Owner.IsEnabled) continue;
            var visible = IntersectRevealBounds(entry.Bounds, popupSpotlightViewport);
            if (visible.Width <= 0 || visible.Height <= 0 || !NearSpotlight(visible, point) ||
                entry.Ancestors.Any(node => node.Visibility != Visibility.Visible || !node.IsHitTestVisible)) continue;
            popupSpotlightNext.Add(surface);
            SetControlSpotlight(surface, new Point(point.X - entry.Bounds.X, point.Y - entry.Bounds.Y));
        }
        foreach (var surface in popupSpotlightLit.ToArray())
            if (!popupSpotlightNext.Contains(surface)) SetControlSpotlight(surface, null);
        (popupSpotlightLit, popupSpotlightNext) = (popupSpotlightNext, popupSpotlightLit);
        popupSpotlightNext.Clear();
    }
}
