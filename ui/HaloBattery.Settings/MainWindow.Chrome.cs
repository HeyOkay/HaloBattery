using System.Runtime.InteropServices;
using Microsoft.UI.Input;
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

    private const double RestingBrandOpacity = .84;
    private const int BrandFadeMilliseconds = 180;
    private TextBlock? brandLabel;
    private bool brandHovered;

    [StructLayout(LayoutKind.Sequential)]
    private struct BrandPointerPoint { public int X, Y; }
    [DllImport("user32.dll", EntryPoint = "GetCursorPos", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetBrandCursorPosition(out BrandPointerPoint point);
    [DllImport("user32.dll", EntryPoint = "ScreenToClient", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool BrandScreenToClient(nint window, ref BrandPointerPoint point);

    private void InitializeBrandHover()
    {
        // Caption input bypasses XAML. Observe it without removing the logo from
        // the native drag region or keeping a rendering/timer loop alive.
        header.PointerMoved += (_, _) => UpdateBrandHover();
        header.PointerExited += (_, _) => SetBrandHovered(false);
        try
        {
            var source = InputNonClientPointerSource.GetForWindowId(AppWindow.Id);
            source.PointerEntered += OnBrandPointerMoved;
            source.PointerMoved += OnBrandPointerMoved;
            source.PointerExited += OnBrandPointerExited;
            Closed += (_, _) =>
            {
                source.PointerEntered -= OnBrandPointerMoved;
                source.PointerMoved -= OnBrandPointerMoved;
                source.PointerExited -= OnBrandPointerExited;
            };
        }
        catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
    }

    private void OnBrandPointerMoved(InputNonClientPointerSource sender, NonClientPointerEventArgs args)
        => UpdateBrandHover();

    private void OnBrandPointerExited(InputNonClientPointerSource sender, NonClientPointerEventArgs args)
        => SetBrandHovered(false);

    private void UpdateBrandHover()
    {
        if (brandLabel is not { IsLoaded: true } label || closing) return;
        if (!GetBrandCursorPosition(out var point) || !BrandScreenToClient(hwnd, ref point)) return;
        var bounds = label.TransformToVisual(root).TransformBounds(
            new Windows.Foundation.Rect(0, 0, label.ActualWidth, label.ActualHeight));
        SetBrandHovered(bounds.Contains(new Windows.Foundation.Point(point.X / Scale, point.Y / Scale)));
    }

    private void SetBrandHovered(bool hovered)
    {
        if (brandHovered == hovered) return;
        brandHovered = hovered;
        if (brandLabel is not { } label) return;
        if (settings.AnimationsEnabled)
            label.OpacityTransition ??= new ScalarTransition
                { Duration = TimeSpan.FromMilliseconds(BrandFadeMilliseconds) };
        else
            label.OpacityTransition = null;
        label.Opacity = hovered ? 1 : RestingBrandOpacity;
    }

}
