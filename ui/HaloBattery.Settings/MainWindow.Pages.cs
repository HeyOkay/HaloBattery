using System.Text.Json;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage.Streams;
using Windows.System;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private bool applyingState, pagesInitialized, previewPending, previewDirty = true;
    private readonly List<Action> pageBindings = new();
    private DispatcherTimer? previewTimer;
    private Image? previewImage;
    private InfoBar? pageError;
    private string? previewDeviceKey;
    private string lastError = "";
    private long previewGeneration;

    private bool BoolCfg(string key, bool fallback = false) => State.Settings.TryGetValue(key, out var v) && v.ValueKind is JsonValueKind.True or JsonValueKind.False ? v.GetBoolean() : fallback;
    private int IntCfg(string key, int fallback = 0) => State.Settings.TryGetValue(key, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n) ? n : fallback;
    private string StrCfg(string key, string fallback = "") => State.Settings.TryGetValue(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? fallback : fallback;
    private string ContentMode => BoolCfg("percent_in_icon") ? "percent" : BoolCfg("badges") ? "pictogram" : "ring";
    private DeviceState? SelectedDevice => State.Devices.FirstOrDefault(d => d.Key == selectedDeviceKey);
    private string F(string key, params (string key, object? value)[] values)
    {
        var text = T(key);
        foreach (var (name, value) in values) text = text.Replace("{" + name + "}", Convert.ToString(value, System.Globalization.CultureInfo.CurrentCulture) ?? "");
        return text;
    }
    private void SetGlobal(string key, object? value) => Bridge.SendCommand("setGlobal", new { key, value });
    public void ApplyState(UiState state)
    {
        if (closing) return;
        var old = State;
        var oldSelection = selectedDeviceKey;
        State = state;
        selectedDeviceKey = state.SelectedDeviceKey ?? state.Devices.FirstOrDefault()?.Key;
        previewDirty = true;
        if (!pagesInitialized)
        {
            pagesInitialized = true;
            Closed += (_, _) => { previewTimer?.Stop(); previewGeneration++; pageBindings.Clear(); };
            Navigate(string.IsNullOrEmpty(state.Page) ? "general" : state.Page, false);
            return;
        }
        // Status/charging/progress updates refresh the existing controls. The only
        // structural changes are localization, device identity and provider families.
        var rebuild = old.Language != state.Language ||
            current == "devices" && oldSelection != selectedDeviceKey ||
            current == "advanced" && !old.Providers.Select(p => p.Name).SequenceEqual(state.Providers.Select(p => p.Name));
        if (rebuild) Navigate(current, false);
        else RefreshBindings();
    }

    public void NavigateFromBackend(string page, string? deviceKey)
    {
        if (closing) return;
        if (deviceKey is not null) selectedDeviceKey = deviceKey;
        if (pagesInitialized)
        {
            if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized } presenter) presenter.Restore();
            Activate();
            Navigate(page, true);
        }
    }

    public void ShowError(string message)
    {
        previewPending = false;
        previewDirty = true;
        lastError = F("settings.error", ("error", message));
        if (pageError is not null) { pageError.Message = lastError; pageError.Visibility = Visibility.Visible; pageError.IsOpen = true; }
        RefreshBindings();
    }

    private void RefreshBindings()
    {
        applyingState = true;
        try { foreach (var refresh in pageBindings) refresh(); }
        finally { applyingState = false; }
    }

    private Page BuildPage(string page)
    {
        StopIconCatalog();
        pageBindings.Clear();
        RefreshGeneralFooter(page);
        previewImage = null;
        previewDirty = true;
        previewGeneration++;
        previewTimer?.Stop();
        var content = PageContent(T("settings." + page));
        pageError = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = true, Message = lastError, IsOpen = lastError.Length > 0, Visibility = lastError.Length > 0 ? Visibility.Visible : Visibility.Collapsed };
        pageError.Closed += (_, _) => { lastError = ""; if (pageError is not null) pageError.Visibility = Visibility.Collapsed; };
        content.Children.Add(pageError);
        switch (page)
        {
            case "devices": BuildDevices(content); break;
            case "icons": BuildIcons(content); break;
            case "notifications": BuildNotifications(content); break;
            case "advanced": BuildAdvanced(content); break;
            case "about": BuildAbout(content); break;
            default: BuildGeneral(content); break;
        }
        RefreshBindings();
        if (previewImage is not null) StartPreview();
        return new Page { Content = Scroll(content), Background = Brush("#00000000") };
    }

    private sealed record Choice(JsonElement Value, string Label)
    {
        public override string ToString() => Label;
    }
    private static Choice Pick(object? value, string label) => new(JsonSerializer.SerializeToElement(value), label);
    private static List<Choice> Picks(IEnumerable<UiOption> options) => options.Select(o => new Choice(o.Value, o.Label)).ToList();
    private ComboBox ChoiceBox(string name, Func<List<Choice>> options, Func<object?> value, Action<object?> changed)
    {
        var combo = PillComboBox();
        AutomationProperties.SetName(combo, name);
        string signature = "";
        pageBindings.Add(() =>
        {
            // An open native flyout owns its selection until it closes.
            if (combo.IsDropDownOpen) return;
            var choices = options();
            var next = JsonSerializer.Serialize(choices.Select(c => new { value = c.Value, label = c.Label }));
            if (next != signature)
            {
                combo.Items.Clear();
                foreach (var item in choices) combo.Items.Add(item);
                signature = next;
                FitComboBox(combo);
            }
            var selected = JsonSerializer.SerializeToElement(value()).GetRawText();
            combo.SelectedItem = combo.Items.Cast<Choice>().FirstOrDefault(c => c.Value.GetRawText() == selected);
            combo.IsEnabled = combo.Items.Count > 0;
        });
        combo.SelectionChanged += (_, _) =>
        {
            if (!applyingState && combo.SelectedItem is Choice pick) changed(pick.Value);
        };
        combo.DropDownClosed += (_, _) => RefreshBindings();
        return combo;
    }
    private UIElement ToggleControl(string label, Func<bool> value, Action<bool> changed)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, VerticalAlignment = VerticalAlignment.Center };
        var text = Text("", 13, true);
        var toggle = new SettingToggle(this);
        AutomationProperties.SetName(toggle, label);
        void Changed() { if (!applyingState) changed(toggle.IsChecked == true); }
        toggle.Checked += (_, _) => Changed();
        toggle.Unchecked += (_, _) => Changed();
        pageBindings.Add(() => { toggle.IsChecked = value(); text.Text = T(value() ? "settings.on" : "settings.off"); });
        row.Children.Add(text);
        row.Children.Add(toggle);
        return row;
    }
    private CardView GlobalToggle(StackPanel parent, string key, string? label = null, string hint = "", string icon = "sliders-horizontal")
    {
        var title = T(label ?? "preferences." + key);
        var c = Card(title, hint.Length == 0 ? "" : T(hint), ToggleControl(title, () => BoolCfg(key), v => SetGlobal(key, v)), icon);
        parent.Children.Add(c);
        return c;
    }
    private void GlobalChoice(StackPanel parent, string key, string label, Func<List<Choice>> options, Func<object?> value, string hint = "", string icon = "palette")
        => parent.Children.Add(Card(T(label), hint.Length == 0 ? "" : T(hint), ChoiceBox(T(label), options, value, v => SetGlobal(key, v)), icon));
    private Button ActionButton(string label, string command)
    {
        var button = Button(T(label), true);
        button.Click += (_, _) => Bridge.SendCommand(command, new { });
        return button;
    }
    private void ActionCard(StackPanel parent, string title, string button, string command, string hint = "", string icon = "external-link")
        => parent.Children.Add(Card(T(title), hint, ActionButton(button, command), icon));
    private List<Choice> Thresholds(int? extra = null)
    {
        var result = Picks(State.Options.Thresholds);
        if (extra is { } custom && !result.Any(c => c.Value.ValueKind == JsonValueKind.Number && c.Value.TryGetInt32(out var n) && n == custom))
            result.Add(Pick(custom, custom == 0 ? T("choice.off") : custom + "%"));
        return result;
    }

    private void BuildGeneral(StackPanel p)
    {
        Section(p, T("settings.application"));
        GlobalChoice(p, "language", "preferences.language", () => Picks(State.Options.Languages), () => State.Language, icon: "languages");
        p.Children.Add(Card(T("preferences.autostart"), "", ToggleControl(T("preferences.autostart"), () => State.Autostart, v => SetGlobal("autostart", v)), "circle-arrow-up"));
        Section(p, T("settings.polling"));
        GlobalChoice(p, "interval", "preferences.interval", () => Picks(State.Options.Intervals), () => IntCfg("interval", 60), "settings.polling_hint", "refresh-cw");
        BuildUpdates(p);
    }

    private void BuildDevices(StackPanel p)
    {
        var selector = ChoiceBox(T("settings.select_device"), () => State.Devices.Select(d => Pick(d.Key, d.Name + (d.Hidden ? " - " + T("settings.hidden") : ""))).ToList(), () => selectedDeviceKey,
            value => Bridge.SendCommand("selectDevice", new { deviceKey = value }));
        p.Children.Add(Card(T("settings.select_device"), "", selector, "smartphone-nfc"));
        if (SelectedDevice is { } device)
        {
            // Detached controls can lose focus after a backend selection change.
            // Every callback on this page therefore keeps its original stable key.
            var deviceKey = device.Key;
            void ChangeDevice(string field, object? value) => Bridge.SendCommand("setDevice", new { deviceKey, field, value });
            AddPreview(p, true);
            // The native editor is top-aligned inside its ScrollViewer; leave
            // balanced space around its single line in the 30-DIP field.
            var name = new TextBox { Text = device.Name, MaxLength = 60, Height = 30, MinHeight = 30, Width = 240, MinWidth = 0, FontSize = 13, Padding = new(14, 6, 14, 6), CornerRadius = new(15), BorderThickness = new(0), Background = Brush(dark ? "#303030" : "#ECECEC"), Foreground = TextBrush, VerticalContentAlignment = VerticalAlignment.Center };
            AutomationProperties.SetName(name, T("settings.rename"));
            var committed = device.Name;
            void Commit()
            {
                if (name.Text != committed) { committed = name.Text; ChangeDevice("name", name.Text); }
            }
            name.LostFocus += (_, _) => Commit();
            name.KeyDown += (_, e) =>
            {
                if (e.Key == VirtualKey.Enter) { Commit(); e.Handled = true; }
                else if (e.Key == VirtualKey.Escape) { name.Text = SelectedDevice?.Name ?? committed; committed = name.Text; e.Handled = true; }
            };
            pageBindings.Add(() => { if (name.FocusState == FocusState.Unfocused && SelectedDevice is { } d) { name.Text = committed = d.Name; } });
            p.Children.Add(Card(T("settings.rename"), "", name, "pencil"));
            var reset = Button(T("settings.reset"), true);
            reset.Click += (_, _) => ChangeDevice("reset_name", null);
            p.Children.Add(Card(T("settings.reset_name"), "", reset, "refresh-cw"));
            var visibilityGlyph = device.Hidden ? "eye-closed" : "eye";
            CardView? visibilityCard = null;
            void SetVisibilityGlyph(bool hidden)
            {
                var glyph = hidden ? "eye-closed" : "eye";
                if (glyph == visibilityGlyph) return;
                visibilityCard?.SetIcon(LucideIcon(glyph));
                visibilityGlyph = glyph;
            }
            visibilityCard = Card(T("settings.show_device"), "", ToggleControl(T("settings.show_device"),
                () => SelectedDevice?.Hidden != true, show => { SetVisibilityGlyph(!show); ChangeDevice("hidden", !show); }), visibilityGlyph);
            p.Children.Add(visibilityCard);
            pageBindings.Add(() => SetVisibilityGlyph(SelectedDevice?.Hidden == true));
            p.Children.Add(Card(T("settings.pictogram"), "", ChoiceBox(T("settings.pictogram"), () => Picks(State.Options.Pictograms), () => SelectedDevice?.Pictogram ?? "", v => ChangeDevice("pictogram", v)), "palette"));
            var brand = Card(T("menu.controller_logo"), "", ChoiceBox(T("menu.controller_logo"), () => Picks(State.Options.Brands), () => SelectedDevice?.Brand ?? "default", v => ChangeDevice("brand", v)), "palette");
            var color = Card(T("controller.color"), "", ToggleControl(T("controller.color"), () => SelectedDevice?.BrandColor == true, v => ChangeDevice("brand_color", v)), "palette");
            p.Children.Add(brand); p.Children.Add(color);
            pageBindings.Add(() => { var show = State.Options.Brands.Count > 0 && (SelectedDevice?.Controller == true || SelectedDevice?.Pictogram == "gamepad"); brand.Visibility = color.Visibility = show ? Visibility.Visible : Visibility.Collapsed; });
            p.Children.Add(Card(T("menu.device_low"), "", ChoiceBox(T("menu.device_low"), () =>
            {
                var levels = Thresholds(SelectedDevice?.LowOverride);
                levels.Insert(0, Pick(null, IntCfg("low") == 0 ? T("menu.default_off") : F("menu.default_low", ("level", IntCfg("low")))));
                return levels;
            }, () => SelectedDevice?.LowOverride, v => ChangeDevice("low_override", v)), "bell"));
            BuildIconCatalog(p, deviceKey);
        }
        else p.Children.Add(Card(T("settings.no_devices"), "", icon: "mouse"));
        Section(p, T("settings.compatibility"));
        GlobalToggle(p, "bluetooth", icon: "bluetooth");
        GlobalToggle(p, "keep_disconnected", icon: "eye");
    }

    private void BuildIcons(StackPanel p)
    {
        AddPreview(p, false);
        Section(p, T("settings.content"));
        p.Children.Add(Card(T("settings.content"), "", ChoiceBox(T("settings.content"), () => new() { Pick("pictogram", T("settings.pictogram")), Pick("percent", T("settings.percentage")), Pick("ring", T("settings.ring_only")) }, () => ContentMode,
            mode => Bridge.SendCommand("setIconContent", new { mode })), "circle"));
        GlobalChoice(p, "filled_icons", "preferences.icon_style", () => new() { Pick(false, T("icon_style.outline")), Pick(true, T("icon_style.filled")) }, () => BoolCfg("filled_icons"));
        var fallback = GlobalToggle(p, "badges", "settings.fallback", icon: "mouse");
        pageBindings.Add(() => fallback.Visibility = ContentMode == "percent" ? Visibility.Visible : Visibility.Collapsed);
        GlobalToggle(p, "animation", icon: "loader-circle");
        GlobalToggle(p, "time_left", hint: "settings.estimate_hint", icon: "info");
        Section(p, T("settings.appearance"));
        GlobalChoice(p, "ring_style", "settings.ring_thickness", () => new() { Pick("thin", T("settings.ring_thin")), Pick("classic", T("settings.ring_classic")) }, () => StrCfg("ring_style", "thin"), icon: "circle");
        GlobalChoice(p, "icon_theme", "preferences.theme", () => Picks(State.Options.IconThemes), () => StrCfg("icon_theme", "auto"), icon: "sun-moon");
    }

    private void BuildNotifications(StackPanel p)
    {
        Section(p, T("settings.battery_alerts"));
        GlobalChoice(p, "low", "preferences.low", () => Thresholds(IntCfg("low")), () => IntCfg("low"), "settings.low_hint", "bell");
        GlobalToggle(p, "full_alert", "preferences.full", icon: "battery-full");
        GlobalToggle(p, "low_sound", hint: "settings.sound_hint", icon: "bell");
        Section(p, T("settings.fullscreen"));
        GlobalToggle(p, "quiet_fullscreen", "preferences.quiet", "settings.quiet_hint", "bell-off");
    }

    private void BuildAdvanced(StackPanel p)
    {
        Section(p, T("settings.compatibility"));
        p.Children.Add(Warning(T("settings.experimental"), T("settings.playstation_hint")));
        GlobalToggle(p, "playstation_full_mode", "preferences.playstation", icon: "info");
        Section(p, T("settings.integrations"));
        GlobalToggle(p, "status_file", hint: "settings.status_hint", icon: "folder-open");
        var path = Text(State.StatusPath, 12, true);
        path.FontFamily = MonoFont; path.IsTextSelectionEnabled = true; path.Margin = new(24, 0, 24, 0);
        pageBindings.Add(() => path.Text = State.StatusPath);
        p.Children.Add(path);
        Section(p, T("settings.providers"));
        var providers = Expander(T("settings.providers"), T("settings.providers_hint"), icon: "wrench");
        for (int i = 0; i < State.Providers.Count; i++)
        {
            var providerName = State.Providers[i].Name;
            var provider = State.Providers[i];
            var control = ToggleControl(provider.Label, () => State.Providers.FirstOrDefault(s => s.Name == providerName)?.Enabled == true,
                enabled => Bridge.SendCommand("setProvider", new { name = providerName, enabled }));
            var row = DetailRow(provider.Label, control, i != 0);
            if (row is FrameworkElement element) element.Margin = new(60, 0, 24, 0);
            providers.AddBody(row);
        }
        p.Children.Add(providers);
    }

    private void BuildUpdates(StackPanel p)
    {
        Section(p, T("settings.updates"));
        GlobalToggle(p, "update_check", "preferences.updates", icon: "refresh-cw");
        ActionCard(p, "preferences.check_now", "settings.check", "checkUpdate", icon: "download");
        var install = Button("", true);
        install.Click += (_, _) => Bridge.SendCommand("startUpdate", new { });
        var progress = new ProgressBar { Minimum = 0, Maximum = 100, Height = 4, HorizontalAlignment = HorizontalAlignment.Stretch };
        var update = Card("", "", install, "download");
        update.SetFooter(progress);
        p.Children.Add(update);
        var updateStatus = Text("", 12, true); updateStatus.IsTextSelectionEnabled = true;
        p.Children.Add(updateStatus);
        pageBindings.Add(() =>
        {
            var u = State.Update;
            update.Visibility = string.IsNullOrEmpty(u.Version) ? Visibility.Collapsed : Visibility.Visible;
            var label = F(u.Progress is not null ? "menu.downloading" : u.InstallOk ? "menu.install_update" : "menu.download", ("version", u.Version), ("progress", u.Progress));
            update.SetTitle(Text(label));
            install.Content = label; install.IsEnabled = u.Progress is null;
            progress.Visibility = u.Progress is null ? Visibility.Collapsed : Visibility.Visible;
            progress.Value = Math.Clamp(u.Progress ?? 0, 0, 100);
            updateStatus.Text = u.Message;
            updateStatus.Visibility = string.IsNullOrEmpty(u.Message) ? Visibility.Collapsed : Visibility.Visible;
        });
    }

    private void BuildAbout(StackPanel p)
    {
        var version = Card("HaloBattery", F("settings.version", ("version", State.Version)), icon: "loader-circle");
        p.Children.Add(version);
        pageBindings.Add(() => version.SetDescription(Text(F("settings.version", ("version", State.Version)), 12, true)));
        Section(p, T("settings.support"));
        ActionCard(p, "menu.diagnostics", "settings.open", "diagnostics", icon: "wrench");
        ActionCard(p, "settings.data_folder", "settings.open", "openData", State.DataDir, "folder-open");
        ActionCard(p, "settings.project", "settings.open", "openProject");
        ActionCard(p, "settings.issue", "settings.open", "openIssue");
        p.Children.Add(Card(T("settings.fonts"), "Lucide · ISC / MIT", icon: "info"));
        var licenses = Button(T("settings.iconify_licenses"), true);
        licenses.Click += (_, _) => RequestAllIconLicenses();
        p.Children.Add(Card(T("settings.iconify_licenses"), "", licenses, "info"));
    }

    private void AddPreview(StackPanel p, bool devicePage)
    {
        previewImage = new Image { Stretch = Stretch.Fill, UseLayoutRounding = true, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementVisual(previewImage).IsPixelSnappingEnabled = true;
        var previewGlyph = devicePage ? DeviceKindGlyph(SelectedDevice?.Kind) : "eye";
        var preview = Card(devicePage ? SelectedDevice?.Name ?? "" : T("settings.preview"), "", previewImage, previewGlyph);
        p.Children.Add(preview);
        pageBindings.Add(() =>
        {
            if (previewDeviceKey != selectedDeviceKey) previewDirty = true;
            previewDeviceKey = selectedDeviceKey;
            if (devicePage && SelectedDevice is { } d)
            {
                var glyph = DeviceKindGlyph(d.Kind);
                if (glyph != previewGlyph)
                {
                    preview.SetIcon(LucideIcon(glyph));
                    previewGlyph = glyph;
                }
                preview.SetTitle(Text(d.Name));
                preview.SetDescription(Text(d.Description, 12, true));
            }
            else preview.SetDescription(Text(State.Devices.Count == 0 ? T("settings.preview_unavailable") : T("settings.preview_empty"), 12, true));
            ResizePreview();
        });
    }
    private void ResizePreview()
    {
        if (previewImage is null) return;
        var size = 80 / Scale;
        if (previewImage.Width != size || previewImage.Height != size)
        {
            previewImage.Width = previewImage.Height = size;
            previewDirty = true;
        }
    }
    private void StartPreview()
    {
        if (previewTimer is null)
        {
            previewTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(16) };
            previewTimer.Tick += (_, _) =>
            {
                if (closing || !AppWindow.IsVisible || previewImage is null || current is not ("icons" or "devices") || AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized }) return;
                ResizePreview();
                var device = State.Devices.FirstOrDefault(d => d.Key == previewDeviceKey);
                var animate = BoolCfg("animation") && device is { Charging: true, Online: true };
                if (!previewPending && (previewDirty || animate) && Bridge.RequestPreview(previewDeviceKey))
                {
                    previewPending = true;
                    previewDirty = false;
                }
            };
        }
        previewTimer.Start();
    }
    public async void ApplyPreview(string? deviceKey, string pngBase64)
    {
        if (closing || previewImage is null || deviceKey != previewDeviceKey) { previewPending = false; return; }
        var generation = previewGeneration;
        try
        {
            var bytes = Convert.FromBase64String(pngBase64);
            using var stream = new InMemoryRandomAccessStream();
            using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
            {
                writer.WriteBytes(bytes); await writer.StoreAsync(); await writer.FlushAsync(); writer.DetachStream();
            }
            stream.Seek(0);
            var bitmap = new BitmapImage();
            await bitmap.SetSourceAsync(stream);
            if (generation != previewGeneration || deviceKey != previewDeviceKey || closing || bitmap.PixelWidth != 80 || bitmap.PixelHeight != 80) return;
            if (previewImage is not null) { ResizePreview(); previewImage.Source = bitmap; }
        }
        catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
        finally { previewPending = false; }
    }
}
