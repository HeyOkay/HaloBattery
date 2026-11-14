using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Windows.Graphics.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private sealed record ToggleShadowPixels(int Width, int Height, byte[] Pixels);
    private sealed record ToggleShadowDifference(int Darkened, int Brightened, int Left, int Top, int Width, int Height);

    private async Task VerifyToggleThumbShadow(string destination, List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("general", false);
        await Task.Delay(150);
        var toggle = VerificationDescendants(frame).OfType<SettingToggle>().First();
        Border Host() => VerificationDescendants(toggle).OfType<Border>().Single(item => item.Name == "ThumbShadowHost");
        SpriteVisual Sprite() => (SpriteVisual)ElementCompositionPreview.GetElementChildVisual(Host());
        var sprite = Sprite();
        var shadow = sprite.Shadow as DropShadow;
        Check("toggle shadow belongs only to the thumb", shadow is { Mask: not null } &&
            shadow.Opacity is >= .15f and <= .25f && shadow.BlurRadius == 4 &&
            shadow.Offset.Y == 1.5f && sprite.Size.X == 16 && sprite.Size.Y == 16 &&
            ElementCompositionPreview.GetElementChildVisual(toggle) is null);
        Check("toggle thumb shadow adds no animation dependency", sprite.TryGetAnimationController("Offset") is null &&
            shadow!.TryGetAnimationController("BlurRadius") is null, new { systemAnimationsEnabled = AnimationsEnabled });

        async Task<ToggleShadowPixels> Capture(string name)
        {
            var path = Path.Combine(destination, name + ".composited.png");
            await Task.Delay(150);
            await FixtureWindowCapture.CaptureAsync(hwnd, path);
            using var file = File.OpenRead(path);
            using var stream = file.AsRandomAccessStream();
            var decoder = await BitmapDecoder.CreateAsync(stream);
            var data = await decoder.GetPixelDataAsync(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied,
                new BitmapTransform(), ExifOrientationMode.IgnoreExifOrientation, ColorManagementMode.DoNotColorManage);
            return new((int)decoder.PixelWidth, (int)decoder.PixelHeight, data.DetachPixelData());
        }
        ToggleShadowDifference Difference(ToggleShadowPixels before, ToggleShadowPixels after)
        {
            if (before.Width != after.Width || before.Height != after.Height)
                throw new InvalidOperationException("Toggle capture size changed");
            var origin = toggle.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point());
            // WGC includes the native window frame. A generous local inset
            // includes that frame offset while excluding text and other cards.
            var left = Math.Max(0, (int)Math.Floor(origin.X * Scale) - 16);
            var top = Math.Max(0, (int)Math.Floor(origin.Y * Scale) - 16);
            var right = Math.Min(before.Width, (int)Math.Ceiling((origin.X + 40) * Scale) + 16);
            var bottom = Math.Min(before.Height, (int)Math.Ceiling((origin.Y + 22) * Scale) + 16);
            int darkened = 0, brightened = 0, minX = right, minY = bottom, maxX = left, maxY = top;
            for (var y = top; y < bottom; y++)
                for (var x = left; x < right; x++)
                {
                    var at = (y * before.Width + x) * 4;
                    var delta = before.Pixels[at] + before.Pixels[at + 1] + before.Pixels[at + 2] -
                        after.Pixels[at] - after.Pixels[at + 1] - after.Pixels[at + 2];
                    if (delta >= 3)
                    {
                        darkened++; minX = Math.Min(minX, x); minY = Math.Min(minY, y);
                        maxX = Math.Max(maxX, x); maxY = Math.Max(maxY, y);
                    }
                    else if (delta <= -3) brightened++;
                }
            return new(darkened, brightened, minX, minY,
                darkened == 0 ? 0 : maxX - minX + 1, darkened == 0 ? 0 : maxY - minY + 1);
        }
        var original = toggle.IsChecked;
        if (Arg("--capture-compositor") == "true")
        {
            var differences = new List<ToggleShadowDifference>();
            foreach (var state in new[] { false, true })
            {
                toggle.IsChecked = state;
                root.UpdateLayout();
                Sprite().IsVisible = false;
                var before = await Capture("toggle-" + (state ? "on" : "off") + "-without-shadow");
                Sprite().IsVisible = true;
                var after = await Capture("toggle-" + (state ? "on" : "off") + "-with-shadow");
                var difference = Difference(before, after);
                differences.Add(difference);
                Check("compositor renders localized toggle thumb shadow " + state, difference.Darkened >= 3 &&
                    difference.Brightened <= 3 && difference.Width <= 28 * Scale + 2 &&
                    difference.Height <= 28 * Scale + 2, difference);
            }
            var movement = differences[1].Left + differences[1].Width / 2.0 -
                differences[0].Left - differences[0].Width / 2.0;
            Check("compositor shadow follows the thumb between switch states",
                Math.Abs(movement - 18 * Scale) <= 2, new { actual = movement, expected = 18 * Scale });
        }
        else checks.Add(new { name = "toggle compositor pixel capture is opt-in", diagnostic = true,
            note = "Run --capture-compositor true for real shadow pixels; XAML RenderTargetBitmap omits compositor shadows." });

        toggle.IsChecked = original;
        var oldHost = Host();
        var oldSprite = Sprite();
        var row = (Panel)Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(toggle);
        var index = row.Children.IndexOf(toggle);
        row.Children.RemoveAt(index);
        await Task.Delay(100);
        Check("unloading toggle detaches compositor child", !toggle.IsLoaded &&
            ElementCompositionPreview.GetElementChildVisual(oldHost) is null);
        row.Children.Insert(index, toggle);
        await Task.Delay(100);
        Check("reloading toggle restores exactly one thumb shadow", toggle.IsLoaded &&
            !ReferenceEquals(Sprite(), oldSprite) && Sprite().Shadow is DropShadow);
        oldHost = Host();
        var template = toggle.Template;
        toggle.Template = null;
        toggle.Template = template;
        toggle.ApplyTemplate();
        await Task.Delay(100);
        Check("retemplating toggle releases the old thumb shadow",
            ElementCompositionPreview.GetElementChildVisual(oldHost) is null && Sprite().Shadow is DropShadow);

        var beforeToggle = toggle.IsChecked;
        ((IToggleProvider)new ToggleButtonAutomationPeer(toggle).GetPattern(PatternInterface.Toggle)).Toggle();
        Check("shadow preserves native toggle automation", toggle.IsChecked != beforeToggle);
        toggle.IsChecked = original;
        toggle.IsEnabled = false;
        await Task.Delay(50);
        Check("disabled toggle keeps its existing opacity", Math.Abs(toggle.Opacity - .45) < .00001 &&
            Sprite().Shadow is DropShadow, new { opacity = toggle.Opacity });
        toggle.IsEnabled = true;
        await Task.Delay(50);
        Check("enabled toggle restores opacity", toggle.Opacity == 1, new { opacity = toggle.Opacity });
    }

    private async Task VerifyRingThicknessChoice(List<object> checks)
    {
        Navigate("icons", false);
        await Task.Delay(100);
        var combo = VerificationDescendants(frame).OfType<ComboBox>()
            .Single(item => AutomationProperties.GetName(item) == T("settings.ring_thickness"));
        var choices = combo.Items.Cast<Choice>().ToArray();
        var valid = choices.Length == 2 && choices[0].Value.GetString() == "thin" &&
            choices[0].Label == T("settings.ring_thin") && choices[1].Value.GetString() == "classic" &&
            choices[1].Label == T("settings.ring_classic") && !choices.Any(item => item.Label.StartsWith("settings."));
        checks.Add(new { name = "ring thickness offers both localized native choices", passed = valid });
        if (!valid) throw new InvalidOperationException("Invalid ring thickness choices");
        foreach (var value in new[] { "classic", "thin" })
        {
            var before = Bridge.FixtureCommands.Count;
            combo.SelectedItem = choices.Single(item => item.Value.GetString() == value);
            await Task.Delay(50);
            using var wire = JsonDocument.Parse(Bridge.FixtureCommands.Last());
            var command = wire.RootElement;
            var passed = Bridge.FixtureCommands.Count == before + 1 && command.GetProperty("name").GetString() == "setGlobal" &&
                command.GetProperty("args").GetProperty("key").GetString() == "ring_style" &&
                command.GetProperty("args").GetProperty("value").GetString() == value;
            checks.Add(new { name = "ring thickness dispatches authoritative setting " + value, passed });
            if (!passed) throw new InvalidOperationException("Invalid ring thickness command");
            var state = JsonSerializer.Deserialize<UiState>(JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
            state.Settings["ring_style"] = JsonSerializer.SerializeToElement(value);
            state.Revision++;
            ApplyState(state);
            passed = ReferenceEquals(combo, VerificationDescendants(frame).OfType<ComboBox>()
                .Single(item => AutomationProperties.GetName(item) == T("settings.ring_thickness"))) &&
                combo.SelectedItem is Choice selected && selected.Value.GetString() == value;
            checks.Add(new { name = "ring thickness state refresh preserves selection " + value, passed });
            if (!passed) throw new InvalidOperationException("Ring thickness state did not refresh");
        }
        Navigate("general", false);
    }
}
