using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    // Measurements from the approved SVG: 832-unit icon viewport, 858-unit
    // canvas, Outfit at 658.60382 units and baseline 670.015625. Text remains
    // native/hinted, with its H ink aligned to the lightning, not its line box.
    private const double HeaderArtworkScale = 22.0 / 832;
    private const double HeaderWordmarkHeight = 858 * HeaderArtworkScale;
    private const double HeaderWordmarkFontSize = 658.6038224327436 * HeaderArtworkScale;
    private const double HeaderWordmarkTextLeft = 939.71534 * HeaderArtworkScale;
    private const double HeaderWordmarkBaseline = 670.015625 * HeaderArtworkScale;
    private readonly SvgImageSource darkHeaderLogo = new(new Uri("ms-appx:///Assets/AppIconHeaderDark.svg"));
    private readonly SvgImageSource lightHeaderLogo = new(new Uri("ms-appx:///Assets/AppIconHeaderLight.svg"));

    private Canvas BuildNativeHeaderBrand()
    {
        var content = new Canvas { Height = HeaderWordmarkHeight };
        var icon = new Image { Source = dark ? darkHeaderLogo : lightHeaderLogo,
            Width = 22, Height = 22, Stretch = Stretch.Uniform };
        Canvas.SetLeft(icon, 13 * HeaderArtworkScale);
        Canvas.SetTop(icon, 13 * HeaderArtworkScale);
        content.Children.Add(icon);
        var brand = new TextBlock
        {
            FontSize = HeaderWordmarkFontSize,
            FontFamily = new FontFamily("ms-appx:///Assets/Fonts/Outfit-SemiBold.ttf#Outfit"),
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = TextBrush, CharacterSpacing = -12, TextLineBounds = TextLineBounds.Tight,
            Opacity = 1
        };
        brand.Inlines.Add(new Run { Text = "Halo",
            FontFamily = new FontFamily("ms-appx:///Assets/Fonts/Outfit-Medium.ttf#Outfit"),
            FontWeight = Microsoft.UI.Text.FontWeights.Medium, Foreground = TextBrush });
        brand.Inlines.Add(new Run { Text = "Battery",
            Foreground = new SolidColorBrush(Desaturate(accent, .8, dark ? .76 : -1)) });
        Canvas.SetLeft(brand, HeaderWordmarkTextLeft);
        content.Children.Add(brand);
        void PositionText()
        {
            Canvas.SetTop(brand, HeaderWordmarkBaseline - brand.BaselineOffset);
            content.Width = HeaderWordmarkTextLeft + brand.DesiredSize.Width + 24 * HeaderArtworkScale;
        }
        brand.Measure(new Windows.Foundation.Size(double.PositiveInfinity, double.PositiveInfinity));
        PositionText();
        brand.SizeChanged += (_, _) => PositionText();
        brand.Loaded += (_, _) => PositionText();
        AutomationProperties.SetName(content, "HaloBattery");
        return content;
    }
}
