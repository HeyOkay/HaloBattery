using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyGlobalScrollBars(string destination, List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        void Appearance(string name, ScrollBar bar)
        {
            var vertical = bar.Orientation == Orientation.Vertical;
            Check(name + ": inherits the application default", ScrollBarAppearance.GetEnabled(bar) &&
                bar.Margin == (vertical ? new Thickness(0,8,0,8) : new Thickness(8,0,8,0)));
            var template = TemplateRoot(bar)!;
            var axis = vertical ? "Vertical" : "Horizontal";
            foreach (var suffix in new[] { "SmallDecrease", "SmallIncrease" })
            {
                var button = (RepeatButton)template.FindName(axis + suffix);
                var buttonRoot = (Grid)TemplateRoot(button)!;
                var arrow = (FontIcon)buttonRoot.FindName("Arrow");
                Check(name + ": " + suffix + " has neutral padding and centered alignment", button.Padding == new Thickness(0) &&
                    buttonRoot.Padding == new Thickness(0) && arrow.FontSize == 6 &&
                    arrow.HorizontalAlignment == HorizontalAlignment.Center && arrow.VerticalAlignment == VerticalAlignment.Center);
            }
        }
        var picker = PillComboBox();
        picker.HorizontalAlignment = HorizontalAlignment.Stretch;
        picker.MaxDropDownHeight = 180;
        foreach (var index in Enumerable.Range(0,60)) picker.Items.Add("Fixture choice " + index);
        picker.SelectedIndex = 0;
        var inner = new ScrollViewer { Height = 120, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = Text(string.Join("\n", Enumerable.Repeat("Scrollable license notice",80))) };
        var horizontal = new ScrollViewer { Height = 60, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollMode = ScrollMode.Enabled, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
            Content = new Border { Width = 1200, Height = 20, Background = SubtleBrush } };
        var gallery = new GridView { Height = 150, ItemsSource = Enumerable.Range(0,160).Select(i => "Icon " + i).ToArray() };
        ScrollViewer.SetVerticalScrollBarVisibility(gallery, ScrollBarVisibility.Auto);
        var body = new StackPanel { Spacing = 12, Children = { picker, inner, horizontal, gallery } };
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "Scrollbar fixture", Content = body, CloseButtonText = "Close" };
        var shown = dialog.ShowAsync();
        try
        {
            await Task.Delay(180);
            body.UpdateLayout();
            Check("dialog notice genuinely overflows", inner.ScrollableHeight > 0);
            Appearance("dialog notice", VerificationDescendants(inner).OfType<ScrollBar>().First(b => b.Orientation == Orientation.Vertical));
            Check("horizontal content genuinely overflows", horizontal.ScrollableWidth > 0);
            Appearance("horizontal scroller", VerificationDescendants(horizontal).OfType<ScrollBar>().First(b => b.Orientation == Orientation.Horizontal));
            Appearance("nested gallery", VerificationDescendants(gallery).OfType<ScrollBar>().First(b => b.Orientation == Orientation.Vertical));
            picker.IsDropDownOpen = true;
            await Task.Delay(150);
            var popup = VisualTreeHelper.GetOpenPopupsForXamlRoot(root.XamlRoot).First(p => p.Child is not null &&
                VerificationDescendants(p.Child).OfType<ComboBoxItem>().Any(i => i.Content?.ToString()?.StartsWith("Fixture choice ") == true));
            var popupScroll = VerificationDescendants(popup.Child).OfType<ScrollViewer>().First(s => s.ScrollableHeight > 0);
            Appearance("dropdown popup", VerificationDescendants(popupScroll).OfType<ScrollBar>().First(b => b.Orientation == Orientation.Vertical));
            if (Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported())
                await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "global-scrollbars.composited.png"));
        }
        finally
        {
            picker.IsDropDownOpen = false;
            dialog.Hide();
            await shown;
        }
    }
}
