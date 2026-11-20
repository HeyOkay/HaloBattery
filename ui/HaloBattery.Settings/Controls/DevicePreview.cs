using System.Numerics;
using System.Text.Json;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.UI;

namespace HaloBattery.Settings.Controls;

// Only the short transition uses a compositor stroke. Both the artwork underneath
// it and the final complete frame come from the unchanged Python tray renderer.
internal sealed partial class DevicePreview : Grid, IDisposable
{
    internal sealed record Ring(float Level, Color Color, float Radius, float Width, bool Eligible)
    {
        internal static Ring Read(JsonElement data)
        {
            var color = data.GetProperty("color");
            var result = new Ring(data.GetProperty("level").GetSingle(),
                Color.FromArgb(color[3].GetByte(), color[0].GetByte(), color[1].GetByte(), color[2].GetByte()),
                data.GetProperty("radius").GetSingle(), data.GetProperty("width").GetSingle(),
                data.GetProperty("eligible").GetBoolean());
            if (!float.IsFinite(result.Level) || result.Level is < 0 or > 1 ||
                !float.IsFinite(result.Radius) || result.Radius is <= 0 or > 40 ||
                !float.IsFinite(result.Width) || result.Width is <= 0 or > 20)
                throw new InvalidDataException("Invalid preview ring geometry");
            return result;
        }
    }

    internal Image Image { get; } = new() { Stretch = Stretch.Fill, UseLayoutRounding = true };
    private readonly ShapeVisual overlay;
    private readonly CompositionEllipseGeometry arc;
    private readonly CompositionSpriteShape stroke;
    private readonly CompositionColorBrush ink;
    private CompositionScopedBatch? batch;
    private Windows.Foundation.TypedEventHandler<object, CompositionBatchCompletedEventArgs>? finished;
    private ImageSource? complete;
    private Ring? target;
    private string? deviceKey;
    private long version;
    private bool disposed;
    private float dpiScale = 1;
    internal bool IsAnimating => batch is not null;
    internal CompositionEllipseGeometry Arc => arc;
#if HALO_BATTERY_VERIFICATION
    internal CompositionColorBrush Ink => ink;
#endif

    internal DevicePreview()
    {
        HorizontalAlignment = HorizontalAlignment.Center;
        VerticalAlignment = VerticalAlignment.Center;
        IsHitTestVisible = false;
        Children.Add(Image);
        var visual = ElementCompositionPreview.GetElementVisual(this);
        visual.IsPixelSnappingEnabled = true;
        ElementCompositionPreview.GetElementVisual(Image).IsPixelSnappingEnabled = true;
        var compositor = visual.Compositor;
        overlay = compositor.CreateShapeVisual();
        overlay.Opacity = 0;
        arc = compositor.CreateEllipseGeometry();
        arc.Center = new Vector2(40);
        ink = compositor.CreateColorBrush();
        stroke = compositor.CreateSpriteShape(arc);
        stroke.StrokeBrush = ink;
        stroke.StrokeStartCap = stroke.StrokeEndCap = CompositionStrokeCap.Round;
        overlay.Shapes.Add(stroke);
        ElementCompositionPreview.SetElementChildVisual(this, overlay);
    }

    internal void Resize(double scale)
    {
        dpiScale = (float)scale;
        Width = Height = Image.Width = Image.Height = 80 / scale;
        overlay.Size = new Vector2((float)Width);
        ApplyGeometry();
    }

    private void ApplyGeometry()
    {
        // Keep image dimensions and geometry in the same DIP coordinates.
        // Composition ellipses already trim clockwise from 12 o'clock; a separate
        // shape scale/rotation displaces the arc at non-100% system DPI.
        var center = new Vector2(40 / dpiScale);
        arc.Center = center;
        stroke.TransformMatrix = Matrix3x2.Identity;
        if (target is not { } ring) return;
        arc.Radius = new Vector2(ring.Radius / dpiScale);
        stroke.StrokeThickness = ring.Width / dpiScale;
    }

    internal void Apply(string? key, BitmapImage image, BitmapImage? background, Ring? ring, bool motion)
    {
        if (disposed) return;
        complete = image;
        if (ring is null)
        {
            if (!IsAnimating) Image.Source = image;
            return;
        }
        var previous = target;
        var changedDevice = key != deviceKey;
        deviceKey = key;
        target = ring;
        // Unknown/absent arcs have different semantics; never animate a fake
        // percentage. Geometry/theme edits also settle directly to their PNG.
        var compatible = previous is { Eligible: true } && ring.Eligible &&
            previous.Radius == ring.Radius && previous.Width == ring.Width;
        if (!motion || !compatible || background is null || !changedDevice)
        {
            if (IsAnimating && compatible && motion && !changedDevice)
                return; // A charging frame updates the final PNG, not the clock.
            Settle();
            return;
        }
        var wasAnimating = IsAnimating;
        ForgetBatch();
        var epoch = ++version;
        ApplyGeometry();
        if (!wasAnimating)
        {
            arc.TrimEnd = previous!.Level;
            ink.Color = previous.Color;
        }
        Image.Source = background;
        overlay.Opacity = 1;
        var compositor = overlay.Compositor;
        using var fill = compositor.CreateScalarKeyFrameAnimation();
        fill.InsertExpressionKeyFrame(0, "this.StartingValue");
        using var fillEase = compositor.CreateCubicBezierEasingFunction(new Vector2(.16f, 1), new Vector2(.3f, 1));
        fill.InsertKeyFrame(1, ring.Level, fillEase);
        fill.Duration = TimeSpan.FromMilliseconds(450);
        using var color = compositor.CreateColorKeyFrameAnimation();
        color.InsertExpressionKeyFrame(0, "this.StartingValue");
        using var colorEase = compositor.CreateCubicBezierEasingFunction(new Vector2(.25f, .1f), new Vector2(.25f, 1));
        color.InsertKeyFrame(1, ring.Color, colorEase);
        color.Duration = TimeSpan.FromMilliseconds(300);
        batch = compositor.CreateScopedBatch(CompositionBatchTypes.Animation);
        finished = (_, _) => { if (!disposed && version == epoch) Settle(); };
        batch.Completed += finished;
        // Replacing an active animation reads its visible StartingValue on the
        // compositor thread, including rapid A -> B -> A selection changes.
        arc.StartAnimation("TrimEnd", fill);
        ink.StartAnimation("Color", color);
        batch.End();
    }

    internal void Settle()
    {
        version++;
        ForgetBatch();
        arc.StopAnimation("TrimEnd");
        ink.StopAnimation("Color");
        ApplyGeometry();
        if (target is { } ring) { arc.TrimEnd = ring.Level; ink.Color = ring.Color; }
        overlay.Opacity = 0;
        Image.Source = complete;
    }

    private void ForgetBatch()
    {
        if (batch is null) return;
        if (finished is not null) batch.Completed -= finished;
        batch.Dispose(); batch = null; finished = null;
    }

    public void Dispose()
    {
        if (disposed) return;
        Settle(); disposed = true;
        ElementCompositionPreview.SetElementChildVisual(this, null);
        overlay.Shapes.Clear();
        stroke.Dispose(); arc.Dispose(); ink.Dispose(); overlay.Dispose();
    }
}
