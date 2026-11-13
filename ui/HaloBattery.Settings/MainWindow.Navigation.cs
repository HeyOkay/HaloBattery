using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private readonly Grid navigationHost = new();
    private readonly TranslateTransform navigationTranslation = new();
    private readonly Border navigationHighlight = new()
    {
        Height = 40,
        CornerRadius = new(10),
        VerticalAlignment = VerticalAlignment.Top,
        HorizontalAlignment = HorizontalAlignment.Stretch,
        IsHitTestVisible = false,
        Visibility = Visibility.Collapsed
    };
    private readonly List<string> navigationPageIds = new();
    private Storyboard? navigationAnimation;
    private string navigationSignature = "";
    private string? navigationSelectionId;
    private double navigationTargetY;
    private bool navigationHostBuilt;

    internal double NavigationHighlightY => navigationTranslation.Y;
    internal double NavigationHighlightTargetY => navigationTargetY;
    internal bool NavigationHighlightAnimating => navigationAnimation is not null;

    private UIElement BuildNavigationHost()
    {
        if (navigationHostBuilt) return navigationHost;
        navigationHostBuilt = true;
        // Both layers use the old StackPanel's coordinate space and insets.
        // The highlight follows rail width without changing row height/opacity.
        navigationHost.Margin = nav.Margin;
        nav.Margin = new Thickness(0);
        navigationHighlight.RenderTransform = navigationTranslation;
        navigationHost.Children.Add(navigationHighlight);
        navigationHost.Children.Add(nav);
        navigationHost.Unloaded += (_, _) => StopNavigationAnimation(navigationTargetY);
        Closed += (_, _) => StopNavigationAnimation(navigationTargetY);
        return navigationHost;
    }

    private void RefreshNavigation(bool motion = true)
    {
        var pages = State.Pages.ToArray();
        var signature = JsonSerializer.Serialize(new
        {
            dark,
            language = State.Language,
            pages = pages.Select(page => new { page.Id, page.Title })
        });
        navigationHighlight.Background = Brush(dark ? "#20FFFFFF" : "#14000000");
        if (signature != navigationSignature)
        {
            navigationSignature = signature;
            nav.Children.Clear();
            navLabels.Clear();
            navigationPageIds.Clear();
            foreach (var page in pages)
            {
                var id = page.Id;
                var row = new Grid();
                row.ColumnDefinitions.Add(new() { Width = new(20) });
                row.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
                row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
                var icon = new Grid { Width = 20, Height = 20 };
                icon.Children.Add(LucideIcon(NavigationIcon(id)));
                row.Children.Add(icon);
                var label = Text(page.Title);
                label.TextWrapping = TextWrapping.NoWrap;
                label.TextTrimming = TextTrimming.Clip;
                label.Margin = new(14, 0, 0, 0);
                label.Opacity = expanded ? 1 : 0;
                Grid.SetColumn(label, 1);
                row.Children.Add(label);
                navLabels.Add(label);
                var button = new Button
                {
                    Content = row,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Stretch,
                    Height = 40,
                    Padding = new(10, 0, 10, 0),
                    CornerRadius = new(10),
                    BorderThickness = new(0),
                    Background = Brush("#00000000"),
                    Foreground = TextBrush
                };
                if (!dark)
                {
                    button.Resources["ButtonBackgroundPointerOver"] = Brush("#1F000000");
                    button.Resources["ButtonBackgroundPressed"] = Brush("#2C000000");
                    button.Resources["ButtonForegroundPointerOver"] = TextBrush;
                    button.Resources["ButtonForegroundPressed"] = TextBrush;
                }
                ToolTipService.SetToolTip(button, page.Title);
                AutomationProperties.SetName(button, page.Title);
                button.Click += (_, _) => { if (id != current) Navigate(id, true); };
                nav.Children.Add(button);
                navigationPageIds.Add(id);
            }
        }
        // FinishRail still calls BuildNavigation; update existing labels instead
        // of replacing buttons and interrupting the moving highlight.
        foreach (var label in navLabels) label.Opacity = expanded ? 1 : 0;
        SetNavigationSelection(current, motion);
    }

    internal void SetNavigationSelection(string id, bool motion = true)
    {
        var index = navigationPageIds.IndexOf(id);
        if (index < 0)
        {
            if (navigationPageIds.Count == 0)
            {
                StopNavigationAnimation(0);
                navigationSelectionId = null;
                navigationHighlight.Visibility = Visibility.Collapsed;
            }
            return;
        }
        var target = index * (40 + nav.Spacing);
        var first = navigationSelectionId is null;
        var reselect = navigationSelectionId == id && navigationTargetY == target;
        navigationSelectionId = id;
        navigationTargetY = target;
        navigationHighlight.Visibility = Visibility.Visible;
        if (first || !motion || !settings.AnimationsEnabled || closing)
        {
            StopNavigationAnimation(target);
            return;
        }
        if (reselect) return;

        // Read the effective animated property before stopping its clock. Commit
        // that value as the new base so fast retargeting starts where it is seen.
        var from = navigationTranslation.Y;
        StopNavigationAnimation(from);
        if (Math.Abs(from - target) < .001) { navigationTranslation.Y = target; return; }
        var animation = new DoubleAnimation
        {
            From = from,
            To = target,
            Duration = new Duration(TimeSpan.FromMilliseconds(220)),
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut }
        };
        Storyboard.SetTarget(animation, navigationTranslation);
        Storyboard.SetTargetProperty(animation, "Y");
        var storyboard = new Storyboard();
        storyboard.Children.Add(animation);
        navigationAnimation = storyboard;
        storyboard.Completed += (_, _) =>
        {
            if (ReferenceEquals(navigationAnimation, storyboard)) StopNavigationAnimation(target);
        };
        storyboard.Begin();
    }

    private void StopNavigationAnimation(double position)
    {
        var previous = navigationAnimation;
        navigationAnimation = null;
        // Commit before Stop exposes the base; never expose the prior start.
        navigationTranslation.Y = position;
        previous?.Stop();
        navigationTranslation.Y = position;
    }
}
