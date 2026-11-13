using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace HaloBattery.Settings.Controls;

// The source shell's 40 x 22 switch, with native keyboard and automation behavior.
internal sealed class SettingToggle : ToggleButton
{
    private readonly MainWindow ui;
    public SettingToggle(MainWindow window)
    {
        ui = window;
        Width = MinWidth = 40;
        Height = MinHeight = 22;
        Padding = new Thickness(0);
        BorderThickness = new Thickness(0);
        UseSystemFocusVisuals = true;
        Template = (ControlTemplate)XamlReader.Load("<ControlTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' TargetType='ToggleButton'><Grid Width='40' Height='22' Background='Transparent'><Border x:Name='Track' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' CornerRadius='11'/><Ellipse x:Name='Thumb' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml' Width='16' Height='16' Fill='White' HorizontalAlignment='Left' Margin='3,3,0,3'/></Grid></ControlTemplate>");
        Checked += (_, _) => Paint();
        Unchecked += (_, _) => Paint();
        IsEnabledChanged += (_, _) => Opacity = IsEnabled ? 1 : .45;
    }
    protected override void OnApplyTemplate() { base.OnApplyTemplate(); Paint(); }
    private void Paint()
    {
        if (GetTemplateChild("Track") is Border track)
            track.Background = ui.Brush(IsChecked == true ? ui.AccentHex : ui.Dark ? "#4A4A4A" : "#C6C6C6");
        if (GetTemplateChild("Thumb") is Ellipse thumb)
            thumb.Margin = new Thickness(IsChecked == true ? 21 : 3, 3, 0, 3);
    }
}
