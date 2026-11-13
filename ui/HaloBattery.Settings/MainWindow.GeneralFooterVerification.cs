using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyGeneralFooter(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("general", false);
        await Task.Delay(100);
        root.UpdateLayout();
        Check("updates are in General", VerificationDescendants(frame).OfType<TextBlock>().Any(t => t.Text == T("settings.updates")));
        var quit = generalFooterContent?.Child as Button;
        Check("Exit is fixed outside the scrolling page", generalFooter?.Visibility == Visibility.Visible && quit is not null && !VerificationDescendants(frame).Contains(quit));
        var y = generalFooter!.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).Y;
        var scroll = pageHosts.Single().scroll;
        var oldMaxHeight = scroll.MaxHeight;
        scroll.MaxHeight = 300;
        root.UpdateLayout();
        scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
        await Task.Delay(50);
        Check("Exit stays anchored during content scrolling", Math.Abs(generalFooter.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).Y - y) <= 1 / Scale);
        scroll.MaxHeight = oldMaxHeight;
        root.UpdateLayout();
        VisualStateManager.GoToState(quit!, "PointerOver", false);
        var backgrounds = VerificationDescendants(quit!).Select(node => node switch
        {
            Border b => b.Background as SolidColorBrush,
            Panel p => p.Background as SolidColorBrush,
            ContentPresenter c => c.Background as SolidColorBrush,
            _ => null
        }).Where(brush => brush is not null).Select(brush => brush!.Color.ToString()).ToArray();
        Check("Exit hover uses discharged device red", backgrounds.Contains("#FFE81123"), backgrounds);
        VisualStateManager.GoToState(quit!, "Normal", false);
        var before = Bridge.FixtureCommands.Count;
        var peer = new ButtonAutomationPeer(quit!);
        ((IInvokeProvider)peer.GetPattern(PatternInterface.Invoke)).Invoke();
        await Task.Delay(30);
        Check("fixed Exit invokes existing quit command", Bridge.FixtureCommands.Count == before + 1 &&
            JsonDocument.Parse(Bridge.FixtureCommands.Last()).RootElement.GetProperty("name").GetString() == "quit");
        Navigate("about", false);
        await Task.Delay(80);
        Check("About has no updates or Exit footer", generalFooter.Visibility == Visibility.Collapsed &&
            !VerificationDescendants(frame).OfType<TextBlock>().Any(t => t.Text == T("settings.updates")));
        Navigate("advanced", false);
        await Task.Delay(80);
        var warning = VerificationDescendants(frame).OfType<Border>().FirstOrDefault(b => b.Background is SolidColorBrush brush &&
            brush.Color.ToString() == (dark ? "#FF433519" : "#FFFFF4CE"));
        Check("experimental warning has no outline", warning is not null && warning.BorderThickness == new Thickness(0));
    }
}
