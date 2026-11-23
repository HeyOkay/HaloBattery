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
    private const string KofiUrl = "https://ko-fi.com/halobattery";
    private const string KofiBrandColor = "#FF5E5B";

    // Qlementine Icons discord-16 and coffee-cup-16, MIT. Preserve the fill
    // paths and their fill rules; scale the 16-unit artwork to the 20 px slot.
    private static readonly string[] DiscordPaths =
    {
        "F1 M9 8.5c0 .826.615 1.5 1.36 1.5c.762 0 1.35-.671 1.36-1.5S11.125 7 10.36 7C9.592 7 9 7.677 9 8.5M5.63 10c-.747 0-1.36-.671-1.36-1.5S4.866 7 5.63 7S7.01 7.677 7 8.5c-.013.826-.602 1.5-1.36 1.5z",
        "F0 M13.3 2.72a1 1 0 0 1 .41.342c1.71 2.47 2.57 5.29 2.25 8.53a1 1 0 0 1-.405.71c-1.16.851-2.47 1.5-3.85 1.91a.99.99 0 0 1-1.08-.357a10 10 0 0 1-.665-1.01a9.4 9.4 0 0 1-3.87 0a9 9 0 0 1-.664 1.01a1 1 0 0 1-1.09.357a12.8 12.8 0 0 1-3.85-1.91a1 1 0 0 1-.405-.711c-.269-2.79.277-5.64 2.25-8.52c.103-.151.246-.271.413-.347c.999-.452 2.05-.774 3.14-.957c.415-.07.83.128 1.04.494l.089.161q1.01-.087 2.03 0l.088-.161a1 1 0 0 1 1.04-.494c1.08.181 2.14.502 3.14.955zm-3.67.776a11 11 0 0 0-3.21 0a8 8 0 0 0-.37-.744c-.998.168-1.97.465-2.89.882c-1.83 2.68-2.32 5.29-2.08 7.86c1.07.783 2.27 1.38 3.54 1.76a8 8 0 0 0 .461-.681q.158-.26.297-.53a7.5 7.5 0 0 1-1.195-.565q.15-.109.293-.218a8.5 8.5 0 0 0 1.886.62a8.4 8.4 0 0 0 4.146-.217a8 8 0 0 0 1.04-.404q.144.117.293.218a11 11 0 0 1-.282.157a8 8 0 0 1-.915.41a9 9 0 0 0 .518.872q.117.17.241.337c1.27-.38 2.47-.975 3.54-1.76c.291-2.98-.497-5.57-2.08-7.86c-.92-.417-1.89-.712-2.89-.879q-.204.362-.37.744z",
    };
    private static readonly string[] KofiPaths =
    {
        "F0 M12 6a1 1 0 0 1 1 1h.5a2.5 2.5 0 0 1 0 5h-.602a5 5 0 0 1-3.92 3.9c-.38.076-.806.092-1.37.096h-1.22c-.561-.004-.987-.02-1.37-.096a5.02 5.02 0 0 1-3.93-3.93c-.101-.507-.096-1.09-.096-1.98v-3a1 1 0 0 1 1-1h10zM2 10c0 .929 0 1.39.077 1.78a3.99 3.99 0 0 0 3.14 3.14c.386.077.851.077 1.78.077c.93 0 1.39 0 1.78-.077a3.99 3.99 0 0 0 3.14-3.14c.077-.386.077-.851.077-1.78V7h-10v3zm11 .609q-.002.207-.008.391h.508a1.5 1.5 0 0 0 0-3H13z",
        "F1 M1.38.015a.5.5 0 0 1 .606.364l.031.125c.11.442.382.827.761 1.08c.586.39 1.01.986 1.18 1.67l.031.126a.5.5 0 0 1-.97.242l-.03-.125a1.84 1.84 0 0 0-.762-1.08a2.84 2.84 0 0 1-1.18-1.67L1.016.62A.5.5 0 0 1 1.38.015m3 0a.5.5 0 0 1 .606.364l.031.125c.11.442.382.827.761 1.08c.586.39 1.01.986 1.18 1.67l.031.126a.5.5 0 0 1-.97.242l-.03-.125a1.84 1.84 0 0 0-.762-1.08a2.84 2.84 0 0 1-1.18-1.67L4.016.62A.5.5 0 0 1 4.38.015m3 0a.5.5 0 0 1 .606.364l.031.125c.11.442.382.827.761 1.08c.586.39 1.01.986 1.18 1.67l.031.126a.5.5 0 0 1-.97.242l-.03-.125a1.84 1.84 0 0 0-.762-1.08a2.84 2.84 0 0 1-1.18-1.67L7.016.62A.5.5 0 0 1 7.38.015",
    };

    // A rail shortcut that opens a link: a neutral glyph that fades into the
    // brand colour on hover or keyboard focus.
    private sealed class LinkButton
    {
        public required Button Button { get; init; }
        public required string[] Paths { get; init; }
        public required FrameworkElement NormalGlyph { get; init; }
        public required FrameworkElement ColorGlyph { get; init; }
        public string? TooltipKey { get; init; }
        public bool? Dark { get; set; }
        public string? Language { get; set; }
    }

    private readonly List<LinkButton> linkButtons = new();

    private FrameworkElement BrandIcon(string[] data, string? color = null)
    {
        var fill = color ?? (dark ? "#FFFFFF" : "#1B1B1B");
        var paths = string.Concat(data.Select(d => $"<Path Data='{d}' Fill='{fill}'/>"));
        return (FrameworkElement)XamlReader.Load($"<Viewbox xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Width='20' Height='20' HorizontalAlignment='Center' VerticalAlignment='Center'><Canvas Width='16' Height='16'>{paths}</Canvas></Viewbox>");
    }

    private UIElement BuildLinkButtons()
    {
        var panel = new StackPanel { Spacing = nav.Spacing };
        panel.Children.Add(BuildLinkButton(KofiPaths, KofiBrandColor, KofiUrl, "Support on Ko-fi", "settings.support_kofi"));
        panel.Children.Add(BuildLinkButton(DiscordPaths, DiscordBrandColor, DiscordCommunityUrl, "Join our Discord"));
        return panel;
    }

    private Button BuildLinkButton(string[] paths, string brandColor, string url, string name, string? tooltipKey = null)
    {
        var glyph = new Grid { Width = 20, Height = 20 };
        var normal = BrandIcon(paths);
        normal.Opacity = InactiveChromeGlyphOpacity;
        var color = BrandIcon(paths, brandColor);
        color.Opacity = 0;
        glyph.Children.Add(normal);
        glyph.Children.Add(color);
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
        var link = new LinkButton { Button = button, Paths = paths, NormalGlyph = normal, ColorGlyph = color, TooltipKey = tooltipKey };
        linkButtons.Add(link);
        ToolTipService.SetToolTip(button, name);
        AutomationProperties.SetName(button, name);
        button.PointerEntered += (_, _) => RefreshLinkHover(link);
        button.PointerExited += (_, _) => RefreshLinkHover(link);
        button.PointerCanceled += (_, _) => RefreshLinkHover(link);
        button.GotFocus += (_, _) => RefreshLinkHover(link);
        button.LostFocus += (_, _) => RefreshLinkHover(link);
        button.Loaded += (_, _) => RefreshLinkHover(link);
        button.Unloaded += (_, _) => SetLinkHighlight(link, false, false);
        button.Click += async (_, _) =>
        {
            try { await Windows.System.Launcher.LaunchUriAsync(new Uri(url)); }
            catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
        };
        RefreshLinkButton(link);
        return button;
    }

    private void RefreshLinkButtons()
    {
        foreach (var link in linkButtons) RefreshLinkButton(link);
    }

    private void RefreshLinkButton(LinkButton link)
    {
        // The label follows the app language; the English name stands in
        // until the backend has sent its catalog.
        if (link.TooltipKey is { } key && link.Language != State.Language && State.Labels.ContainsKey(key))
        {
            link.Language = State.Language;
            ToolTipService.SetToolTip(link.Button, T(key));
            AutomationProperties.SetName(link.Button, T(key));
        }
        if (link.Dark == dark) return;
        link.Dark = dark;
        var button = link.Button;
        button.Foreground = TextBrush;
        button.Resources["ButtonBackgroundPointerOver"] = Brush(NavigationHighlightColor);
        button.Resources["ButtonBackgroundPressed"] = Brush(dark ? DarkChromePressedBackground : "#2C000000");
        if (link.NormalGlyph is Viewbox { Child: Panel canvas })
            foreach (var path in canvas.Children.OfType<Microsoft.UI.Xaml.Shapes.Path>())
                path.Fill = TextBrush;
        RefreshLinkHover(link);
    }

    private void RefreshLinkHover(LinkButton link) => SetLinkHighlight(link,
        link.Button.IsPointerOver || link.Button.FocusState == FocusState.Keyboard,
        settings.AnimationsEnabled);

    private static void SetLinkHighlight(LinkButton link, bool highlighted, bool motion)
    {
        // Native opacity transitions retarget from the visible value. Only the
        // two vector layers fade; no timers, layout work or queued animations.
        foreach (var glyph in new[] { link.NormalGlyph, link.ColorGlyph })
        {
            if (motion) glyph.OpacityTransition ??= new ScalarTransition { Duration = TimeSpan.FromMilliseconds(180) };
            else glyph.OpacityTransition = null;
        }
        link.NormalGlyph.Opacity = highlighted ? 0 : InactiveChromeGlyphOpacity;
        link.ColorGlyph.Opacity = highlighted ? 1 : 0;
    }
}
