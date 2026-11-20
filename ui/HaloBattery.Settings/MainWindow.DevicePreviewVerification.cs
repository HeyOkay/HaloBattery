using System.Text.Json;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyDevicePreview(string destination, List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        using var data = JsonDocument.Parse(File.ReadAllText(Path.Combine(Path.GetDirectoryName(Arg("--fixture-state"))!, "preview-frames.json")));
        async Task<(Microsoft.UI.Xaml.Media.Imaging.BitmapImage Image, Microsoft.UI.Xaml.Media.Imaging.BitmapImage Background, DevicePreview.Ring Ring)> Frame(string key)
        {
            var frame = data.RootElement.GetProperty(key);
            var ring = frame.GetProperty("transition");
            return (await DecodePreviewAsync(frame.GetProperty("png").GetString()!),
                await DecodePreviewAsync(ring.GetProperty("background").GetString()!), DevicePreview.Ring.Read(ring));
        }
        var a = await Frame("a"); var b = await Frame("b"); var c = await Frame("c");
        Navigate("devices", false);
        root.UpdateLayout();
        var presenter = previewPresenter!;
        presenter.Apply("a", a.Image, a.Background, a.Ring, true);
        Check("first preview shows canonical pixels without animation", !presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, a.Image));
        Check("preview retains exactly 80 physical pixels", Math.Abs(presenter.Width * Scale - 80) < .001);
        presenter.Apply("b", b.Image, b.Background, b.Ring, true);
        Check("device switch uses the new artwork with an independent arc clock", presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, b.Background) && presenter.Arc.TryGetAnimationController("TrimEnd") is not null);
        if (Arg("--verify-capture") == "preview")
        {
            presenter.Arc.TryGetAnimationController("TrimEnd")!.Pause();
            presenter.Arc.TryGetAnimationController("TrimEnd")!.Progress = 1;
            presenter.Ink.TryGetAnimationController("Color")!.Pause();
            presenter.Ink.TryGetAnimationController("Color")!.Progress = 1;
            await Task.Delay(80);
            await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "animated-arc.png"));
            presenter.Settle();
            await Task.Delay(80);
            await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "canonical-arc.png"));
            return; // Pixel comparison is a separate fixture run with paused clocks.
        }
        foreach (var dpi in new[] { 1.0, 1.25, 1.5, 2.0 })
        {
            presenter.Resize(dpi);
            Check($"arc center and radius retain the track geometry at {dpi * 100}% DPI",
                Math.Abs(presenter.Arc.Center.X * dpi - 40) < .001 &&
                Math.Abs(presenter.Arc.Center.Y * dpi - 40) < .001 &&
                Math.Abs(presenter.Arc.Radius.X * dpi - b.Ring.Radius) < .001 &&
                Math.Abs(presenter.Arc.Radius.Y * dpi - b.Ring.Radius) < .001);
        }
        presenter.Resize(Scale);
        await Task.Delay(80);
        var clock = presenter.Arc.TryGetAnimationController("TrimEnd");
        clock!.Pause(); clock.Progress = .35f;
        Navigate("devices", false);
        Check("device-page rebuild reuses the running native presenter", ReferenceEquals(previewPresenter, presenter) && presenter.IsAnimating);
        presenter.Apply("c", c.Image, c.Background, c.Ring, true);
        Check("rapid switch replaces the active clock and artwork", presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, c.Background) && !ReferenceEquals(clock, presenter.Arc.TryGetAnimationController("TrimEnd")));
        presenter.Apply("a", a.Image, a.Background, a.Ring, true);
        presenter.Apply("a", c.Image, null, null, true);
        Check("ordinary charging-frame replies cannot interrupt selection interpolation", presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, a.Background));
        await Task.Delay(600);
        Check("completion restores the newest exact Python frame", !presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, c.Image));
        presenter.Apply("b", b.Image, b.Background, b.Ring, false);
        Check("disabled system animations settle immediately", !presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, b.Image));
        presenter.Apply("c", c.Image, c.Background, c.Ring, true);
        presenter.Apply("unknown", a.Image, a.Background, a.Ring with { Eligible = false }, true);
        Check("unknown-level state cancels the percentage transition", !presenter.IsAnimating && ReferenceEquals(presenter.Image.Source, a.Image));
        presenter.Apply("a", a.Image, a.Background, a.Ring, true);
        presenter.Apply("b", b.Image, b.Background, b.Ring, true);
        Navigate("general", false);
        Check("page departure disposes the transition presenter", previewPresenter is null);
        // A response from an obsolete page generation must not paint A again.
        Navigate("devices", false); root.UpdateLayout();
        previewRequestGeneration = previewGeneration - 1;
        ApplyPreview(previewDeviceKey, data.RootElement.GetProperty("a").GetProperty("png").GetString()!);
        await Task.Delay(40);
        Check("late response from an old generation is discarded before decoding", previewImage!.Source is null);
    }
    private async Task VerifyDevicePreviewSelection(string destination, List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        using var frames = JsonDocument.Parse(File.ReadAllText(Path.Combine(
            Path.GetDirectoryName(Arg("--fixture-state"))!, "preview-frames.json")));
        Navigate("devices", false);
        DevicePreview? retained = null;
        var first = true;
        foreach (var key in new[] { "a", "b", "c", "b" })
        {
            var next = JsonSerializer.Deserialize(JsonSerializer.Serialize(State, SettingsJson.Context.UiState), SettingsJson.Context.UiState)!;
            next.SelectedDeviceKey = key; next.Revision++;
            ApplyState(next);
            var packet = frames.RootElement.GetProperty(key);
            previewRequestGeneration = previewGeneration;
            previewPending = true;
            ApplyPreview(key, packet.GetProperty("png").GetString()!, packet.GetProperty("transition").Clone());
            for (var attempt = 0; previewPending && attempt < 200; attempt++) await Task.Delay(5);
            Check($"selected {key}: preview reply completed for the current page", !previewPending && previewDeviceKey == key);
            var presenter = previewPresenter!;
            Check($"selected {key}: page rebuild retains the same preview presenter", retained is null || ReferenceEquals(retained, presenter));
            Check($"selected {key}: complete state-to-PNG path uses selection animation", presenter.IsAnimating == !first);
            retained = presenter;
            if (key == "b" && Arg("--verify-capture") == "preview")
            {
                var clock = presenter.Arc.TryGetAnimationController("TrimEnd")!;
                clock.Pause(); clock.Progress = .1f;
                await Task.Delay(80);
                root.UpdateLayout();
                var corner = presenter.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point());
                var bounds = new { x = corner.X * Scale, y = corner.Y * Scale,
                    width = presenter.ActualWidth * Scale, height = presenter.ActualHeight * Scale,
                    rootWidth = root.ActualWidth * Scale, rootHeight = root.ActualHeight * Scale };
                File.WriteAllText(Path.Combine(destination, "preview-bounds.json"), JsonSerializer.Serialize(bounds));
                await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "intermediate-actual.png"));
                presenter.Settle();
                presenter.Image.Source = await DecodePreviewAsync(frames.RootElement.GetProperty("intermediate").GetProperty("png").GetString()!);
                await Task.Delay(80);
                await FixtureWindowCapture.CaptureAsync(hwnd, Path.Combine(destination, "intermediate-expected.png"));
                return;
            }
            if (!first)
            {
                await Task.Delay(80);
                Check($"selected {key}: animation stays active between rendered frames", presenter.IsAnimating);
                await Task.Delay(500);
                Check($"selected {key}: animation finishes at the canonical preview", !presenter.IsAnimating);
            }
            first = false;
        }
    }

}
