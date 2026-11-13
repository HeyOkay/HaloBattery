using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private Border? generalFooter;
    private Border? generalFooterContent;

    // The overscroll host keeps this in its Auto bottom row, outside the
    // scrolling and pulled page content. Collapsing it releases the whole row.
    private FrameworkElement BuildGeneralFooter()
    {
        generalFooterContent = new Border { HorizontalAlignment = HorizontalAlignment.Left };
        generalFooter = new Border { Child = generalFooterContent, Visibility = Visibility.Collapsed };
        generalFooter.SizeChanged += (_, _) => PositionGeneralFooter();
        return generalFooter;
    }

    private void RefreshGeneralFooter(string page)
    {
        if (generalFooter is null || generalFooterContent is null) return;
        generalFooter.Visibility = page == "general" ? Visibility.Visible : Visibility.Collapsed;
        generalFooter.Background = Brush(dark ? "#181818" : "#FFFFFF");
        if (page != "general") return;
        var quit = ActionButton("settings.quit", "quit");
        AutomationProperties.SetName(quit, T("settings.quit"));
        quit.HorizontalAlignment = HorizontalAlignment.Right;
        // halo/icons.py RED: the same color as a discharged device indicator.
        quit.Resources["ButtonBackgroundPointerOver"] = Brush("#E81123");
        quit.Resources["ButtonBackgroundPressed"] = Brush("#E81123");
        quit.Resources["ButtonForegroundPointerOver"] = Brush("#FFFFFF");
        quit.Resources["ButtonForegroundPressed"] = Brush("#FFFFFF");
        generalFooterContent.Child = quit;
        PositionGeneralFooter();
    }

    private void PositionGeneralFooter()
    {
        if (generalFooter is null || generalFooterContent is null) return;
        var viewport = generalFooter.ActualWidth;
        if (viewport <= 0) return;
        var width = PageWidth(viewport);
        var inset = PageInset(width);
        generalFooterContent.Width = width;
        generalFooterContent.Margin = new(PageLeft(viewport), 0, 0, 0);
        generalFooterContent.Padding = new(inset, 12, inset, 16);
    }
}
