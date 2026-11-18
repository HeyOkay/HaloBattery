using System;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HaloBattery.Settings.Controls;

// Style setters carry only a bool across the XAML ABI, never a MainWindow.
public sealed partial class ComboPopupSpotlightRegistration : DependencyObject
{
    private static WeakReference<MainWindow>? window;
    public static readonly DependencyProperty EnabledProperty = DependencyProperty.RegisterAttached(
        "Enabled", typeof(bool), typeof(ComboPopupSpotlightRegistration), new PropertyMetadata(false, Changed));
    public static bool GetEnabled(DependencyObject owner) => (bool)owner.GetValue(EnabledProperty);
    public static void SetEnabled(DependencyObject owner, bool value) => owner.SetValue(EnabledProperty, value);
    internal static void Install(MainWindow owner, ComboBox box)
    {
        window = new WeakReference<MainWindow>(owner);
        var style = new Style(typeof(ComboBoxItem)) { BasedOn = box.ItemContainerStyle };
        style.Setters.Add(new Setter(EnabledProperty, true));
        box.ItemContainerStyle = style;
    }
    private static void Changed(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        if (args.NewValue is true && sender is ComboBoxItem item && window is not null && window.TryGetTarget(out var owner))
            owner.EnableControlSpotlight(item); // The shared helper activates only on Loaded.
    }
}
