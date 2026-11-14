using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Shapes;
using Path = Microsoft.UI.Xaml.Shapes.Path;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyRequestedSettingGlyphs(string destination, List<object> checks)
    {
        var mappings = new (string Page, string Label, string Glyph)[]
        {
            ("general", "preferences.language", "languages"),
            ("general", "preferences.autostart", "circle-arrow-up"),
            ("general", "preferences.updates", "refresh-cw"),
            ("general", "preferences.check_now", "download"),
            ("devices", "settings.select_device", "smartphone-nfc"),
            ("devices", "preferences.bluetooth", "bluetooth"),
            ("icons", "settings.preview", "eye"),
            ("icons", "settings.content", "circle"),
            ("icons", "preferences.animation", "loader-circle"),
            ("icons", "settings.ring_thickness", "circle"),
            ("icons", "preferences.theme", "sun-moon"),
            ("notifications", "preferences.full", "battery-full"),
            ("notifications", "preferences.quiet", "bell-off"),
            ("about", "HaloBattery", "loader-circle")
        };
        foreach (var page in mappings.Select(item => item.Page).Distinct())
        {
            Navigate(page, false);
            await Task.Delay(100);
            root.UpdateLayout();
            foreach (var (_, label, glyph) in mappings.Where(item => item.Page == page))
            {
                var title = label == "HaloBattery" ? label : T(label);
                var setting = cards.Single(item => item.TitleElement is TextBlock text && text.Text == title);
                var icon = (setting.EntryFadeTargets.First() as ContentControl)?.Content as Viewbox;
                var actual = (icon?.Child as Canvas)?.Children.OfType<Path>().ToArray() ?? Array.Empty<Path>();
                var expected = ((Canvas)((Viewbox)LucideIcon(glyph)).Child).Children.OfType<Path>().ToArray();
                var passed = LucidePaths.ContainsKey(glyph) && icon is { Width: 20, Height: 20 } &&
                    actual.Length == expected.Length && actual.All(path => path.StrokeThickness == 1.5) &&
                    actual.Select(path => path.Data.ToString()).SequenceEqual(expected.Select(path => path.Data.ToString()));
                checks.Add(new { name = "requested settings glyph " + page + "/" + label, passed,
                    expectedGlyph = glyph, actualPathCount = actual.Length, expectedPathCount = expected.Length });
                if (!passed) throw new InvalidOperationException("Incorrect settings glyph: " + label);
            }
            await VerificationCapture(destination, "glyphs-" + page);
            if (Arg("--capture-compositor") == "true")
                await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(destination, "glyphs-" + page + ".composited.png"));
        }
        await VerifyDeviceKindHeader(destination, checks);
        await VerifyDeviceVisibilityGlyph(destination, checks);
        Navigate("general", false);
    }

    private async Task VerifyDeviceKindHeader(string destination, List<object> checks)
    {
        var original = System.Text.Json.JsonSerializer.Deserialize<UiState>(
            System.Text.Json.JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
        Navigate("devices", false);
        await Task.Delay(100);
        var deviceKey = selectedDeviceKey;
        var header = cards.Single(item => item.TitleElement is TextBlock text && text.Text == SelectedDevice!.Name);
        var page = frame.Content;
        Viewbox? Icon() => (header.EntryFadeTargets.First() as ContentControl)?.Content as Viewbox;
        var previousIcon = Icon();
        var previousGlyph = DeviceKindGlyph(SelectedDevice!.Kind);
        var cases = new (string? Kind, string Glyph)[]
        {
            ("mouse", "mouse"), ("keyboard", "keyboard"), ("headset", "headphones"),
            ("microphone", "mic"), ("speaker", "speaker"), ("webcam", "webcam"),
            ("health", "heart-pulse"), ("smart_home", "house"), ("watch", "watch"),
            ("display", "tv-minimal"), ("tracker", "radar"), ("gamepad", "gamepad-2"),
            ("dualshock", "gamepad-2"), ("dualsense", "gamepad-2"),
            ("bluetooth", "bluetooth"), ("unknown", "bluetooth"), ("", "bluetooth"), (null, "bluetooth")
        };
        foreach (var (kind, glyph) in cases)
        {
            var state = System.Text.Json.JsonSerializer.Deserialize<UiState>(
                System.Text.Json.JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
            var device = state.Devices.Single(item => item.Key == deviceKey);
            device.Kind = kind!;
            device.Pictogram = kind == "speaker" ? "mouse" : "speaker";
            device.CustomIconId = "fixture-custom-asset";
            device.CustomIconName = "noto:smiling-face";
            device.Brand = "xbox";
            device.BrandColor = true;
            state.Settings["filled_icons"] = System.Text.Json.JsonSerializer.SerializeToElement(true);
            device.Name = T("icon." + (kind is "dualshock" or "dualsense" ? "gamepad" :
                kind is null or "" or "unknown" ? "bluetooth" : kind));
            state.Revision++;
            ApplyState(state);
            root.UpdateLayout();
            var icon = Icon();
            var actual = (icon?.Child as Canvas)?.Children.OfType<Path>().ToArray() ?? Array.Empty<Path>();
            var expected = ((Canvas)((Viewbox)LucideIcon(glyph)).Child).Children.OfType<Path>().ToArray();
            var passed = ReferenceEquals(page, frame.Content) && cards.Contains(header) &&
                LucidePaths.ContainsKey(glyph) && icon is { Width: 20, Height: 20 } &&
                actual.Length == expected.Length && actual.All(path => path.Fill is null && path.StrokeThickness == 1.5) &&
                actual.Select(path => path.Data.ToString()).SequenceEqual(expected.Select(path => path.Data.ToString())) &&
                !VerificationDescendants(icon).OfType<Image>().Any() &&
                (glyph == previousGlyph ? ReferenceEquals(icon, previousIcon) : !ReferenceEquals(icon, previousIcon));
            checks.Add(new { name = "device header uses basic semantic outline " + (kind ?? "null"), passed,
                expectedGlyph = glyph, ignoresCustomPictogram = device.Pictogram, ignoresCustomIcon = device.CustomIconName,
                ignoresFilledIcons = true, ignoresControllerBrand = device.Brand });
            if (!passed) throw new InvalidOperationException("Incorrect basic device-kind header: " + kind);
            previousIcon = icon;
            previousGlyph = glyph;
            if (kind is "mouse" or "headset" or "gamepad")
            {
                await Task.Delay(100);
                await VerificationCapture(destination, "device-kind-" + kind);
                if (Arg("--capture-compositor") == "true")
                    await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(destination, "device-kind-" + kind + ".composited.png"));
            }
        }
        var oldIcon = Icon();
        var poll = System.Text.Json.JsonSerializer.Deserialize<UiState>(
            System.Text.Json.JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
        poll.Devices.Single(item => item.Key == deviceKey).Level = 59;
        poll.Revision++;
        ApplyState(poll);
        var retained = ReferenceEquals(oldIcon, Icon());
        checks.Add(new { name = "device header keeps its glyph during battery-only polls", passed = retained });
        if (!retained) throw new InvalidOperationException("Battery poll replaced the device-kind glyph");
        original.Revision = State.Revision + 1;
        ApplyState(original);
    }
    private async Task VerifyDeviceVisibilityGlyph(string destination, List<object> checks)
    {
        UiState Clone(UiState value) => System.Text.Json.JsonSerializer.Deserialize<UiState>(
            System.Text.Json.JsonSerializer.Serialize(value, SettingsBridge.Json), SettingsBridge.Json)!;
        var original = Clone(State);
        if (original.Devices.Count == 0) return;
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        CardView VisibilityCard() => cards.Single(card => card.TitleElement is TextBlock title && title.Text == T("settings.show_device"));
        Viewbox? IconOf(CardView card) => (card.EntryFadeTargets.First() as ContentControl)?.Content as Viewbox;
        bool Glyph(CardView card, string name)
        {
            var icon = IconOf(card);
            var paths = (icon?.Child as Canvas)?.Children.OfType<Path>().ToArray() ?? Array.Empty<Path>();
            var expected = ((Canvas)((Viewbox)LucideIcon(name)).Child).Children.OfType<Path>().ToArray();
            return icon is { Width: 20, Height: 20 } && paths.Length == expected.Length &&
                paths.All(path => path.StrokeThickness == 1.5 && path.Fill is null) &&
                paths.Select(path => path.Data.ToString()).SequenceEqual(expected.Select(path => path.Data.ToString()));
        }
        void Authoritative(bool hidden, string? key = null)
        {
            var state = Clone(State);
            state.SelectedDeviceKey = key ?? selectedDeviceKey;
            state.Devices.Single(device => device.Key == state.SelectedDeviceKey).Hidden = hidden;
            state.Revision++;
            ApplyState(state);
        }
        try
        {
            Authoritative(true);
            Navigate("devices", false);
            await Task.Delay(100);
            var card = VisibilityCard();
            var page = frame.Content;
            var toggle = VerificationDescendants(card.ActionElement!).OfType<SettingToggle>().Single();
            var deviceKey = selectedDeviceKey;
            var compatibility = cards.Single(item => item.TitleElement is TextBlock title && title.Text == T("preferences.keep_disconnected"));
            var compatibilityIcon = IconOf(compatibility);
            Check("hidden device initially renders the outlined eye-closed glyph", Glyph(card, "eye-closed") && toggle.IsChecked == false);
            card.StartBringIntoView(new BringIntoViewOptions { VerticalAlignmentRatio = .25 });
            await Task.Delay(100);
            await VerificationCapture(destination, "visibility-eye-closed");
            if (Arg("--capture-compositor") == "true")
                await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(destination, "visibility-eye-closed.composited.png"));

            async Task Toggle(bool show)
            {
                var peer = new Microsoft.UI.Xaml.Automation.Peers.ToggleButtonAutomationPeer(toggle);
                ((Microsoft.UI.Xaml.Automation.Provider.IToggleProvider)peer.GetPattern(
                    Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Toggle)).Toggle();
                for (var attempt = 0; toggle.IsChecked != show && attempt < 30; attempt++) await Task.Delay(10);
                using var command = System.Text.Json.JsonDocument.Parse(Bridge.FixtureCommands.Last());
                var args = command.RootElement.GetProperty("args");
                Check("visibility toggle dispatches original device key and hidden state " + show,
                    args.GetProperty("deviceKey").GetString() == deviceKey && args.GetProperty("field").GetString() == "hidden" &&
                    args.GetProperty("value").GetBoolean() == !show);
                Check("visibility glyph changes immediately without rebuilding page " + show,
                    ReferenceEquals(page, frame.Content) && ReferenceEquals(card, VisibilityCard()) && Glyph(card, show ? "eye" : "eye-closed"));
            }
            await Toggle(true);
            Check("optimistic eye glyph does not mutate authoritative hidden state", SelectedDevice!.Hidden);
            var shownIcon = IconOf(card);
            Authoritative(false);
            Check("authoritative visible state retains the current eye and page", !SelectedDevice!.Hidden && Glyph(card, "eye") &&
                ReferenceEquals(shownIcon, IconOf(card)) && ReferenceEquals(page, frame.Content));
            await VerificationCapture(destination, "visibility-eye");
            if (Arg("--capture-compositor") == "true")
                await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(destination, "visibility-eye.composited.png"));
            await Toggle(false);
            Authoritative(false); // Rejected/overridden toggle restores backend truth.
            Check("authoritative visibility reconciles an optimistic hidden glyph", Glyph(card, "eye") && toggle.IsChecked == true && ReferenceEquals(page, frame.Content));
            Authoritative(true);
            Check("authoritative hidden state updates only this card glyph", Glyph(card, "eye-closed") &&
                ReferenceEquals(compatibilityIcon, IconOf(compatibility)) && Glyph(compatibility, "eye") && ReferenceEquals(page, frame.Content));
            var hiddenIcon = IconOf(card);
            Authoritative(true);
            Check("unchanged hidden polls preserve the native eye-closed instance", ReferenceEquals(hiddenIcon, IconOf(card)));
            var other = State.Devices.FirstOrDefault(device => device.Key != deviceKey);
            if (other is not null)
            {
                Authoritative(true, other.Key);
                Check("switching to a hidden device initializes eye-closed", selectedDeviceKey == other.Key && Glyph(VisibilityCard(), "eye-closed"));
                Authoritative(false, deviceKey);
                Check("switching to a visible device initializes eye", selectedDeviceKey == deviceKey && Glyph(VisibilityCard(), "eye"));
            }
            Authoritative(true);
            Navigate("icons", false);
            var preview = cards.Single(item => item.TitleElement is TextBlock title && title.Text == T("settings.preview"));
            Check("hidden device leaves the separate tray-preview eye glyph unchanged", Glyph(preview, "eye"));
        }
        finally { original.Revision = State.Revision + 1; ApplyState(original); }
    }

}
