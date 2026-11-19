using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.Graphics;

namespace HaloBattery.Settings;

/// <summary>
/// A small native window that hosts the bundled Windows 11 tray replica.
/// The HTML is local; WebView2 only supplies the browser engine already
/// present on Windows 11. If the runtime is unavailable the caller falls
/// back to the user's default browser.
/// </summary>
internal sealed class TaskbarPreviewWindow : Window
{
    private readonly WebView2 web = new();

    private TaskbarPreviewWindow()
    {
        Title = "Windows 11 tray preview";
        Content = web;
        AppWindow.Resize(new SizeInt32(1000, 620));
    }

    internal static async Task<TaskbarPreviewWindow> CreateAsync(Uri source, string assetDirectory)
    {
        var window = new TaskbarPreviewWindow();
        await window.web.EnsureCoreWebView2Async();
        window.web.CoreWebView2.SetVirtualHostNameToFolderMapping(
            "halo.tray", assetDirectory, CoreWebView2HostResourceAccessKind.Allow);
        window.web.Source = source;
        return window;
    }
}
