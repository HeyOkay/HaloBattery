using System.Text.Json;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    // Fixture-only checks: all catalog replies are injected, and Bridge records
    // outgoing envelopes in memory instead of touching a backend or the network.
    private async Task VerifyIconCatalog(List<object> checks)
    {
        if (State.Devices.Count == 0) return;
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("devices", false);
        await Task.Delay(50);
        var view = iconCatalog ?? throw new InvalidOperationException("Catalog was not built");
        var collapsedCommands = Bridge.FixtureCommands.Count;
        view.Search.Text = "not-open";
        await Task.Delay(350);
        Check("collapsed catalog never requests a saved or deferred query", view.Requests.Count == 0 &&
            Bridge.FixtureCommands.Count == collapsedCommands);
        view.Panel.IsExpanded = true;
        root.UpdateLayout();
        if (frame.Content is Page { Content: ScrollViewer initialScroll })
        {
            var top = view.Panel.TransformToVisual(initialScroll).TransformPoint(new Windows.Foundation.Point()).Y + initialScroll.VerticalOffset;
            initialScroll.ChangeView(null, Math.Max(0, top - 24), null, true);
            await Task.Delay(150);
        }
        view.Search.Text = "mouse";
        await Task.Delay(50); // Deliver WinUI TextChanged before cancelling its debounce.
        BeginCatalogSearch(view);
        var first = view.Requests.Single().Key;
        using (var envelope = JsonDocument.Parse(Bridge.FixtureCommands.Last()))
        {
            var wire = envelope.RootElement;
            Check("catalog requests physical DPI thumbnail size", wire.GetProperty("type").GetString() == "catalog" &&
                wire.GetProperty("size").GetInt32() == CatalogPixelSize && wire.GetProperty("deviceKey").GetString() == view.DeviceKey);
        }
        // A stale query may complete after the user has already typed again.
        view.Search.Text = "keyboard"; await Task.Delay(50); BeginCatalogSearch(view);
        var search = view.Requests.Single().Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = first, action = "search",
            deviceKey = view.DeviceKey, query = "mouse", items = new[] { new { id = "lucide:stale", name = "Stale" } } }));
        Check("catalog drops stale search response", view.Items.Count == 0);
        const string png16 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAJklEQVR4nGP8////fwYKABMlmkcNgAAmBgoB06gBDKNhwEB5GAAAa8oEHLq5H1oAAAAASUVORK5CYII=";
        const string png80 = "iVBORw0KGgoAAAANSUhEUgAAAFAAAABQCAYAAACOEfKtAAAAv0lEQVR4nO3SsREAIBDDMGD/nZ8dMKXUp/Flz8wsnp33KQJ+4IGRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgJGAkYCRgKu5e1sEnHGz/2gAAAAASUVORK5CYII=";
        object[] Items(int start) => Enumerable.Range(start, 50).Select(index => (object)new
        { id = "lucide:fixture-" + index, name = "Fixture " + index, collection = "Lucide", preview = png16 }).ToArray();
        // Approved names can still have unsupported SVGs: a wholly skipped
        // first page must retain a route to later, renderable results.
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = search, action = "search",
            deviceKey = view.DeviceKey, query = "keyboard", start = 0, nextStart = 50, hasMore = true,
            total = 999, contextKey = "fixture-context", skipped = 50, items = Array.Empty<object>() }));
        Check("empty skipped first page retains numbered navigation and next", view.Items.Count == 0 &&
            view.Pager.Visibility == Visibility.Visible && view.Pager.Children.OfType<Button>().Last().IsEnabled &&
            view.Pager.Children.OfType<Button>().Count(button => button.Content is string) > 1);
        var nextPeer = new Microsoft.UI.Xaml.Automation.Peers.ButtonAutomationPeer(view.Pager.Children.OfType<Button>().Last());
        ((Microsoft.UI.Xaml.Automation.Provider.IInvokeProvider)nextPeer.GetPattern(
            Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Invoke)).Invoke();
        for (var attempt = 0; view.Start != 50 && attempt < 30; attempt++) await Task.Delay(10);
        Check("next button leaves an entirely skipped first page", view.Start == 50 && view.Loading);
        NavigateCatalogPage(view, 0, force: true);
        search = view.Requests.Single().Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = search, action = "search",
            deviceKey = view.DeviceKey, query = "keyboard", start = 0, nextStart = 50, hasMore = true,
            total = 999, contextKey = "fixture-context", items = Items(0) }));
        await Task.Delay(100); root.UpdateLayout();
        Check("catalog shows one fifty-icon page", view.Items.Count == 50 && view.Total == 999 && view.HasMore);
        var realized = VerificationDescendants(view.Results).OfType<GridViewItem>().Count();
        Check("catalog result grid virtualizes a bounded viewport", view.Results.ActualHeight == 300 && realized > 0 && realized <= view.Items.Count && VerificationDescendants(view.Results).OfType<ItemsWrapGrid>().Any(), new { height = view.Results.ActualHeight, realized, items = view.Items.Count });
        Check("catalog template binds visible item names", VerificationDescendants(view.Results).OfType<TextBlock>().Any(text => text.Text.StartsWith("Fixture ")));
        Check("catalog template binds decoded ring images", VerificationDescendants(view.Results).OfType<Image>().Any(image => image.Source is BitmapImage));

        view.Search.Focus(FocusState.Programmatic);
        var previousText = view.Search.Text;
        var state = JsonSerializer.Deserialize<UiState>(JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
        state.Revision++;
        ApplyState(state);
        Check("poll state preserves catalog view, query, results and focus", ReferenceEquals(iconCatalog, view) &&
            view.Search.Text == previousText && view.Items.Count == 50 && view.Search.FocusState != FocusState.Unfocused);
        view.Results.SelectedItem = view.Items[0].Id;
        var details = view.Requests.Single(pair => pair.Value.Action == "details").Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = details, action = "details",
            deviceKey = view.DeviceKey, detail = new { id = view.SelectedId, collection = "Lucide", author = "Lucide contributors",
                license = "ISC", licenseText = "Fixture license text", notices = "Fixture notices", preview80 = png80, previewActual = png16, actualSize = 16 } }));
        for (var attempt = 0; !view.Apply.IsEnabled && attempt < 40; attempt++) await Task.Delay(25);
        Check("catalog details decode both physical ring previews", view.Preview80.Source is BitmapImage { PixelWidth: 80 } &&
            view.PreviewActual.Source is BitmapImage { PixelWidth: 16 } && view.Apply.IsEnabled);
        Check("catalog preserves author, license and legal notices", view.Metadata.Text.Contains("Lucide contributors") &&
            view.Metadata.Text.Contains("ISC") && view.Notices.Contains("Fixture license text") && view.Notices.Contains("Fixture notices"));

        root.UpdateLayout();
        var tiles = VerificationDescendants(view.Results).OfType<GridViewItem>()
            .Where(item => item.ContentTemplateRoot is Grid).Select(item =>
            {
                var panel = (Grid)item.ContentTemplateRoot;
                var preview = (Border)panel.Children[0];
                var name = (TextBlock)panel.Children[1];
                var collection = (TextBlock)panel.Children[2];
                var panelOrigin = panel.TransformToVisual(item).TransformPoint(new Windows.Foundation.Point());
                // Use row layout slots rather than font ink extents, and allow
                // one physical pixel for fractional system-DPI layout rounding.
                var imageSlot = Microsoft.UI.Xaml.Controls.Primitives.LayoutInformation.GetLayoutSlot(preview);
                var nameSlot = Microsoft.UI.Xaml.Controls.Primitives.LayoutInformation.GetLayoutSlot(name);
                var collectionSlot = Microsoft.UI.Xaml.Controls.Primitives.LayoutInformation.GetLayoutSlot(collection);
                return new { selected = item.IsSelected, width = item.ActualWidth, height = item.ActualHeight,
                    panelX = panelOrigin.X, panelY = panelOrigin.Y,
                    left = imageSlot.X, top = imageSlot.Y, right = panel.ActualWidth - imageSlot.Right,
                    bottom = panel.ActualHeight - collectionSlot.Bottom,
                    nameInset = nameSlot.X, collectionInset = collectionSlot.X,
                    nameGap = nameSlot.Y - imageSlot.Bottom,
                    collectionGap = collectionSlot.Y - nameSlot.Bottom };
            }).ToArray();
        bool Near(double value, double expected) => Math.Abs(value - expected) <= 1 / Scale + .01;
        await VerifyNeutralButtonStates(checks);
        await VerificationCapture(Arg("--verify-dir"), "icon-catalog-spacing");
        Check("catalog tile preview and labels have uniform balanced padding", tiles.Length > 1 && tiles.All(tile =>
            Near(tile.width, 136) && Near(tile.height, 112) &&
            Near(tile.left, 10) && Near(tile.top, 10) && Near(tile.right, 10) && Near(tile.bottom, 10) &&
            Near(tile.nameInset, 10) && Near(tile.collectionInset, 10) &&
            Near(tile.nameGap, 6) && Near(tile.collectionGap, 6)), tiles);
        var selectedTile = tiles.FirstOrDefault(tile => tile.selected);
        var otherTile = tiles.FirstOrDefault(tile => !tile.selected);
        Check("catalog selection preserves tile content bounds", selectedTile is not null && otherTile is not null &&
            Near(selectedTile.panelX, otherTile.panelX) && Near(selectedTile.panelY, otherTile.panelY) &&
            Near(selectedTile.width, otherTile.width) && Near(selectedTile.height, otherTile.height));

        var nativePresenter = VerificationDescendants(view.Results).OfType<GridViewItem>()
            .Where(item => item.IsSelected).SelectMany(VerificationDescendants)
            .OfType<Microsoft.UI.Xaml.Controls.Primitives.ListViewItemPresenter>().FirstOrDefault();
        Check("catalog retains native rounded selection outline", nativePresenter is not null &&
            nativePresenter.SelectedBorderBrush is Microsoft.UI.Xaml.Media.SolidColorBrush { Color.A: > 0 } &&
            nativePresenter.SelectedBorderThickness.Left > 0 && Near(nativePresenter.CornerRadius.TopLeft, 10));

        if (frame.Content is Page { Content: ScrollViewer scroll })
        {
            var top = view.Panel.TransformToVisual(scroll).TransformPoint(new Windows.Foundation.Point()).Y + scroll.VerticalOffset;
            scroll.ChangeView(null, Math.Max(0, top - 24), null, true);
            await Task.Delay(100);
        }
        await VerificationCapture(Arg("--verify-dir"), "icon-catalog");
        view.Results.SelectedItem = view.Items[1].Id;
        var staleDetail = view.Requests.Single(pair => pair.Value.Action == "details").Key;
        view.Results.SelectedItem = view.Items[2].Id;
        var latestDetail = view.Requests.Single(pair => pair.Value.Action == "details" && pair.Key != staleDetail).Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = latestDetail, action = "details",
            deviceKey = view.DeviceKey, detail = new { id = view.SelectedId, author = "Current attribution",
                preview80 = png80, previewActual = png16, actualSize = 16 } }));
        for (var attempt = 0; !view.Apply.IsEnabled && attempt < 40; attempt++) await Task.Delay(25);
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = staleDetail, action = "details",
            deviceKey = view.DeviceKey, detail = new { id = "lucide:fixture-1", author = "Stale attribution",
                preview80 = png80, previewActual = png16, actualSize = 16 } }));
        await Task.Delay(25);
        Check("catalog drops details from previous selection", view.Metadata.Text.Contains("Current attribution") && view.Apply.IsEnabled && !view.Loading);
        // The valid large preview yields to native decoding before the second,
        // invalid PNG fails. Change selection while that older reply is in flight.
        view.Results.SelectedItem = view.Items[3].Id;
        var failingDetail = view.Requests.Single(pair => pair.Value.Action == "details").Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = failingDetail, action = "details",
            deviceKey = view.DeviceKey, detail = new { id = view.SelectedId, preview80 = png80, previewActual = "", actualSize = 16 } }));
        Check("native detail decode yields before its later image failure", view.Status.Text == T("settings.iconify_loading"));
        view.Results.SelectedItem = view.Items[4].Id;
        var replacementDetail = view.Requests.Single(pair => pair.Value.Action == "details").Key;
        await Task.Delay(200);
        Check("obsolete detail decode failure cannot replace newer selection status", view.Status.Text == T("settings.iconify_loading") &&
            view.Retry.Visibility == Visibility.Collapsed && view.Metadata.Text == view.SelectedId);
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = replacementDetail, action = "details",
            deviceKey = view.DeviceKey, detail = new { id = view.SelectedId, author = "Current attribution",
                preview80 = png80, previewActual = png16, actualSize = 16 } }));
        for (var attempt = 0; !view.Apply.IsEnabled && attempt < 40; attempt++) await Task.Delay(25);
        var readyItems = view.Items.ToArray();
        var readyStatus = view.Status.Text;
        SendCatalog(view, "search", view.Start, background: true);
        var validation = view.Requests.Single(pair => pair.Value.Action == "search").Key;
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = validation, action = "search",
            deviceKey = view.DeviceKey, query = view.Query, start = view.Start, contextKey = view.ContextKey, items = new[] { 1 } }));
        Check("malformed background validation leaves the ready page and status intact", view.Items.SequenceEqual(readyItems) &&
            view.Status.Text == readyStatus && view.Retry.Visibility == Visibility.Collapsed && view.Apply.IsEnabled);
        await VerifyCatalogPagePerformance(view, checks);
        Navigate("general", false);
        Check("leaving devices cancels catalog work", !view.Active && view.Requests.Count == 0 && iconCatalog is null &&
            view.Pages.Count == 0 && view.ById.Count == 0 && view.ThumbnailNodes.Count == 0 && view.Items.Count == 0);
    }
    private async Task VerifyNeutralButtonStates(List<object> checks)
    {
        var neutral = VerificationDescendants(frame).OfType<Button>()
            .Where(button => button.Content is string text &&
                (text == T("settings.iconify_browse") || text == T("settings.reset"))).ToArray();
        var combo = VerificationDescendants(frame).OfType<ComboBox>().First();
        if (neutral.Length != 2) throw new InvalidOperationException("Expected browse and reset fixture buttons");
        async Task<uint> BackgroundPixel(FrameworkElement control)
        {
            var bitmap = new Microsoft.UI.Xaml.Media.Imaging.RenderTargetBitmap();
            await bitmap.RenderAsync(control, (int)Math.Round(control.ActualWidth * Scale),
                (int)Math.Round(control.ActualHeight * Scale));
            var pixels = (await bitmap.GetPixelsAsync()).ToArray();
            // Inside the flat top of the pill, above the text and away from
            // its curved edge; compare rendered pixels, including alpha.
            var y = Math.Clamp((int)Math.Round(3 * Scale), 0, bitmap.PixelHeight - 1);
            var at = (y * bitmap.PixelWidth + bitmap.PixelWidth / 2) * 4;
            return ((uint)pixels[at + 3] << 24) | ((uint)pixels[at + 2] << 16) |
                ((uint)pixels[at + 1] << 8) | pixels[at];
        }
        var sequence = new[] { "Normal", "PointerOver", "Pressed", "Normal" };
        for (var index = 0; index < sequence.Length; index++)
        {
            var state = sequence[index];
            var changed = VisualStateManager.GoToState(combo, state, false);
            foreach (var button in neutral) changed &= VisualStateManager.GoToState(button, state, false);
            // Leave the native 83ms brush transition in place and sample its
            // settled result, as in a real hover/press/leave sequence.
            await Task.Delay(150);
            var dropdown = await BackgroundPixel(combo);
            var samples = new List<object>();
            var passed = changed;
            foreach (var button in neutral)
            {
                var pixel = await BackgroundPixel(button);
                var foreground = VerificationDescendants(button).OfType<ContentPresenter>()
                    .FirstOrDefault()?.Foreground as Microsoft.UI.Xaml.Media.SolidColorBrush;
                passed &= pixel == dropdown && foreground?.Color.Equals(TextBrush.Color) == true;
                samples.Add(new { label = button.Content, background = pixel.ToString("X8"),
                    foreground = foreground?.Color.ToString() });
            }
            checks.Add(new { name = "neutral buttons match rendered dropdown " + state + " " + index,
                passed, actual = new { dropdown = dropdown.ToString("X8"), buttons = samples } });
            if (!passed) throw new InvalidOperationException("Neutral button visual state differs from dropdown: " + state);
            await VerificationCapture(Arg("--verify-dir"), "neutral-buttons-" + index + "-" + state.ToLowerInvariant());
        }
    }

}
