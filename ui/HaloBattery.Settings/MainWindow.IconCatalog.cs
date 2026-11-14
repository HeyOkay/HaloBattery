using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage.Streams;
using Windows.System;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private IconCatalogView? iconCatalog;
    private readonly Dictionary<string, string> iconCatalogQueries = new();
    private bool iconCatalogCloseHook, iconCatalogDialogOpen;
    private readonly HashSet<long> iconCatalogLicenseRequests = new();

    private sealed class CatalogTile
    {
        public string Id { get; init; } = "";
        public string Name { get; init; } = "";
        public string Collection { get; init; } = "";
        public string Preview { get; init; } = "";
        public double ThumbnailDip { get; init; }
        public SolidColorBrush PreviewBackground { get; init; } = new();
        public ImageSource? Image { get; set; }
        public Task<BitmapImage>? Decode { get; set; }
    }
    private sealed class CatalogMetrics
    {
        public long Lookups, LookupComparisons, ContainsComparisons;
        public int ContainerUpdates, Recycles, DecodeStarts, DecodeCompletions, CacheHits, DuplicateDecodes, SharedDecodeWaiters;
        public readonly Dictionary<string, int> ActiveDecodes = new();
        public readonly Dictionary<string, long> CompletedAt = new();
        public void StartDecode(string id)
        {
            DecodeStarts++;
            var active = ActiveDecodes.GetValueOrDefault(id);
            if (active > 0) DuplicateDecodes++;
            ActiveDecodes[id] = active + 1;
        }
        public void FinishDecode(string id)
        {
            DecodeCompletions++;
            CompletedAt[id] = System.Diagnostics.Stopwatch.GetTimestamp();
            if (ActiveDecodes.GetValueOrDefault(id) <= 1) ActiveDecodes.Remove(id);
            else ActiveDecodes[id]--;
        }
    }
    private static CatalogTile? FindCatalogTile(IconCatalogView view, string id)
    {
        if (view.Metrics is { } metrics) { metrics.Lookups++; metrics.LookupComparisons++; }
        return view.ById.GetValueOrDefault(id);
    }
    private static bool ContainsCatalogTile(IconCatalogView view, CatalogTile tile)
    {
        if (view.Metrics is { } metrics) metrics.ContainsComparisons++;
        return view.ById.TryGetValue(tile.Id, out var current) && ReferenceEquals(current, tile);
    }
    private const int CatalogPageSize = 50;
    private const int CatalogThumbnailCacheLimit = CatalogPageSize * 3;
    private static void CacheCatalogThumbnail(IconCatalogView view, CatalogTile tile, ImageSource image)
    {
        tile.Image = image;
        if (view.ThumbnailNodes.Remove(tile.Id, out var old)) view.ThumbnailOrder.Remove(old);
        view.ThumbnailNodes[tile.Id] = view.ThumbnailOrder.AddLast(tile.Id);
        while (view.ThumbnailOrder.Count > CatalogThumbnailCacheLimit)
        {
            var expired = view.ThumbnailOrder.First!.Value;
            view.ThumbnailOrder.RemoveFirst();
            view.ThumbnailNodes.Remove(expired);
            if (view.ById.TryGetValue(expired, out var previous)) previous.Image = null;
        }
    }
    private static async Task<BitmapImage> DecodeCatalogThumbnail(IconCatalogView view, CatalogTile tile)
    {
        var metrics = view.Metrics;
        metrics?.StartDecode(tile.Id);
        try { return await CatalogBitmap(tile.Preview); }
        finally { metrics?.FinishDecode(tile.Id); }
    }
    private async Task<ImageSource> GetCatalogThumbnail(IconCatalogView view, CatalogTile tile)
    {
        if (tile.Image is { } cached)
        {
            if (view.Metrics is { } metrics) metrics.CacheHits++;
            CacheCatalogThumbnail(view, tile, cached);
            return cached;
        }
        if (tile.Decode is null) tile.Decode = DecodeCatalogThumbnail(view, tile);
        else if (view.Metrics is { } metrics) metrics.SharedDecodeWaiters++;
        var pending = tile.Decode;
        try
        {
            var image = await pending;
            if (CatalogActive(view) && ContainsCatalogTile(view, tile)) CacheCatalogThumbnail(view, tile, image);
            return image;
        }
        finally { if (ReferenceEquals(tile.Decode, pending)) tile.Decode = null; }
    }
    private sealed record CatalogRequest(string Action, long SearchGeneration, long DetailGeneration, string? IconId, int Start, bool Background);
    private sealed record CatalogPage(int Start, List<CatalogTile> Items, bool HasMore, int? Total, bool Cached, bool Offline);
    private sealed class IconCatalogView
    {
        public required string DeviceKey;
        public required HaloBattery.Settings.Controls.CardView Panel;
        public XamlRoot? Root;
        public Windows.Foundation.TypedEventHandler<XamlRoot, XamlRootChangedEventArgs>? RootChanged;
        public required TextBox Search;
        public required GridView Results;
        public required TextBlock Status;
        public required TextBlock Metadata;
        public required TextBlock Current;
        public required ProgressRing Busy;
        public required StackPanel Pager;
        public required Button Apply;
        public required Button Retry;
        public required Button License;
        public required Image Preview80;
        public required Image PreviewActual;
        public required Border PreviewBackground;
        public readonly List<CatalogTile> Items = new();
        public readonly Dictionary<int, CatalogPage> Pages = new();
        public readonly Dictionary<string, CatalogTile> ById = new();
        public readonly LinkedList<string> ThumbnailOrder = new();
        public readonly Dictionary<string, LinkedListNode<string>> ThumbnailNodes = new();
        public CatalogMetrics? Metrics;
        public readonly Dictionary<long, CatalogRequest> Requests = new();
        public readonly Dictionary<long, long> PrefetchAnchors = new();
        public CancellationTokenSource? Debounce;
        public long SearchGeneration, DetailGeneration;
        public bool Active = true, Loading;
        public bool HasMore;
        public int NextStart, Start;
        public int? Total;
        public double Scale;
        public bool IconLightTaskbar;
        public string Query = "", SelectedId = "", Notices = "", PreviewSignature = "", ContextKey = "";
    }

    private int CatalogPixelSize => Math.Clamp((int)Math.Round(32 * Scale), 8, 192);
    private bool CatalogActive(IconCatalogView view) => !closing && view.Active &&
        ReferenceEquals(iconCatalog, view) && current == "devices" && selectedDeviceKey == view.DeviceKey;

    private void StopIconCatalog()
    {
        if (iconCatalog is not { } view) return;
        view.Active = false;
        if (view.Root is not null && view.RootChanged is not null) view.Root.Changed -= view.RootChanged;
        view.Debounce?.Cancel();
        view.Debounce?.Dispose();
        iconCatalogQueries[view.DeviceKey] = view.Search.Text;
        Bridge.RequestCatalog("cancel", view.DeviceKey, size: CatalogPixelSize);
        view.Requests.Clear(); view.PrefetchAnchors.Clear();
        ClearCatalogPages(view);
        view.Preview80.Source = view.PreviewActual.Source = null;
        iconCatalog = null;
    }

    private void BuildIconCatalog(StackPanel parent, string deviceKey)
    {
        if (!iconCatalogCloseHook)
        {
            iconCatalogCloseHook = true;
            Closed += (_, _) => { StopIconCatalog(); iconCatalogQueries.Clear(); iconCatalogLicenseRequests.Clear(); };
        }
        Section(parent, T("settings.iconify_title"));
        var browse = Button(T("settings.iconify_browse"), true);
        var catalog = Expander(T("settings.iconify_title"), T("settings.iconify_hint"), browse, "palette");
        parent.Children.Add(catalog);
        var body = new StackPanel { Spacing = 12, Margin = new(24, 16, 24, 20) };
        catalog.AddBody(body);
        var search = new TextBox
        {
            Text = iconCatalogQueries.GetValueOrDefault(deviceKey, ""), MaxLength = 100,
            PlaceholderText = T("settings.iconify_search"), FontSize = 13,
            Height = 32, MinHeight = 32, CornerRadius = new(16), Padding = new(14, 4, 14, 4),
            Background = Brush(dark ? "#303030" : "#ECECEC"), Foreground = TextBrush, BorderThickness = new(0)
        };
        AutomationProperties.SetName(search, T("settings.iconify_search"));
        var searchButton = Button(T("settings.iconify_search_button"), true);
        var searchRow = new Grid { ColumnSpacing = 8 };
        searchRow.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        searchRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        searchRow.Children.Add(search); Grid.SetColumn(searchButton, 1); searchRow.Children.Add(searchButton);
        body.Children.Add(searchRow);
        var status = Text(T("settings.iconify_ready"), 12, true);
        AutomationProperties.SetLiveSetting(status, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        var busy = new ProgressRing { Width = 18, Height = 18, IsActive = false, Visibility = Visibility.Collapsed };
        var statusRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        statusRow.Children.Add(busy); statusRow.Children.Add(status); body.Children.Add(statusRow);
        var results = new GridView
        {
            Height = 300, MinHeight = 180, SelectionMode = ListViewSelectionMode.Single,
            IsItemClickEnabled = false, HorizontalAlignment = HorizontalAlignment.Stretch,
            Background = Brush("#00000000"), Foreground = TextBrush,
            ItemsPanel = (ItemsPanelTemplate)XamlReader.Load("<ItemsPanelTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'><ItemsWrapGrid Orientation='Horizontal' ItemWidth='144' ItemHeight='120' CacheLength='1'/></ItemsPanelTemplate>"),
            ItemTemplate = (DataTemplate)XamlReader.Load("<DataTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'><Grid Width='136' Height='112' RowSpacing='6' Padding='10'><Grid.RowDefinitions><RowDefinition Height='48'/><RowDefinition Height='16'/><RowDefinition Height='16'/></Grid.RowDefinitions><Border CornerRadius='8'><Image Stretch='Fill'/></Border><TextBlock Grid.Row='1' FontSize='12' VerticalAlignment='Center' TextTrimming='CharacterEllipsis' MaxLines='1'/><TextBlock Grid.Row='2' FontSize='10' VerticalAlignment='Center' Opacity='0.7' TextTrimming='CharacterEllipsis' MaxLines='1'/></Grid></DataTemplate>")
        };
        // One inset owns the tile layout in every native selection state.
        // Explicit container geometry prevents the default presenter padding
        // from consuming the space reserved for the second label.
        var tileStyle = new Style(typeof(GridViewItem)) { BasedOn = (Style)Application.Current.Resources["DefaultGridViewItemStyle"] };
        tileStyle.Setters.Add(new Setter(GridViewItem.WidthProperty, 136.0));
        tileStyle.Setters.Add(new Setter(GridViewItem.HeightProperty, 112.0));
        tileStyle.Setters.Add(new Setter(GridViewItem.MinWidthProperty, 0.0));
        tileStyle.Setters.Add(new Setter(GridViewItem.MinHeightProperty, 0.0));
        tileStyle.Setters.Add(new Setter(GridViewItem.MarginProperty, new Thickness(4)));
        tileStyle.Setters.Add(new Setter(GridViewItem.PaddingProperty, new Thickness(0)));
        tileStyle.Setters.Add(new Setter(GridViewItem.CornerRadiusProperty, new CornerRadius(10)));
        tileStyle.Setters.Add(new Setter(GridViewItem.HorizontalContentAlignmentProperty, HorizontalAlignment.Stretch));
        tileStyle.Setters.Add(new Setter(GridViewItem.VerticalContentAlignmentProperty, VerticalAlignment.Stretch));
        results.ItemContainerStyle = tileStyle;
        results.Resources["GridViewItemCornerRadius"] = new CornerRadius(10);
        ScrollViewer.SetHorizontalScrollBarVisibility(results, ScrollBarVisibility.Disabled);
        ScrollViewer.SetVerticalScrollBarVisibility(results, ScrollBarVisibility.Auto);
        AutomationProperties.SetName(results, T("settings.iconify_results"));
        body.Children.Add(results);
        var pager = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6,
            HorizontalAlignment = HorizontalAlignment.Center, Visibility = Visibility.Collapsed };
        AutomationProperties.SetName(pager, T("settings.iconify_pages"));
        body.Children.Add(pager);
        var retry = Button(T("settings.iconify_retry"), true); retry.Visibility = Visibility.Collapsed;
        body.Children.Add(ButtonRow(retry));
        var currentIcon = Text("", 12, true); body.Children.Add(currentIcon);
        body.Children.Add(Text(T("settings.iconify_priority"), 12, true));
        var large = new Image { Width = 80 / Scale, Height = 80 / Scale, Stretch = Stretch.Fill };
        var actual = new Image { Stretch = Stretch.Fill };
        var images = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 24, VerticalAlignment = VerticalAlignment.Center };
        images.Children.Add(large); images.Children.Add(actual);
        var previewBackground = new Border { Padding = new(16), CornerRadius = new(12), Child = images };
        body.Children.Add(previewBackground);
        var metadata = Text(T("settings.iconify_select"), 12, true); metadata.IsTextSelectionEnabled = true; body.Children.Add(metadata);
        var apply = Button(T("settings.iconify_apply"), true, primary: true); apply.IsEnabled = false;
        var restore = Button(T("settings.iconify_restore"), true);
        var license = Button(T("settings.iconify_license"), true); license.IsEnabled = false;
        var allLicenses = Button(T("settings.iconify_licenses"), true);
        body.Children.Add(ButtonRow(license, allLicenses));
        body.Children.Add(ButtonRow(restore, apply));
        var view = new IconCatalogView
        {
            DeviceKey = deviceKey, Panel = catalog, Search = search, Results = results, Status = status, Metadata = metadata,
            Current = currentIcon, Busy = busy, Pager = pager, Apply = apply, Retry = retry, License = license,
            Preview80 = large, PreviewActual = actual, PreviewBackground = previewBackground, Scale = Scale, IconLightTaskbar = State.IconLightTaskbar, PreviewSignature = CatalogPreviewSignature()
        };
        // Native Items contains only strings. A managed ObservableCollection's
        // generic CCW reflection paths are trimmed by WinRT.Runtime; using native
        // Items also keeps the GridView's default container virtualization.
        iconCatalog = view;
        body.Loaded += (_, _) =>
        {
            if (!CatalogActive(view) || root.XamlRoot is null || view.Root is not null) return;
            view.Root = root.XamlRoot;
            view.RootChanged = (_, _) => RefreshCatalogScale(view);
            view.Root.Changed += view.RootChanged;
        };
        browse.Click += (_, _) => { if (!CatalogActive(view)) return; catalog.IsExpanded = true; catalog.StartBringIntoView(new BringIntoViewOptions { VerticalAlignmentRatio = 0 }); search.Focus(FocusState.Programmatic); if (view.Items.Count == 0 && !view.Loading) BeginCatalogSearch(view); };
        search.TextChanged += async (_, _) =>
        {
            if (!CatalogActive(view) || !catalog.IsExpanded) return;
            InvalidateCatalogSearch(view);
            var debounce = view.Debounce = new CancellationTokenSource();
            try { await Task.Delay(300, debounce.Token); if (CatalogActive(view) && catalog.IsExpanded) BeginCatalogSearch(view); }
            catch (OperationCanceledException) { }
        };
        searchButton.Click += (_, _) => BeginCatalogSearch(view);
        search.KeyDown += (_, e) => { if (e.Key == VirtualKey.Enter) { BeginCatalogSearch(view); e.Handled = true; } };
        retry.Click += (_, _) => NavigateCatalogPage(view, view.Start, force: true);
        results.SelectionChanged += (_, _) =>
        {
            if (!CatalogActive(view)) return;
            view.DetailGeneration++; view.Apply.IsEnabled = view.License.IsEnabled = false;
            view.Notices = ""; view.Preview80.Source = view.PreviewActual.Source = null;
            if (results.SelectedItem is string selectedId && FindCatalogTile(view, selectedId) is { } tile)
            {
                view.SelectedId = tile.Id; view.Metadata.Text = tile.Id;
                SendCatalog(view, "details", iconId: tile.Id);
            }
            else { view.SelectedId = ""; view.Metadata.Text = T("settings.iconify_select"); }
        };
        async void UpdateCatalogContainer(ListViewBase sender, ContainerContentChangingEventArgs args)
        {
            if (view.Metrics is { } metrics) metrics.ContainerUpdates++;
            if (args.InRecycleQueue)
            {
                if (view.Metrics is { } recycledMetrics) recycledMetrics.Recycles++;
                // Keep recently decoded thumbnails in the bounded cache.
                if (args.ItemContainer.ContentTemplateRoot is Grid recycledPanel)
                {
                    recycledPanel.Tag = null;
                    ((Image)((Border)recycledPanel.Children[0]).Child).Source = null;
                }
                return;
            }
            if (args.Phase == 0 && (args.Item is not string readyId || FindCatalogTile(view, readyId)?.Image is null)) { args.RegisterUpdateCallback(UpdateCatalogContainer); return; }
            if (args.Item is not string id || FindCatalogTile(view, id) is not { } tile ||
                args.ItemContainer.ContentTemplateRoot is not Grid panel) return;
            panel.Tag = id;
            var background = (Border)panel.Children[0];
            var image = (Image)background.Child;
            image.Source = tile.Image; image.Width = image.Height = tile.ThumbnailDip;
            background.Background = tile.PreviewBackground;
            ((TextBlock)panel.Children[1]).Text = tile.Name;
            ((TextBlock)panel.Children[2]).Text = tile.Collection;
            AutomationProperties.SetName(args.ItemContainer, tile.Name + " · " + tile.Collection);
            try
            {
                var bitmap = await GetCatalogThumbnail(view, tile);
                if (CatalogActive(view) && ContainsCatalogTile(view, tile) && Equals(panel.Tag, id))
                {
                    image.Source = bitmap;
                }
            }
            catch (Exception error) when (error is FormatException or ArgumentException or System.Runtime.InteropServices.COMException or IOException)
            { System.Diagnostics.Debug.WriteLine(error); }
        }
        results.ContainerContentChanging += UpdateCatalogContainer;
        apply.Click += (_, _) => { if (CatalogActive(view) && view.SelectedId.Length > 0) { view.Apply.IsEnabled = false; SendCatalog(view, "apply", iconId: view.SelectedId); } };
        restore.Click += (_, _) => { if (CatalogActive(view)) SendCatalog(view, "restore"); };
        license.Click += async (_, _) => { if (CatalogActive(view)) await ShowCatalogNotices(view.Notices); };
        allLicenses.Click += (_, _) => { if (CatalogActive(view)) SendCatalog(view, "licenses"); };
        pageBindings.Add(() =>
        {
            if (!CatalogActive(view)) return;
            view.Current.Text = SelectedDevice?.CustomIconName is { Length: > 0 } id
                ? T("settings.iconify_current") + ": " + id : T("settings.iconify_default");
            view.PreviewBackground.Background = Brush(State.IconLightTaskbar ? "#F3F3F3" : "#202020");
            RefreshCatalogScale(view);
        });
    }

    private string CatalogPreviewSignature()
    {
        var device = SelectedDevice;
        return string.Join("|", device?.Level, device?.Charging, device?.Online, device?.LowOverride, device?.Paused,
            State.IconLightTaskbar, State.Settings.GetValueOrDefault("low").ToString(),
            State.Settings.GetValueOrDefault("ring_style").ToString());
    }
    private void RefreshCatalogScale(IconCatalogView view)
    {
        var signature = CatalogPreviewSignature();
        if (!CatalogActive(view) || Math.Abs(view.Scale - Scale) <= .001 && view.PreviewSignature == signature) return;
        view.IconLightTaskbar = State.IconLightTaskbar; view.PreviewSignature = signature;
        view.Scale = Scale; view.Preview80.Width = view.Preview80.Height = 80 / Scale;
        var reload = view.Items.Count > 0 || view.Loading;
        InvalidateCatalogSearch(view); ClearCatalogPages(view); ResetCatalogSelection(view);
        if (reload && view.Panel.IsExpanded) SendCatalog(view, "search", view.Start);
    }

    private void InvalidateCatalogSearch(IconCatalogView view, bool cancelBackend = true)
    {
        view.SearchGeneration++; view.DetailGeneration++;
        view.Debounce?.Cancel(); view.Debounce?.Dispose(); view.Debounce = null;
        view.Loading = false; view.Busy.IsActive = false; view.Busy.Visibility = Visibility.Collapsed;
        view.Apply.IsEnabled = view.License.IsEnabled = false;
        view.Requests.Clear(); view.PrefetchAnchors.Clear();
        if (cancelBackend) Bridge.RequestCatalog("cancel", view.DeviceKey, size: CatalogPixelSize);
    }
    private void BeginCatalogSearch(IconCatalogView view)
    {
        if (!CatalogActive(view) || !view.Panel.IsExpanded) return;
        InvalidateCatalogSearch(view);
        view.Query = view.Search.Text.Trim(); iconCatalogQueries[view.DeviceKey] = view.Query;
        ClearCatalogPages(view);
        view.Total = null; view.HasMore = false; view.NextStart = 0; view.Start = 0;
        ResetCatalogSelection(view);
        SendCatalog(view, "search");
    }
    private void SendCatalog(IconCatalogView view, string action, int start = 0, string? iconId = null, bool background = false)
    {
        if (!CatalogActive(view)) return;
        var id = Bridge.RequestCatalog(action, view.DeviceKey, view.Query, start, CatalogPixelSize, iconId);
        if (id == 0) { view.Status.Text = T("settings.iconify_error"); return; }
        view.Requests[id] = new(action, view.SearchGeneration, view.DetailGeneration, iconId, start, background);
        if (action == "search") view.PrefetchAnchors[id] = view.SearchGeneration;
        if (background) return;
        view.Loading = true; view.Busy.IsActive = true; view.Busy.Visibility = Visibility.Visible;
        view.Status.Text = T("settings.iconify_loading"); view.Retry.Visibility = Visibility.Collapsed;
        RenderCatalogPager(view);
    }

    private static void ClearCatalogPages(IconCatalogView view)
    {
        view.Results.Items.Clear(); view.Items.Clear();
        foreach (var tile in view.ById.Values) tile.Image = null;
        view.ById.Clear(); view.Pages.Clear(); view.ThumbnailNodes.Clear(); view.ThumbnailOrder.Clear(); view.ContextKey = "";
    }
    private void ResetCatalogSelection(IconCatalogView view)
    {
        view.SelectedId = ""; view.Results.SelectedItem = null;
        view.Apply.IsEnabled = view.License.IsEnabled = false;
        view.Preview80.Source = view.PreviewActual.Source = null;
        view.Notices = ""; view.Metadata.Text = T("settings.iconify_select");
    }
    private void NavigateCatalogPage(IconCatalogView view, int start, bool force = false)
    {
        if (!CatalogActive(view) || !view.Panel.IsExpanded || view.Search.Text.Trim() != view.Query || start < 0 || start % CatalogPageSize != 0) return;
        if (view.Total is { } total && start > Math.Max(0, (total - 1) / CatalogPageSize) * CatalogPageSize) return;
        // Keep the server's neighbour rendering alive: this search can
        // promote its in-flight warm operation instead of starting it again.
        InvalidateCatalogSearch(view, cancelBackend: false);
        view.Start = start;
        ResetCatalogSelection(view);
        if (!force && view.Pages.TryGetValue(start, out var cached))
        {
            ShowCatalogPage(view, cached);
            // Establish a fresh anchor for server-side neighbour warming.
            // The validation response does not blank or rebuild this page.
            SendCatalog(view, "search", start, background: true);
        }
        else
        {
            view.Results.Items.Clear(); view.Items.Clear();
            SendCatalog(view, "search", start);
        }
    }
    private void ShowCatalogPage(IconCatalogView view, CatalogPage page)
    {
        view.Start = page.Start; view.HasMore = page.HasMore;
        view.NextStart = page.Start + CatalogPageSize;
        if (page.Total is { } total) view.Total = total;
        CacheCatalogPage(view, page);
        view.Results.Items.Clear(); view.Items.Clear();
        foreach (var tile in page.Items) { view.Items.Add(tile); view.Results.Items.Add(tile.Id); }
        if (CatalogScrollViewer(view.Results) is { } scroll)
            scroll.ChangeView(null, 0, null, true);
        view.Status.Text = T(page.Offline ? "settings.iconify_offline" : page.Cached ? "settings.iconify_cached" :
            page.Items.Count == 0 ? "settings.iconify_empty" : "settings.iconify_results") +
            (page.Items.Count > 0 ? " · " + (page.Start + 1) + "–" + (page.Start + page.Items.Count) +
                (view.Total is { } count ? " / " + count : "") : "");
        RenderCatalogPager(view);
    }
    private static void CacheCatalogPage(IconCatalogView view, CatalogPage page)
    {
        view.Pages[page.Start] = page;
        foreach (var start in view.Pages.Keys.Where(start => Math.Abs((long)start - view.Start) > CatalogPageSize).ToArray())
            view.Pages.Remove(start);
        var retained = view.Pages.Values.SelectMany(value => value.Items).GroupBy(tile => tile.Id)
            .ToDictionary(group => group.Key, group => group.First());
        foreach (var tile in view.ById.Values)
        {
            if (retained.ContainsKey(tile.Id)) continue;
            tile.Image = null;
            if (view.ThumbnailNodes.Remove(tile.Id, out var node)) view.ThumbnailOrder.Remove(node);
        }
        view.ById.Clear();
        foreach (var pair in retained) view.ById[pair.Key] = pair.Value;
    }
    private CatalogPage ReadCatalogPage(IconCatalogView view, JsonElement message)
    {
        var tiles = new List<CatalogTile>();
        var seen = new HashSet<string>();
        if (message.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in items.EnumerateArray().Take(CatalogPageSize))
            {
                var id = CatalogString(item, "id");
                if (id.Length == 0 || !seen.Add(id)) continue;
                var tile = view.ById.GetValueOrDefault(id) ?? new CatalogTile { Id = id, Name = CatalogString(item, "name"),
                    Collection = CatalogString(item, "collection"), Preview = CatalogString(item, "preview"),
                    ThumbnailDip = CatalogPixelSize / Scale,
                    PreviewBackground = Brush(State.IconLightTaskbar ? "#F3F3F3" : "#202020") };
                tiles.Add(tile);
            }
        }
        var total = CatalogInt(message, "total", -1);
        return new(CatalogInt(message, "start"), tiles, CatalogBool(message, "hasMore"), total >= 0 ? total : null,
            CatalogBool(message, "cached"), CatalogBool(message, "offline"));
    }
    private async Task AcceptCatalogPrefetch(IconCatalogView view, JsonElement message)
    {
        if (!CatalogActive(view) || !view.Panel.IsExpanded || CatalogString(message, "action") != "search" ||
            !message.TryGetProperty("anchorId", out var anchorValue) || !anchorValue.TryGetInt64(out var anchor) ||
            !view.PrefetchAnchors.TryGetValue(anchor, out var generation) || generation != view.SearchGeneration ||
            CatalogString(message, "deviceKey") != view.DeviceKey || CatalogString(message, "query") != view.Query ||
            CatalogInt(message, "size") != CatalogPixelSize || view.ContextKey.Length == 0 ||
            CatalogString(message, "contextKey") != view.ContextKey || CatalogString(message, "errorCode").Length > 0) return;
        var start = CatalogInt(message, "start", -1);
        if (start < 0 || start % CatalogPageSize != 0 || Math.Abs((long)start - view.Start) != CatalogPageSize) return;
        try
        {
            var page = ReadCatalogPage(view, message);
            CacheCatalogPage(view, page);
            // WinUI SetSourceAsync yields while decoding. Warm every neighbour
            // image before a click; keep current items, selection and focus intact.
            await Task.WhenAll(page.Items.Select(tile => GetCatalogThumbnail(view, tile)));
        }
        catch (Exception error) when (error is JsonException or FormatException or ArgumentException or IOException or
            System.Runtime.InteropServices.COMException or InvalidOperationException)
        { System.Diagnostics.Debug.WriteLine(error); }
    }
    private static ScrollViewer? CatalogScrollViewer(DependencyObject element)
    {
        if (element is ScrollViewer scroll) return scroll;
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(element); index++)
            if (CatalogScrollViewer(VisualTreeHelper.GetChild(element, index)) is { } child) return child;
        return null;
    }
    private static int[] CatalogPageNumbers(int page, int? totalPages, bool hasMore)
    {
        var last = totalPages is { } count ? Math.Max(0, count - 1) : page + (hasMore ? 1 : 0);
        if (last < 7) return Enumerable.Range(0, last + 1).ToArray();
        var firstMiddle = Math.Clamp(page - 1, 1, last - 3);
        return new[] { 0 }.Concat(Enumerable.Range(firstMiddle, 3)).Append(last).Distinct().Order().ToArray();
    }
    private void RenderCatalogPager(IconCatalogView view)
    {
        var focused = root.XamlRoot is { } xaml && Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(xaml) is Button currentButton &&
            view.Pager.Children.Contains(currentButton) ? AutomationProperties.GetName(currentButton) : null;
        view.Pager.Children.Clear();
        var page = view.Start / CatalogPageSize;
        var pageCount = view.Total is { } total ? Math.Max(1, (int)(((long)total + CatalogPageSize - 1) / CatalogPageSize)) : (int?)null;
        view.Pager.Visibility = view.Items.Count > 0 || view.HasMore || view.Start > 0 || view.Loading ? Visibility.Visible : Visibility.Collapsed;
        Button PageButton(string label, int target, bool enabled, bool selected = false)
        {
            var button = Button(label, true);
            // Round controls share neutral hover/pressed resources with the
            // existing pill buttons and dropdowns; selected uses a quiet fill.
            button.Width = button.MinWidth = button.Height = button.MinHeight = 32;
            button.Padding = new(0); button.CornerRadius = new(16); button.IsEnabled = enabled;
            button.Tag = target;
            if (label.Length > 3) button.FontSize = 11;
            if (selected)
            {
                button.Background = Brush(dark ? "#505050" : "#D4D4D4");
                button.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
                AutomationProperties.SetItemStatus(button, T("settings.iconify_current_page"));
            }
            AutomationProperties.SetName(button, T("settings.iconify_page").Replace("{page}", (target / CatalogPageSize + 1).ToString()));
            button.Click += (_, _) => { if (target != view.Start && !view.Loading) NavigateCatalogPage(view, target); };
            return button;
        }
        Button Arrow(bool previous)
        {
            var target = previous ? Math.Max(0, view.Start - CatalogPageSize) : view.Start + CatalogPageSize;
            var button = PageButton("", target, !view.Loading && (previous ? page > 0 : view.HasMore));
            var glyph = (FrameworkElement)LucideIcon("chevron-right", 16);
            if (previous) { glyph.RenderTransformOrigin = new(.5, .5); glyph.RenderTransform = new RotateTransform { Angle = 180 }; }
            button.Content = glyph;
            AutomationProperties.SetName(button, T(previous ? "settings.iconify_previous" : "settings.iconify_next"));
            return button;
        }
        view.Pager.Children.Add(Arrow(true));
        var prior = -1;
        foreach (var number in CatalogPageNumbers(page, pageCount, view.HasMore))
        {
            if (prior >= 0 && number - prior > 1)
                view.Pager.Children.Add(new TextBlock { Text = "…", Width = 20, FontFamily = bodyFont,
                    Foreground = SubtleBrush, TextAlignment = TextAlignment.Center, VerticalAlignment = VerticalAlignment.Center });
            view.Pager.Children.Add(PageButton((number + 1).ToString(), number * CatalogPageSize, !view.Loading, number == page));
            prior = number;
        }
        view.Pager.Children.Add(Arrow(false));
        if (focused is { } oldName && view.Pager.Children.OfType<Button>().FirstOrDefault(button =>
            AutomationProperties.GetName(button) == oldName && button.IsEnabled) is { } replacement)
            replacement.Focus(FocusState.Programmatic);
    }

    private static string CatalogString(JsonElement message, string property) =>
        message.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";
    private static bool CatalogBool(JsonElement message, string property) =>
        message.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.True;
    private static int CatalogInt(JsonElement message, string property, int fallback = 0) =>
        message.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var result) ? result : fallback;

    public async void ApplyIconCatalogResponse(JsonElement message)
    {
        if (message.TryGetProperty("id", out var licenseId) && licenseId.TryGetInt64(out var requestId) &&
            CatalogString(message, "action") == "licenses" && iconCatalogLicenseRequests.Remove(requestId))
        {
            if (CatalogString(message, "errorCode").Length > 0) { ShowError(T("settings.iconify_error")); return; }
            var notices = message.TryGetProperty("attributions", out var attributions) && attributions.ValueKind == JsonValueKind.Array
                ? string.Join("\n\n", attributions.EnumerateArray().Select(CatalogNotices)) : "";
            await ShowCatalogNotices(notices);
            return;
        }
        var view = iconCatalog;
        if (view is not null && CatalogBool(message, "prefetch")) { await AcceptCatalogPrefetch(view, message); return; }
        if (view is null || !CatalogActive(view) || !message.TryGetProperty("id", out var idValue) ||
            !idValue.TryGetInt64(out var id) || !view.Requests.Remove(id, out var request)) return;
        // Even an obsolete selection response completes its pending operation.
        // Do not leave the spinner active after discarding its visual result.
        view.Loading = view.Requests.Values.Any(pending => !pending.Background); view.Busy.IsActive = view.Loading;
        view.Busy.Visibility = view.Loading ? Visibility.Visible : Visibility.Collapsed;
        if (!request.Background) RenderCatalogPager(view);
        if (request.SearchGeneration != view.SearchGeneration ||
            CatalogString(message, "deviceKey") != view.DeviceKey ||
            CatalogString(message, "action") != request.Action) return;
        if (request.Action is "details" or "apply" && (request.DetailGeneration != view.DetailGeneration || request.IconId != view.SelectedId)) return;
        try
        {
            if (CatalogString(message, "errorCode").Length > 0)
            {
                if (request.Background) return;
                view.Status.Text = T(CatalogBool(message, "offline") ? "settings.iconify_offline_error" : "settings.iconify_error");
                view.Retry.Visibility = Visibility.Visible; return;
            }
            if (request.Action == "search")
            {
                if (CatalogString(message, "query") != view.Query) return;
                if (CatalogInt(message, "start") != request.Start || request.Start != view.Start) return;
                var context = CatalogString(message, "contextKey");
                var contextChanged = view.ContextKey.Length > 0 && context != view.ContextKey;
                if (contextChanged) { ClearCatalogPages(view); ResetCatalogSelection(view); }
                view.ContextKey = context;
                var page = ReadCatalogPage(view, message);
                if (request.Background && !contextChanged)
                {
                    CacheCatalogPage(view, page);
                    if (page.Total != view.Total) { view.Total = page.Total; RenderCatalogPager(view); }
                }
                else ShowCatalogPage(view, page);
            }
            else if (request.Action == "details" && message.TryGetProperty("detail", out var detail))
            {
                var generation = view.DetailGeneration;
                view.Metadata.Text = string.Join("\n", new[] { CatalogString(detail, "id"), CatalogString(detail, "collection"),
                    T("settings.iconify_author") + ": " + CatalogString(detail, "author"),
                    T("settings.iconify_license") + ": " + CatalogString(detail, "license") }.Where(line => line.Length > 0));
                view.Notices = CatalogNotices(detail);
                var large = await CatalogBitmap(CatalogString(detail, "preview80"));
                var actual = await CatalogBitmap(CatalogString(detail, "previewActual"));
                if (!CatalogActive(view) || generation != view.DetailGeneration || request.SearchGeneration != view.SearchGeneration) return;
                var actualSize = CatalogInt(detail, "actualSize", 16);
                if (large.PixelWidth != 80 || large.PixelHeight != 80 || actualSize is < 8 or > 192 ||
                    actual.PixelWidth != actualSize || actual.PixelHeight != actualSize) throw new InvalidDataException("Invalid catalog preview size");
                view.Preview80.Source = large; view.PreviewActual.Source = actual;
                view.PreviewActual.Width = view.PreviewActual.Height = actualSize / Scale;
                view.Apply.IsEnabled = view.License.IsEnabled = true; view.Status.Text = T("settings.iconify_selected");
            }
            else if (request.Action == "licenses")
            {
                var notices = message.TryGetProperty("attributions", out var entries) && entries.ValueKind == JsonValueKind.Array
                    ? string.Join("\n\n", entries.EnumerateArray().Select(CatalogNotices)) : "";
                await ShowCatalogNotices(notices);
            }
            else if (request.Action is "apply" or "restore")
            {
                view.Status.Text = T(CatalogBool(message, "applied") ? "settings.iconify_saved" : "settings.iconify_error");
                view.Apply.IsEnabled = view.SelectedId.Length > 0;
            }
        }
        catch (Exception error) when (error is JsonException or FormatException or ArgumentException or IOException or System.Runtime.InteropServices.COMException or InvalidOperationException)
        {
            System.Diagnostics.Debug.WriteLine(error);
            if (CatalogActive(view) && !request.Background && request.SearchGeneration == view.SearchGeneration &&
                (request.Action is not ("details" or "apply") ||
                    request.DetailGeneration == view.DetailGeneration && request.IconId == view.SelectedId))
            { view.Status.Text = T("settings.iconify_error"); view.Retry.Visibility = Visibility.Visible; }
        }
    }

    private void RequestAllIconLicenses()
    {
        var id = Bridge.RequestCatalog("licenses", null, size: CatalogPixelSize);
        if (id > 0) iconCatalogLicenseRequests.Add(id);
        else ShowError(T("settings.iconify_error"));
    }

    private static string CatalogNotices(JsonElement detail) =>
        string.Join("\n", new[] { "id", "name", "collection", "author", "license", "licenseUrl", "sourceUrl", "licenseText", "notices" }
            .Select(key => CatalogString(detail, key)).Where(value => value.Length > 0));
    private async Task ShowCatalogNotices(string notices)
    {
        if (closing || root.XamlRoot is null || iconCatalogDialogOpen) return;
        iconCatalogDialogOpen = true;
        var text = Text(notices.Length > 0 ? notices : T("settings.iconify_no_licenses"), 12);
        text.IsTextSelectionEnabled = true;
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = T("settings.iconify_licenses"),
            CloseButtonText = T("settings.close"), Content = new ScrollViewer { Content = text, MaxHeight = 420 },
            RequestedTheme = dark ? ElementTheme.Dark : ElementTheme.Light };
        try { await dialog.ShowAsync(); }
        catch (Exception error) when (error is System.Runtime.InteropServices.COMException or InvalidOperationException) { System.Diagnostics.Debug.WriteLine(error); }
        finally { iconCatalogDialogOpen = false; }
    }
    private static async Task<BitmapImage> CatalogBitmap(string png)
    {
        if (png.Length == 0 || png.Length > 256 * 1024) throw new InvalidDataException("Invalid catalog image");
        var bytes = Convert.FromBase64String(png);
        if (bytes.Length < 24 || !bytes.AsSpan(0, 8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }))
            throw new InvalidDataException("Catalog preview must be PNG");
        var width = System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(bytes.AsSpan(16, 4));
        var height = System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(bytes.AsSpan(20, 4));
        if (width is < 8 or > 192 || height is < 8 or > 192) throw new InvalidDataException("Invalid catalog image dimensions");
        using var stream = new InMemoryRandomAccessStream();
        using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
        {
            writer.WriteBytes(bytes); await writer.StoreAsync(); await writer.FlushAsync(); writer.DetachStream();
        }
        stream.Seek(0); var bitmap = new BitmapImage(); await bitmap.SetSourceAsync(stream); return bitmap;
    }
}
