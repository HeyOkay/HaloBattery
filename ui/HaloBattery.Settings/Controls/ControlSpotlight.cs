using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Numerics;
using System.Runtime.InteropServices;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Windows.Foundation;
using Color = Windows.UI.Color;

namespace HaloBattery.Settings.Controls;

// Pointer scheduling and page/clip geometry belong to MainWindow. This owner
// adds only a lazy, non-layout Composition stroke to an existing control.
internal sealed partial class ControlSpotlight : IDisposable
{
    private readonly Func<bool> isCurrentEpoch;
    private readonly Action<ControlSpotlight, bool> setActive;
    private readonly Action invalidate;
    private readonly Func<bool> isDark;
    private readonly string visualTag = "HaloBattery.ControlSpotlight:" + Guid.NewGuid().ToString("N");
    private readonly List<(DependencyProperty property, long token)> tokens = new();
    private readonly List<CompositionObject> resources = new();
    private ShapeVisual? visual;
    private ContainerVisual? overlayHost;
    private string? overlayHostTag;
    private Point overlayOffset;
    private CompositionRoundedRectangleGeometry? geometry;
    private CompositionRadialGradientBrush? brush;
    private CompositionColorGradientStop? centerStop, edgeStop;
    private bool active, visible, effectDisabled;
    private bool? brushDark;
    private double geometryWidth = -1, geometryHeight = -1, geometryRadius = -1, geometryInset = -1;

    internal Control Owner { get; }
    internal Point? SpotlightPosition { get; private set; }
    internal bool HasVisual => visual is not null;
    internal ShapeVisual? HighlightVisual => visual;
    internal bool IsVisible => visible;
    internal bool Disposed { get; private set; }

    internal ControlSpotlight(Control owner, Func<bool> isCurrentEpoch,
        Action<ControlSpotlight, bool> setActive, Action invalidate, Func<bool> isDark)
    {
        Owner = owner ?? throw new ArgumentNullException(nameof(owner));
        this.isCurrentEpoch = isCurrentEpoch ?? throw new ArgumentNullException(nameof(isCurrentEpoch));
        this.setActive = setActive ?? throw new ArgumentNullException(nameof(setActive));
        this.invalidate = invalidate ?? throw new ArgumentNullException(nameof(invalidate));
        this.isDark = isDark ?? throw new ArgumentNullException(nameof(isDark));
        Owner.Loaded += OnLoaded;
        Owner.Unloaded += OnUnloaded;
        Owner.SizeChanged += OnSizeChanged;
        foreach (var property in new[] { FrameworkElement.MarginProperty,
            FrameworkElement.HorizontalAlignmentProperty, FrameworkElement.VerticalAlignmentProperty,
            UIElement.VisibilityProperty, Control.IsEnabledProperty, Control.CornerRadiusProperty })
            tokens.Add((property, Owner.RegisterPropertyChangedCallback(property, OnPropertyChanged)));
        if (Owner.IsLoaded) OnLoaded(Owner, new RoutedEventArgs());
    }

    private bool Current()
    {
        if (Disposed) return false;
        if (isCurrentEpoch()) return true;
        Dispose();
        return false;
    }

    private void ChangeActive(bool value)
    {
        if (active == value) return;
        active = value;
        setActive(this, value);
    }

    private void OnLoaded(object sender, RoutedEventArgs args)
    {
        if (!Current()) return;
        effectDisabled = false;
        ChangeActive(true);
        invalidate();
    }

    private void OnUnloaded(object sender, RoutedEventArgs args)
    {
        if (!Current()) return;
        ChangeActive(false);
        ReleaseVisual();
        // Loaded/property hooks remain: a retained control can be reparented.
        invalidate();
    }

    private void OnSizeChanged(object sender, SizeChangedEventArgs args)
    {
        if (!Current()) return;
        if (visual is not null) TryUpdateGeometry();
        invalidate();
    }

    private void OnPropertyChanged(DependencyObject sender, DependencyProperty property)
    {
        if (!Current()) return;
        if (property == Control.CornerRadiusProperty && visual is not null) TryUpdateGeometry();
        if ((property == UIElement.VisibilityProperty || property == Control.IsEnabledProperty) &&
            (Owner.Visibility != Visibility.Visible || !Owner.IsEnabled)) SetSpotlight(null);
        invalidate();
    }

    internal bool SetSpotlight(Point? point)
    {
        if (!Current()) return false;
        if (effectDisabled || !active || !Owner.IsLoaded || Owner.Visibility != Visibility.Visible || !Owner.IsEnabled ||
            (Owner is ComboBoxItem && overlayHost is null) ||
            point is { } input && (!double.IsFinite(input.X) || !double.IsFinite(input.Y))) point = null;
        if (point is null)
        {
            var changed = SpotlightPosition is not null || visible;
            SpotlightPosition = null;
            if (!visible) return changed;
            try { if (visual is not null && OwnsChildSlot()) visual.Opacity = 0; }
            catch (Exception error) when (OptionalVisualFailure(error)) { DisableEffect(error); }
            visible = false;
            return changed;
        }
        try
        {
            if (!EnsureVisual() || !UpdateGeometry()) return HideInvalidGeometry();
            if (!OwnsChildSlot())
            {
                DisableEffect(new InvalidOperationException("The control child-visual slot changed owner."));
                return false;
            }
            var colorsChanged = UpdateColors();
            if (SpotlightPosition == point && visible) return colorsChanged;
            brush!.EllipseCenter = new Vector2((float)point.Value.X, (float)point.Value.Y);
            if (!visible) visual!.Opacity = 1;
            SpotlightPosition = point;
            visible = true;
            return true;
        }
        catch (Exception error) when (OptionalVisualFailure(error))
        {
            DisableEffect(error);
            return false;
        }
    }

    private bool HideInvalidGeometry() => SetSpotlight(null);

    // Popup rows share a visual above their ItemsPresenter's layout clip. The
    // brush remains row-local; only the visual's placement changes on scroll.
    internal void SetOverlay(ContainerVisual? host, Point offset = default)
    {
        if (Disposed) return;
        try
        {
            if (!ReferenceEquals(overlayHost, host))
            {
                ReleaseVisual();
                overlayHost = host;
                overlayHostTag = host?.Comment;
                effectDisabled = false;
            }
            if (overlayOffset == offset) return;
            overlayOffset = offset;
            if (visual is not null && overlayHost is not null && OwnsChildSlot())
                visual.Offset = new Vector3((float)offset.X, (float)offset.Y, 0);
        }
        catch (Exception error) when (OptionalVisualFailure(error)) { DisableEffect(error); }
    }

    private T Own<T>(T resource) where T : CompositionObject
    {
        resources.Add(resource);
        return resource;
    }

    private bool EnsureVisual()
    {
        if (effectDisabled) return false;
        if (visual is not null) return true;
        // Do not overwrite another effect attached to this control. The helper
        // never reparents or disposes a foreign Composition visual.
        if (overlayHost is null && ElementCompositionPreview.GetElementChildVisual(Owner) is not null)
        {
            effectDisabled = true;
            Debug.WriteLine("Control spotlight skipped: child-visual slot is already owned.");
            return false;
        }
        var compositor = ElementCompositionPreview.GetElementVisual(Owner).Compositor;
        geometry = Own(compositor.CreateRoundedRectangleGeometry());
        brush = Own(compositor.CreateRadialGradientBrush());
        brush.MappingMode = CompositionMappingMode.Absolute;
        brush.ExtendMode = CompositionGradientExtendMode.Clamp;
        brush.EllipseRadius = new Vector2((float)CardView.SpotlightRadius);
        brush.GradientOriginOffset = Vector2.Zero;
        centerStop = Own(compositor.CreateColorGradientStop(0, Color.FromArgb(0, 0, 0, 0)));
        edgeStop = Own(compositor.CreateColorGradientStop(1, Color.FromArgb(0, 0, 0, 0)));
        brush.ColorStops.Add(centerStop);
        brush.ColorStops.Add(edgeStop);
        var stroke = Own(compositor.CreateSpriteShape(geometry));
        stroke.FillBrush = null;
        stroke.StrokeBrush = brush;
        stroke.StrokeThickness = 1;
        visual = Own(compositor.CreateShapeVisual());
        visual.Comment = visualTag;
        visual.Opacity = 0;
        if (overlayHost is not null) visual.Offset = new Vector3((float)overlayOffset.X, (float)overlayOffset.Y, 0);
        visual.Shapes.Add(stroke);
        UpdateGeometry();
        UpdateColors();
        if (overlayHost is not null)
        {
            if (overlayHost.Comment != overlayHostTag)
            {
                effectDisabled = true;
                ReleaseVisual();
                return false;
            }
            overlayHost.Children.InsertAtTop(visual);
            return true;
        }
        if (ElementCompositionPreview.GetElementChildVisual(Owner) is not null)
        {
            effectDisabled = true;
            ReleaseVisual();
            return false;
        }
        ElementCompositionPreview.SetElementChildVisual(Owner, visual);
        return true;
    }

    private bool OwnsChildSlot()
    {
        if (visual is null) return false;
        if (overlayHost is not null)
        {
            var parent = visual.Parent;
            return visual.Comment == visualTag && parent is not null && overlayHost.Comment == overlayHostTag &&
                (ReferenceEquals(parent, overlayHost) || parent.Comment == overlayHostTag);
        }
        var child = ElementCompositionPreview.GetElementChildVisual(Owner);
        // A projection can return a different managed wrapper for the same
        // visual. The private per-helper tag recognizes only our native node.
        return child is not null && (ReferenceEquals(child, visual) || child.Comment == visualTag);
    }

    private bool UpdateGeometry()
    {
        if (visual is null || geometry is null) return false;
        var width = Owner.ActualWidth;
        var height = Owner.ActualHeight;
        var corners = Owner.CornerRadius;
        if (!double.IsFinite(width) || !double.IsFinite(height) || width <= 1 || height <= 1) return false;
        // Registered pill/round controls have uniform corners. Refuse a shape
        // that could paint an incorrect outline on a future asymmetric control.
        if (corners.TopLeft != corners.TopRight || corners.TopLeft != corners.BottomLeft ||
            corners.TopLeft != corners.BottomRight) return false;
        // Keep popup strokes one physical pixel inside their row. This leaves
        // room for antialiasing without adding padding to the native popup.
        var scale = Owner.XamlRoot?.RasterizationScale ?? 1;
        var inset = Owner is ComboBoxItem ? 1 / scale : 0;
        var shapeWidth = width - 1 - 2 * inset;
        var shapeHeight = height - 1 - 2 * inset;
        if (!double.IsFinite(inset) || shapeWidth <= 0 || shapeHeight <= 0) return false;
        var radius = Math.Max(0, Math.Min(corners.TopLeft - .5 - inset, Math.Min(shapeWidth, shapeHeight) / 2));
        if (width == geometryWidth && height == geometryHeight && radius == geometryRadius && inset == geometryInset) return true;
        visual.Size = new Vector2((float)width, (float)height);
        geometry.Offset = new Vector2((float)(.5 + inset));
        geometry.Size = new Vector2((float)shapeWidth, (float)shapeHeight);
        geometry.CornerRadius = new Vector2((float)radius);
        geometryWidth = width; geometryHeight = height; geometryRadius = radius; geometryInset = inset;
        return true;
    }

    private bool UpdateColors()
    {
        var dark = isDark();
        if (brushDark == dark || centerStop is null || edgeStop is null) return false;
        var channel = dark ? (byte)255 : (byte)0;
        centerStop.Color = Color.FromArgb(dark ? (byte)0x4B : (byte)0x30, channel, channel, channel);
        edgeStop.Color = Color.FromArgb(0, channel, channel, channel);
        brushDark = dark;
        return true;
    }

    private void TryUpdateGeometry()
    {
        try { if (!UpdateGeometry()) SetSpotlight(null); }
        catch (Exception error) when (OptionalVisualFailure(error)) { DisableEffect(error); }
    }

    private static bool OptionalVisualFailure(Exception error) =>
        error is COMException or NotSupportedException or NotImplementedException or InvalidCastException or
            InvalidOperationException or ArgumentException or TypeLoadException;

    private void DisableEffect(Exception error)
    {
        effectDisabled = true;
        Debug.WriteLine("Control spotlight disabled: " + error);
        ReleaseVisual();
    }

    private void ReleaseVisual()
    {
        SpotlightPosition = null;
        visible = false;
        try
        {
            if (OwnsChildSlot())
            {
                if (overlayHost is not null) overlayHost.Children.Remove(visual!);
                else ElementCompositionPreview.SetElementChildVisual(Owner, null);
            }
        }
        catch (Exception error) when (OptionalVisualFailure(error)) { Debug.WriteLine(error); }
        for (var index = resources.Count - 1; index >= 0; index--)
        {
            try { resources[index].Dispose(); }
            catch (Exception error) when (OptionalVisualFailure(error)) { Debug.WriteLine(error); }
        }
        resources.Clear();
        visual = null; geometry = null; brush = null; centerStop = null; edgeStop = null;
        brushDark = null;
        geometryWidth = geometryHeight = geometryRadius = geometryInset = -1;
    }

    public void Dispose()
    {
        if (Disposed) return;
        Disposed = true;
        try
        {
            Owner.Loaded -= OnLoaded;
            Owner.Unloaded -= OnUnloaded;
            Owner.SizeChanged -= OnSizeChanged;
        }
        catch (Exception error) when (OptionalVisualFailure(error)) { Debug.WriteLine(error); }
        foreach (var (property, token) in tokens)
        {
            try { Owner.UnregisterPropertyChangedCallback(property, token); }
            catch (Exception error) when (OptionalVisualFailure(error)) { Debug.WriteLine(error); }
        }
        tokens.Clear();
        ChangeActive(false);
        ReleaseVisual();
        overlayHost = null; overlayHostTag = null;
        invalidate();
    }
}
