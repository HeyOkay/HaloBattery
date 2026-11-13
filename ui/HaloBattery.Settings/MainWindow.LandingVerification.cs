using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Hosting;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyPageLandings(string destination, string phase, List<object> checks)
    {
        var samples = new List<object>();
        var allStable = true;
        async Task Sample(string name, bool capture = false)
        {
            for (var i = 0; PageEntryBlocksWheel && i < 75; i++) await Task.Delay(20);
            await Task.Delay(40);
            root.UpdateLayout();
            var host = pageHosts.Single();
            var heading = host.content.Children.OfType<TextBlock>().First();
            var headingY = heading.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).Y;
            var frameY = frame.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).Y;
            var hostY = overscrollHost.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).Y;
            var focused = Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(root.XamlRoot) as DependencyObject;
            var translation = OverscrollPageElement.Translation;
            var entryY = (frame.Content as Page)?.RenderTransform is TranslateTransform transform ? transform.Y : 0;
            var stable = !PageEntryBlocksWheel && Math.Abs(headingY - hostY - 36) <= 1 / Scale &&
                Math.Abs(frameY - hostY) <= 1 / Scale &&
                Math.Abs(host.scroll.VerticalOffset) <= 1 / Scale && Math.Abs(entryY) < .001;
            allStable &= stable;
            samples.Add(new { name, current, stable, headingY, frameY, hostY, offset = host.scroll.VerticalOffset,
                entryY, translation = new { translation.X, translation.Y, translation.Z },
                focused = focused is null ? null : AutomationProperties.GetName(focused),
                focusType = focused?.GetType().Name, frameTransitions = frame.ContentTransitions?.Count });
            if (capture && Arg("--capture-compositor") == "true")
                await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, phase + "-" + name + ".composited.png"));
        }
        Navigate("general", false);
        await Sample("baseline-general", true);
        foreach (var page in new[] { "devices", "icons", "notifications", "advanced", "about", "general" })
        {
            var button = nav.Children.OfType<Button>().ElementAt(navigationPageIds.IndexOf(page));
            button.Focus(FocusState.Pointer);
            ((IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke)).Invoke();
            await Sample("clicked-" + page, true);
        }
        foreach (var page in new[] { "devices", "icons", "notifications", "advanced" })
        {
            Navigate(page, true);
            await Task.Delay(35);
        }
        await Sample("interrupted-advanced");
        Navigate("general", false);
        HandleContentOverscroll(-300, 0, 0);
        await Task.Delay(65);
        Navigate("devices", true);
        await Sample("interrupted-downward-pull", true);
        Navigate("devices", false);
        HandleContentOverscroll(300, 0, 1000);
        await Task.Delay(65);
        Navigate("icons", true);
        await Sample("interrupted-upward-pull", true);
        Navigate("general", true);
        await Sample("returned-general", true);
        await File.WriteAllTextAsync(Path.Combine(destination, phase + "-landings.json"),
            JsonSerializer.Serialize(samples, new JsonSerializerOptions { WriteIndented = true }));
        checks.Add(new { name = phase + ": repeated tab switches preserve heading and page origin", passed = allStable, actual = samples });
        if (!allStable) throw new InvalidOperationException("Page landing coordinates drifted: " + phase);
    }
}
