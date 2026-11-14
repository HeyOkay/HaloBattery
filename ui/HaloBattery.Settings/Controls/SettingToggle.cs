using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Shapes;

namespace HaloBattery.Settings.Controls;

// The source shell's 40 x 22 switch, with native keyboard and automation behavior.
internal sealed class SettingToggle : ToggleButton
{
    private readonly MainWindow ui;
    private Grid? thumbHost;
    private Border? shadowHost;
    private Ellipse? thumb;
    private SpriteVisual? shadowVisual;
    private DropShadow? thumbShadow;

    public SettingToggle(MainWindow window)
    {
        ui = window;
        Width = MinWidth = 40;
        Height = MinHeight = 22;
        Padding = new Thickness(0);
        BorderThickness = new Thickness(0);
        UseSystemFocusVisuals = true;
        Template = (ControlTemplate)XamlReader.Load("<ControlTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' TargetType='ToggleButton'><Grid Width='40' Height='22' Background='Transparent'><Border x:Name='Track' CornerRadius='11'/><Grid x:Name='ThumbHost' Width='16' Height='16' HorizontalAlignment='Left' Margin='3,3,0,3'><Border x:Name='ThumbShadowHost' Background='Transparent' IsHitTestVisible='False'/><Ellipse x:Name='Thumb' Fill='White'/></Grid></Grid></ControlTemplate>");
        Checked += (_, _) => Paint();
        Unchecked += (_, _) => Paint();
        IsEnabledChanged += (_, _) => Opacity = IsEnabled ? 1 : .45;
        Loaded += (_, _) => AttachThumbShadow();
        Unloaded += (_, _) => DetachThumbShadow();
    }

    protected override void OnApplyTemplate()
    {
        DetachThumbShadow();
        base.OnApplyTemplate();
        thumbHost = GetTemplateChild("ThumbHost") as Grid;
        shadowHost = GetTemplateChild("ThumbShadowHost") as Border;
        thumb = GetTemplateChild("Thumb") as Ellipse;
        Paint();
        if (IsLoaded) AttachThumbShadow();
    }

    private void Paint()
    {
        if (GetTemplateChild("Track") is Border track)
            track.Background = ui.Brush(IsChecked == true ? ui.AccentHex : ui.Dark ? "#4A4A4A" : "#C6C6C6");
        if (thumbHost is not null)
            thumbHost.Margin = new Thickness(IsChecked == true ? 21 : 3, 3, 0, 3);
    }

    private void AttachThumbShadow()
    {
        if (shadowVisual is not null || shadowHost is null || thumb is null) return;
        // A separate child visual behind the ellipse follows its XAML container;
        // never replace the XAML-owned thumb visual or its positioning.
        // https://learn.microsoft.com/windows/apps/develop/composition/using-the-visual-layer-with-xaml
        var compositor = ElementCompositionPreview.GetElementVisual(shadowHost).Compositor;
        thumbShadow = compositor.CreateDropShadow();
        thumbShadow.Color = Windows.UI.Color.FromArgb(255, 0, 0, 0);
        thumbShadow.Opacity = ui.Dark ? .24f : .20f;
        thumbShadow.BlurRadius = 4;
        thumbShadow.Offset = new Vector3(0, 1.5f, 0);
        thumbShadow.Mask = thumb.GetAlphaMask();
        shadowVisual = compositor.CreateSpriteVisual();
        shadowVisual.Size = new Vector2(16, 16);
        shadowVisual.Shadow = thumbShadow;
        ElementCompositionPreview.SetElementChildVisual(shadowHost, shadowVisual);
    }

    private void DetachThumbShadow()
    {
        if (shadowHost is not null)
            ElementCompositionPreview.SetElementChildVisual(shadowHost, null);
        if (shadowVisual is not null)
        {
            shadowVisual.Shadow = null;
            shadowVisual.Dispose();
            shadowVisual = null;
        }
        if (thumbShadow is not null)
        {
            thumbShadow.Mask = null;
            thumbShadow.Dispose();
            thumbShadow = null;
        }
    }
}
