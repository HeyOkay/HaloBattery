using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    // TextBlock's character rectangle includes font ascender space. Cache the
    // real text ink once per heading/font/DPI, never during animation frames.
    private readonly Dictionary<string,double> headingInk = new();
    private readonly HashSet<string> measuringHeadingInk = new();

    private string HeadingInkKey(TextBlock heading) =>
        $"{heading.FontFamily.Source}|{heading.FontSize}|{heading.FontWeight.Weight}|{Scale}|{heading.Text}";

    private double HeadingTextTop(TextBlock heading)
    {
        var key = HeadingInkKey(heading);
        if (headingInk.TryGetValue(key, out var top)) return top;
        PrepareOverscrollHeading(heading);
        return heading.ContentStart.GetCharacterRect(Microsoft.UI.Xaml.Documents.LogicalDirection.Forward).Y;
    }

    private async void PrepareOverscrollHeading(TextBlock heading)
    {
        var key = HeadingInkKey(heading);
        if (headingInk.ContainsKey(key) || !measuringHeadingInk.Add(key)) return;
        try
        {
            // Entry opacity is deliberately zero at first. Measure after it
            // lands, so an empty/transparent entry frame cannot become a metric.
            for (var i = 0; PageEntryBlocksWheel && heading.IsLoaded && i < 30; i++)
                await Task.Delay(25);
            if (!heading.IsLoaded || heading.ActualHeight <= 0 || heading.ActualWidth <= 0) return;
            var bitmap = new RenderTargetBitmap();
            await bitmap.RenderAsync(heading);
            var pixels = (await bitmap.GetPixelsAsync()).ToArray();
            var row = bitmap.PixelWidth * 4;
            for (var y = 0; y < bitmap.PixelHeight; y++)
            {
                var ink = false;
                for (var x = 3; x < row; x += 4)
                    if (pixels[y * row + x] > 16) { ink = true; break; }
                if (!ink) continue;
                if (headingInk.Count >= 256) headingInk.Clear();
                headingInk[key] = y * heading.ActualHeight / bitmap.PixelHeight;
                if (heading.IsLoaded && pageHosts.Count > 0 && pageHosts[0].content.Children.Contains(heading))
                    PositionOverscrollIndicator();
                return;
            }
        }
        catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
        finally { measuringHeadingInk.Remove(key); }
    }
}
