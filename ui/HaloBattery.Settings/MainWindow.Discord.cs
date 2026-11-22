using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Markup;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private const string DiscordCommunityUrl = "https://discord.gg/enmmpnPe95";
    private const string DiscordBrandColor = "#5865F2";
    private Button? discordButton;
    private FrameworkElement? discordNormalGlyph, discordColorGlyph;
    private bool? discordButtonDark;

    // Qlementine Icons discord-16, MIT. Preserve the two fill paths and the
    // even-odd inner contour; scale the 16-unit artwork to the 20 px glyph slot.
    private static readonly string[] DiscordPaths =
    {
        "F1 M9 8.5c0 .826.615 1.5 1.36 1.5c.762 0 1.35-.671 1.36-1.5S11.125 7 10.36 7C9.592 7 9 7.677 9 8.5M5.63 10c-.747 0-1.36-.671-1.36-1.5S4.866 7 5.63 7S7.01 7.677 7 8.5c-.013.826-.602 1.5-1.36 1.5z",
        "F0 M13.3 2.72a1 1 0 0 1 .41.342c1.71 2.47 2.57 5.29 2.25 8.53a1 1 0 0 1-.405.71c-1.16.851-2.47 1.5-3.85 1.91a.99.99 0 0 1-1.08-.357a10 10 0 0 1-.665-1.01a9.4 9.4 0 0 1-3.87 0a9 9 0 0 1-.664 1.01a1 1 0 0 1-1.09.357a12.8 12.8 0 0 1-3.85-1.91a1 1 0 0 1-.405-.711c-.269-2.79.277-5.64 2.25-8.52c.103-.151.246-.271.413-.347c.999-.452 2.05-.774 3.14-.957c.415-.07.83.128 1.04.494l.089.161q1.01-.087 2.03 0l.088-.161a1 1 0 0 1 1.04-.494c1.08.181 2.14.502 3.14.955zm-3.67.776a11 11 0 0 0-3.21 0a8 8 0 0 0-.37-.744c-.998.168-1.97.465-2.89.882c-1.83 2.68-2.32 5.29-2.08 7.86c1.07.783 2.27 1.38 3.54 1.76a8 8 0 0 0 .461-.681q.158-.26.297-.53a7.5 7.5 0 0 1-1.195-.565q.15-.109.293-.218a8.5 8.5 0 0 0 1.886.62a8.4 8.4 0 0 0 4.146-.217a8 8 0 0 0 1.04-.404q.144.117.293.218a11 11 0 0 1-.282.157a8 8 0 0 1-.915.41a9 9 0 0 0 .518.872q.117.17.241.337c1.27-.38 2.47-.975 3.54-1.76c.291-2.98-.497-5.57-2.08-7.86c-.92-.417-1.89-.712-2.89-.879q-.204.362-.37.744z",
    };

    private FrameworkElement DiscordIcon(string? color = null)
    {
        var fill = color ?? (dark ? "#FFFFFF" : "#1B1B1B");
        var paths = string.Concat(DiscordPaths.Select(data => $"<Path Data='{data}' Fill='{fill}'/>"));
        return (FrameworkElement)XamlReader.Load($"<Viewbox xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Width='20' Height='20' HorizontalAlignment='Center' VerticalAlignment='Center'><Canvas Width='16' Height='16'>{paths}</Canvas></Viewbox>");
    }

    private Button BuildDiscordButton()
    {
        var glyph = new Grid { Width = 20, Height = 20 };
        discordNormalGlyph = DiscordIcon();
        discordNormalGlyph.Opacity = InactiveChromeGlyphOpacity;
        discordColorGlyph = DiscordIcon(DiscordBrandColor);
        discordColorGlyph.Opacity = 0;
        glyph.Children.Add(discordNormalGlyph);
        glyph.Children.Add(discordColorGlyph);
        var button = new Button
        {
            Content = glyph,
            Width = 40, Height = 40, MinWidth = 40, MinHeight = 40,
            MaxWidth = 40, MaxHeight = 40, Padding = new(10, 0, 10, 0),
            HorizontalAlignment = HorizontalAlignment.Left,
            HorizontalContentAlignment = HorizontalAlignment.Center,
            VerticalContentAlignment = VerticalAlignment.Center,
            BorderThickness = new(0), CornerRadius = new(10),
            Background = Brush("#00000000"), Foreground = TextBrush
        };
        discordButton = button;
        ToolTipService.SetToolTip(button, "Join our Discord");
        AutomationProperties.SetName(button, "Join our Discord");
        button.PointerEntered += (_, _) => RefreshDiscordHover();
        button.PointerExited += (_, _) => RefreshDiscordHover();
        button.PointerCanceled += (_, _) => RefreshDiscordHover();
        button.GotFocus += (_, _) => RefreshDiscordHover();
        button.LostFocus += (_, _) => RefreshDiscordHover();
        button.Loaded += (_, _) => RefreshDiscordHover();
        button.Unloaded += (_, _) => SetDiscordHighlight(false, false);
        button.Click += async (_, _) =>
        {
            try { await Windows.System.Launcher.LaunchUriAsync(new Uri(DiscordCommunityUrl)); }
            catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
        };
        RefreshDiscordButtonTheme();
        return button;
    }

    private void RefreshDiscordButtonTheme()
    {
        if (discordButton is not { } button || discordButtonDark == dark) return;
        discordButtonDark = dark;
        button.Foreground = TextBrush;
        button.Resources["ButtonBackgroundPointerOver"] = Brush(NavigationHighlightColor);
        button.Resources["ButtonBackgroundPressed"] = Brush(dark ? DarkChromePressedBackground : "#2C000000");
        if (discordNormalGlyph is Viewbox { Child: Panel canvas })
            foreach (var path in canvas.Children.OfType<Microsoft.UI.Xaml.Shapes.Path>())
                path.Fill = TextBrush;
        RefreshDiscordHover();
    }

    private void RefreshDiscordHover() => SetDiscordHighlight(
        discordButton is { } button && (button.IsPointerOver || button.FocusState == FocusState.Keyboard),
        settings.AnimationsEnabled);

    private void SetDiscordHighlight(bool highlighted, bool motion)
    {
        if (discordNormalGlyph is null || discordColorGlyph is null) return;
        // Native opacity transitions retarget from the visible value. Only the
        // two vector layers fade; no timers, layout work or queued animations.
        foreach (var glyph in new[] { discordNormalGlyph, discordColorGlyph })
        {
            if (motion) glyph.OpacityTransition ??= new ScalarTransition { Duration = TimeSpan.FromMilliseconds(180) };
            else glyph.OpacityTransition = null;
        }
        discordNormalGlyph.Opacity = highlighted ? 0 : InactiveChromeGlyphOpacity;
        discordColorGlyph.Opacity = highlighted ? 1 : 0;
    }
}
