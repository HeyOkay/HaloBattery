using System.Diagnostics;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyCatalogPagePerformance(IconCatalogView view, List<object> checks)
    {
        // A deterministic 40px RGBA payload bounds native decoding cost without
        // network, Python rendering, or fixture JSON construction in the timer.
        const string png = "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";
        object[] Items(int start, int count = 50) => Enumerable.Range(start, count).Select(index => (object)new
        { id = "lucide:page-" + index, name = "Page icon " + index, collection = "Fixture", preview = png }).ToArray();
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        JsonElement Response(long id, int start, int count = 50, int? total = 999, bool prefetch = false, string context = "fixture-context") =>
            JsonSerializer.SerializeToElement(new { type = "catalog", id, anchorId = id, action = "search", prefetch,
                deviceKey = view.DeviceKey, query = view.Query, start, nextStart = start + 50, size = CatalogPixelSize,
                hasMore = total is { } limit ? start + 50 < limit : count == 50,
                total, contextKey = context, items = Items(start, count) });
        long SearchRequest() => view.Requests.Single(pair => pair.Value.Action == "search").Key;
        async Task Warm(int start)
        {
            var anchor = view.PrefetchAnchors.Keys.Last();
            ApplyIconCatalogResponse(Response(anchor, start, prefetch: true));
            for (var attempt = 0; attempt < 100 && (!view.Pages.TryGetValue(start, out var page) || page.Items.Any(tile => tile.Image is null)); attempt++)
                await Task.Delay(20);
            Check("catalog predecodes all fifty neighbouring thumbnails " + start,
                view.Pages.TryGetValue(start, out var warmed) && warmed.Items.Count == 50 && warmed.Items.All(tile => tile.Image is BitmapImage));
        }
        var query = view.Search.Text;
        var selection = view.SelectedId;
        var pageItems = view.Items.ToArray();
        view.Search.Focus(FocusState.Programmatic);
        view.Metrics = new CatalogMetrics();
        await Warm(50);
        Check("prefetch preserves visible items, query, selection and focus", view.Start == 0 && view.Items.SequenceEqual(pageItems) &&
            view.Search.Text == query && view.SelectedId == selection && view.Search.FocusState != FocusState.Unfocused);
        var prefetchedPage = view.Pages[50];
        var prefetched = prefetchedPage.Items.Select(tile => tile.Image).ToArray();
        var anchorBefore = view.PrefetchAnchors.Keys.Last();
        ApplyIconCatalogResponse(Response(anchorBefore, 50, prefetch: true, context: "wrong-battery-context"));
        Check("catalog rejects a mismatched prefetch ring context", ReferenceEquals(view.Pages[50], prefetchedPage) &&
            view.Pages[50].Items.Select(tile => tile.Image).SequenceEqual(prefetched));

        async Task SwitchWarmPage(int start)
        {
            var metrics = view.Metrics = new CatalogMetrics();
            var references = view.Pages[start].Items.Select(tile => tile.Image).ToArray();
            var frame = new TaskCompletionSource<double>(TaskCreationOptions.RunContinuationsAsynchronously);
            var started = Stopwatch.GetTimestamp();
            var firstFrameCount = 0;
            var firstFrameReady = false;
            EventHandler<object>? rendering = null;
            rendering = (_, _) =>
            {
                CompositionTarget.Rendering -= rendering;
                var containers = VerificationDescendants(view.Results).OfType<GridViewItem>()
                    .Where(item => item.Content is string && item.ContentTemplateRoot is Grid).ToArray();
                firstFrameCount = containers.Length;
                firstFrameReady = containers.Length > 0 && containers.All(item =>
                {
                    var panel = (Grid)item.ContentTemplateRoot;
                    return Equals(panel.Tag, item.Content) && ((Image)((Border)panel.Children[0]).Child).Source is BitmapImage;
                });
                frame.TrySetResult(Stopwatch.GetElapsedTime(started).TotalMilliseconds);
            };
            CompositionTarget.Rendering += rendering;
            double handlerMs, callbackMs;
            var commandsBefore = Bridge.FixtureCommands.Count;
            try
            {
                NavigateCatalogPage(view, start);
                handlerMs = Stopwatch.GetElapsedTime(started).TotalMilliseconds;
                callbackMs = await frame.Task.WaitAsync(TimeSpan.FromSeconds(3));
            }
            finally { CompositionTarget.Rendering -= rendering; }
            Check("warm page binds ready thumbnails on the next composition callback " + start, firstFrameReady, new { firstFrameCount });
            await Task.Delay(100);
            Check("warm page replaces fifty items without new bitmap decoding " + start, view.Start == start && view.Items.Count == 50 &&
                view.Items.Select(tile => tile.Image).SequenceEqual(references) && metrics.DecodeStarts == 0 && !view.Loading,
                new { synchronousHandlerMs = handlerMs, nextCompositionCallbackMs = callbackMs, metrics.DecodeStarts,
                    note = "Composition callback is not presented-frame telemetry." });
            Check("page navigation promotes backend warming without cancellation " + start,
                Bridge.FixtureCommands.Skip(commandsBefore).Select(command => JsonSerializer.Deserialize<JsonElement>(command))
                    .All(command => command.GetProperty("action").GetString() != "cancel"));
            using var wire = JsonDocument.Parse(Bridge.FixtureCommands.Last());
            Check("page validation uses fifty-step offsets and physical thumbnail extent " + start,
                wire.RootElement.GetProperty("start").GetInt32() == start && wire.RootElement.GetProperty("size").GetInt32() == CatalogPixelSize);
            var before = view.Items.ToArray();
            var id = SearchRequest();
            ApplyIconCatalogResponse(Response(id, start));
            Check("background page validation preserves native item and bitmap references " + start, view.Items.SequenceEqual(before) && !view.Loading);
            Check("gallery holds only current and adjacent pages " + start, view.Pages.Count <= 3 && view.ById.Count <= 150 &&
                view.Pages.Keys.All(offset => Math.Abs((long)offset - start) <= 50));
            view.Metrics = null;
        }
        await SwitchWarmPage(50);
        await Warm(100);
        await SwitchWarmPage(100);
        await Warm(150);
        Check("gallery retains at most one hundred fifty decoded bitmaps", view.ById.Values.Count(tile => tile.Image is not null) <= 150);
        async Task InvokePage(Button button, int target)
        {
            var peer = new Microsoft.UI.Xaml.Automation.Peers.ButtonAutomationPeer(button);
            ((Microsoft.UI.Xaml.Automation.Provider.IInvokeProvider)peer.GetPattern(
                Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Invoke)).Invoke();
            for (var attempt = 0; view.Start != target && attempt < 30; attempt++) await Task.Delay(10);
            Check("native pager button dispatches page " + target, view.Start == target && view.Items.Count == 50 && !view.Loading);
            ApplyIconCatalogResponse(Response(SearchRequest(), target));
        }
        await InvokePage(view.Pager.Children.OfType<Button>().First(), 50);
        await InvokePage(view.Pager.Children.OfType<Button>().Last(), 100);
        var commands = Bridge.FixtureCommands.Count;
        var currentButton = view.Pager.Children.OfType<Button>().Single(button => button.Content is string label && label == "3");
        var currentPeer = new Microsoft.UI.Xaml.Automation.Peers.ButtonAutomationPeer(currentButton);
        ((Microsoft.UI.Xaml.Automation.Provider.IInvokeProvider)currentPeer.GetPattern(
            Microsoft.UI.Xaml.Automation.Peers.PatternInterface.Invoke)).Invoke();
        await Task.Delay(20);
        Check("current page button leaves the gallery unchanged", Bridge.FixtureCommands.Count == commands && view.Start == 100);
        Check("pager compresses large ranges with ellipses and endpoint numbers",
            CatalogPageNumbers(4000, 8002, true).SequenceEqual(new[] { 0, 3999, 4000, 4001, 8001 }) &&
            CatalogPageNumbers(0, 20, true).SequenceEqual(new[] { 0, 1, 2, 3, 19 }) &&
            CatalogPageNumbers(19, 20, false).SequenceEqual(new[] { 0, 16, 17, 18, 19 }));
        Check("pager controls are separate native circles", view.Pager.Children.OfType<Button>().All(button =>
            button.Width == 32 && button.Height == 32 && button.Padding == new Thickness(0) && button.CornerRadius.TopLeft == 16));
        await VerificationCapture(Arg("--verify-dir"), "catalog-pagination");
        if (Arg("--capture-compositor") == "true")
            await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(Arg("--verify-dir"), "catalog-pagination.composited.png"));

        NavigateCatalogPage(view, 950);
        var lastRequest = SearchRequest();
        ApplyIconCatalogResponse(Response(lastRequest, 950, 49));
        Check("last page replaces the list and disables next", view.Items.Count == 49 && view.Start == 950 && !view.HasMore &&
            !view.Pager.Children.OfType<Button>().Last().IsEnabled && view.Status.Text.Contains("951–999 / 999"));
        NavigateCatalogPage(view, 100, force: true);
        var staleRequest = SearchRequest();
        NavigateCatalogPage(view, 200, force: true);
        var currentRequest = SearchRequest();
        ApplyIconCatalogResponse(Response(staleRequest, 100));
        Check("catalog drops an obsolete page response", view.Start == 200 && view.Items.Count == 0);
        ApplyIconCatalogResponse(Response(currentRequest, 200));
        ApplyIconCatalogResponse(Response(anchorBefore, 250, prefetch: true));
        Check("catalog drops prefetch from an obsolete anchor", !view.Pages.ContainsKey(250));

        // An exact total is optional for transport compatibility: next/previous
        // still work from hasMore while every request advances by fifty.
        view.Total = null;
        NavigateCatalogPage(view, 0, force: true);
        ApplyIconCatalogResponse(Response(SearchRequest(), 0, total: null));
        Check("pager works before an exact total arrives", view.Total is null && view.HasMore && view.Pager.Children.OfType<Button>().Last().IsEnabled);
        NavigateCatalogPage(view, 50, force: true);
        ApplyIconCatalogResponse(Response(SearchRequest(), 50, 7, total: null));
        Check("unknown-total terminal page disables next", view.Items.Count == 7 && !view.HasMore && !view.Pager.Children.OfType<Button>().Last().IsEnabled);

        NavigateCatalogPage(view, 100, force: true);
        var offline = SearchRequest();
        ApplyIconCatalogResponse(JsonSerializer.SerializeToElement(new { type = "catalog", id = offline, action = "search",
            deviceKey = view.DeviceKey, query = view.Query, start = 100, offline = true, errorCode = "offline" }));
        Check("uncached offline page exposes retry", view.Retry.Visibility == Visibility.Visible && !view.Loading &&
            view.Status.Text == T("settings.iconify_offline_error"));
        NavigateCatalogPage(view, 50);
        var changed = JsonSerializer.Deserialize<UiState>(JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
        changed.Devices.First(device => device.Key == view.DeviceKey).Paused = !SelectedDevice!.Paused;
        var commandsBeforeRingChange = Bridge.FixtureCommands.Count;
        ApplyState(changed);
        Check("ring-state change cancels stale backend preview work", Bridge.FixtureCommands.Skip(commandsBeforeRingChange)
            .Select(command => JsonSerializer.Deserialize<JsonElement>(command)).Any(command => command.TryGetProperty("action", out var action) && action.GetString() == "cancel"));
        Check("ring-state change invalidates warmed pages without changing query or focus", view.Pages.Count == 0 && view.Search.Text == query &&
            view.Search.FocusState != FocusState.Unfocused && view.Start == 50);
        view.Metrics = null;
        await VerifyCatalogPagerMinimumWidth(view, checks);
    }
    private async Task VerifyCatalogPagerMinimumWidth(IconCatalogView view, List<object> checks)
    {
        if (AppWindow.Presenter is not Microsoft.UI.Windowing.OverlappedPresenter presenter) return;
        var maximized = presenter.State == Microsoft.UI.Windowing.OverlappedPresenterState.Maximized;
        presenter.Restore();
        await Task.Delay(350);
        var normalSize = AppWindow.Size;
        var wasExpanded = expanded;
        var originalStart = view.Start; var originalTotal = view.Total; var originalMore = view.HasMore;
        try
        {
            if (!expanded) { ToggleRail(); await Task.Delay(350); }
            AppWindow.Resize(new Windows.Graphics.SizeInt32((int)Math.Ceiling(MinimumWindowWidthDip * Scale), normalSize.Height));
            await Task.Delay(350);
            // Seven numbered pages plus two arrows is the widest pager. Larger
            // counts use ellipses and fewer number buttons instead.
            view.Start = 0; view.Total = 350; view.HasMore = true;
            RenderCatalogPager(view);
            view.Panel.StartBringIntoView(new BringIntoViewOptions { VerticalAlignmentRatio = 0 });
            await Task.Delay(150);
            root.UpdateLayout();
            var body = (FrameworkElement)view.Pager.Parent;
            var bounds = view.Pager.TransformToVisual(body).TransformBounds(new Windows.Foundation.Rect(0, 0,
                view.Pager.ActualWidth, view.Pager.ActualHeight));
            var tolerance = 1 / Scale + .01;
            var passed = expanded && view.Pager.Children.OfType<Button>().Count() == 9 &&
                bounds.Left >= -tolerance && bounds.Right <= body.ActualWidth + tolerance;
            checks.Add(new { name = "widest nine-circle pager fits minimum window with expanded navigation", passed,
                actual = new { windowDip = root.ActualWidth, bodyWidth = body.ActualWidth,
                    pagerWidth = bounds.Width, left = bounds.Left, right = bounds.Right, tolerance } });
            if (!passed) throw new InvalidOperationException("Catalog pager overflows minimum expanded window");
            await VerificationCapture(Arg("--verify-dir"), "catalog-pagination-minimum-expanded");
            if (Arg("--capture-compositor") == "true")
                await FixtureWindowCapture.CaptureAsync(hwnd, System.IO.Path.Combine(Arg("--verify-dir"), "catalog-pagination-minimum-expanded.composited.png"));
        }
        finally
        {
            view.Start = originalStart; view.Total = originalTotal; view.HasMore = originalMore;
            RenderCatalogPager(view);
            if (expanded != wasExpanded) { ToggleRail(); await Task.Delay(350); }
            AppWindow.Resize(normalSize);
            if (maximized) presenter.Maximize();
            await Task.Delay(350);
        }
    }
}
