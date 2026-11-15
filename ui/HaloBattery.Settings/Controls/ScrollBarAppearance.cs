using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace HaloBattery.Settings.Controls;

// Installed as an app-wide implicit style, including template-created bars in
// popups, lists and editors. No visual-tree scanning or per-frame callbacks.
public sealed class ScrollBarAppearance : DependencyObject
{
    public static readonly DependencyProperty EnabledProperty = DependencyProperty.RegisterAttached(
        "Enabled", typeof(bool), typeof(ScrollBarAppearance), new PropertyMetadata(false, EnabledChanged));
    public static bool GetEnabled(DependencyObject target) => (bool)target.GetValue(EnabledProperty);
    public static void SetEnabled(DependencyObject target, bool value) => target.SetValue(EnabledProperty, value);

    internal static bool Install(ResourceDictionary resources)
    {
        // This is cosmetic: an unavailable or incompatible theme resource must
        // leave the native default usable rather than abort window startup.
        try
        {
            if (resources["DefaultScrollBarStyle"] is not Style native || native.TargetType != typeof(ScrollBar)) return false;
            var style = new Style(typeof(ScrollBar)) { BasedOn = native };
            style.Setters.Add(new Setter(EnabledProperty, true));
            resources[typeof(ScrollBar)] = style;
            return true;
        }
        catch (Exception error)
        {
            System.Diagnostics.Debug.WriteLine(error);
            return false;
        }
    }

    private static void EnabledChanged(DependencyObject target, DependencyPropertyChangedEventArgs args)
    {
        if (target is not ScrollBar bar) return;
        bar.Loaded -= Loaded;
        if (!(bool)args.NewValue) return;
        bar.Loaded += Loaded;
        if (bar.IsLoaded) Apply(bar);
    }

    private static void Loaded(object sender, RoutedEventArgs args) => Apply((ScrollBar)sender);

    internal static FrameworkElement? TemplateRoot(Control control)
    {
        control.ApplyTemplate();
        return VisualTreeHelper.GetChildrenCount(control) > 0 ? VisualTreeHelper.GetChild(control, 0) as FrameworkElement : null;
    }

    private static void Apply(ScrollBar bar)
    {
        var vertical = bar.Orientation == Orientation.Vertical;
        // Inset the overlay, not the viewport or the scrolling content.
        bar.Margin = vertical ? new Thickness(0,8,0,8) : new Thickness(8,0,8,0);
        if (TemplateRoot(bar) is not { } root) return;
        void RemoveDelay(Timeline timeline)
        {
            timeline.BeginTime = TimeSpan.Zero;
            if (timeline is Storyboard storyboard)
                foreach (var child in storyboard.Children) RemoveDelay(child);
        }
        foreach (var group in VisualStateManager.GetVisualStateGroups(root).Where(g => g.Name == "ConsciousStates"))
        {
            foreach (var state in group.States)
                if (state.Storyboard is { } storyboard) RemoveDelay(storyboard);
            foreach (var transition in group.Transitions)
                if (transition.Storyboard is { } storyboard) RemoveDelay(storyboard);
        }
        var axis = vertical ? "Vertical" : "Horizontal";
        foreach (var suffix in new[] { "SmallDecrease", "SmallIncrease" })
        {
            if (root.FindName(axis + suffix) is not RepeatButton button || TemplateRoot(button) is not { } buttonRoot) continue;
            // Native arrow grids use an asymmetric 4-DIP theme padding directly;
            // clearing only the button's Padding does not center the glyph.
            button.Padding = new Thickness(0);
            if (buttonRoot is Grid grid) grid.Padding = new Thickness(0);
            if (buttonRoot.FindName("Arrow") is not FontIcon arrow) continue;
            // Expanded thumb's visible fill is 12 DIP minus its 6-DIP clear stroke.
            arrow.FontSize = arrow.Width = arrow.Height = 6;
            arrow.IsTextScaleFactorEnabled = false;
            // Keep the same subpixel axis as the thumb at fractional DPI.
            // Rounding a 6-DIP arrow independently shifts its center by half a pixel.
            arrow.UseLayoutRounding = false;
            arrow.HorizontalAlignment = HorizontalAlignment.Center;
            arrow.VerticalAlignment = VerticalAlignment.Center;
        }
    }
}
