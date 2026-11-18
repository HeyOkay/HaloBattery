using System.Text.Json;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyWhatsNewAction(string destination, List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("about", false);
        await Task.Delay(100);
        root.UpdateLayout();
        var content = pageHosts.Single().content;
        var version = cards.First();
        var whatsNew = cards.Single(c => VerificationDescendants(c).OfType<TextBlock>().Any(t => t.Text == T("whatsnew.title")));
        Check("What's new card is directly below the version", content.Children.IndexOf(whatsNew) == content.Children.IndexOf(version) + 1);
        var open = VerificationDescendants(whatsNew).OfType<Button>().Single(b => b.Content?.ToString() == T("settings.open"));
        var before = Bridge.FixtureCommands.Count;
        ((IInvokeProvider)new ButtonAutomationPeer(open).GetPattern(PatternInterface.Invoke)).Invoke();
        await Task.Delay(20);
        Check("What's new click sends exactly one bridge command", Bridge.FixtureCommands.Count == before + 1);
        using var message = JsonDocument.Parse(Bridge.FixtureCommands.Last());
        Check("What's new delegates to the existing Python window", message.RootElement.GetProperty("name").GetString() == "showWhatsNew" &&
            !message.RootElement.GetProperty("args").EnumerateObject().Any());
        await VerificationCapture(destination, "about-whats-new");
    }
}
