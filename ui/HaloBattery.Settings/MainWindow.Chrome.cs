using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private const double InactiveChromeGlyphOpacity = .62;
    private const string DarkChromeHoverBackground = "#20FFFFFF";
    private const string DarkChromePressedBackground = "#30FFFFFF";
    private const string ExitHoverBackground = "#E81123";
    // Native caption feedback: the backdrop lightens or darkens the same red.
    private const string ExitPressedBackground = "#99E81123";

    private static double ChromeGlyphOpacity(Button button, bool selected = false) =>
        selected || button.IsPointerOver || button.FocusState == FocusState.Keyboard
            ? 1 : InactiveChromeGlyphOpacity;

    private SolidColorBrush ChromeTextBrush(Button button, bool selected = false)
    {
        var brush = TextBrush;
        brush.Opacity = ChromeGlyphOpacity(button, selected);
        return brush;
    }

    private void BindChromeGlyph(Button button, Func<FrameworkElement?> glyph,
        Func<bool>? selected = null, bool close = false, TextBlock? label = null)
    {
        var normalStroke = TextBrush;
        var hoverStroke = Brush("#FFFFFF");
        void Refresh()
        {
            if (glyph() is not { } content) return;
            var active = selected?.Invoke() ?? false;
            content.Opacity = ChromeGlyphOpacity(button, active);
            if (label is not null) label.Foreground = ChromeTextBrush(button, active);
            if (close && content is Viewbox { Child: Panel canvas })
                foreach (var path in canvas.Children.OfType<Microsoft.UI.Xaml.Shapes.Path>())
                    path.Stroke = button.IsPointerOver ? hoverStroke : normalStroke;
        }
        button.PointerEntered += (_, _) => Refresh();
        button.PointerExited += (_, _) => Refresh();
        button.PointerCanceled += (_, _) => Refresh();
        button.GotFocus += (_, _) => Refresh();
        button.LostFocus += (_, _) => Refresh();
        Refresh();
    }

    // Also used by popup reveal initialization under a stationary pointer.
    [StructLayout(LayoutKind.Sequential)]
    private struct BrandPointerPoint { public int X, Y; }
    [DllImport("user32.dll", EntryPoint = "GetCursorPos", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetBrandCursorPosition(out BrandPointerPoint point);

}
