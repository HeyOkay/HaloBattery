using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.UI;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyHeaderBranding(string destination, List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        root.UpdateLayout();
        var content = (Canvas)pill.Child;
        var text = content.Children.OfType<TextBlock>().Single();
        Check("logo retains native Outfit text and the existing SVG icon renderer",
            content.Children.OfType<Image>().Single().Source is SvgImageSource &&
            text.FontFamily.Source.EndsWith("Outfit-SemiBold.ttf#Outfit") &&
            text.Inlines.OfType<Run>().First().FontFamily.Source.EndsWith("Outfit-Medium.ttf#Outfit"));
        Check("logo text and icon have full opacity without hover transitions",
            text.Opacity == 1 && text.OpacityTransition is null &&
            content.Opacity == 1 && content.OpacityTransition is null);
        var baseline = Canvas.GetTop(text) + text.BaselineOffset;
        Check("native H baseline matches the approved lightning bottom",
            Math.Abs(baseline - 670.015625 * 22 / 832) < .001);
        Check("native H top matches the approved lightning top",
            Math.Abs(baseline - text.FontSize * .7 - 208.99294929707946 * 22 / 832) < .001);
        Check("visible H-to-icon gap matches the approved SVG",
            Math.Abs(Canvas.GetLeft(text) + text.FontSize * .076 -
                834 * 22.0 / 832 - 155.76923076923077 * 22 / 832) < .001);
        var color = accent;
        try
        {
            accent = Color.FromArgb(255, 190, 37, 143);
            BuildHeader(); root.UpdateLayout();
            var battery = ((Canvas)pill.Child).Children.OfType<TextBlock>().Single().Inlines.OfType<Run>().Last();
            Check("system accent changes refresh the native Battery text brush",
                battery.Foreground is SolidColorBrush brush && brush.Color == Desaturate(accent, .8, dark ? .76 : -1));
        }
        finally { accent = color; BuildHeader(); root.UpdateLayout(); }
        await NextStartupFrameAsync();
        await Task.Delay(100);
        await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "header-wordmark.png"));
    }
}
