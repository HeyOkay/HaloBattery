using System.Diagnostics;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml.Automation;
using HaloBattery.Settings.Controls;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    internal void StartFixtureVerification(string fixturePath)
    {
        var output = Arg("--verify-dir");
        if (output.Length == 0) return;
        if (Arg("--fixture-state").Length == 0)
            throw new InvalidDataException("Verification requires fixture mode.");
        var parent = Path.GetDirectoryName(Path.GetFullPath(fixturePath))!;
        var destination = Path.GetFullPath(output);
        var prefix = parent.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Verification output must be inside the fixture directory.");
        if (!DispatcherQueue.TryEnqueue(async () =>
        {
            var snapshots = new List<object>();
            var checks = new List<object>();
            var intervals = new List<double>();
            var clock = Stopwatch.StartNew();
            double previous = -1;
            EventHandler<object> rendering = (_, _) =>
            {
                var now = clock.Elapsed.TotalMilliseconds;
                if (previous >= 0) intervals.Add(now - previous);
                previous = now;
            };
            try
            {
                // Attach is called before Activate; let the real XAML tree load.
                for (var i = 0; !root.IsLoaded && i < 40; i++) await Task.Delay(50);
                if (!root.IsLoaded) throw new TimeoutException("Fixture root did not load.");
                await Task.Delay(600);
                Directory.CreateDirectory(destination);
                CompositionTarget.Rendering += rendering;
                foreach (var page in State.Pages.Select(item => item.Id).ToArray())
                {
                    Navigate(page, true);
                    await Task.Delay(350);
                    root.UpdateLayout();
                    snapshots.Add(VerificationSnapshot("normal-expanded-" + page));
                    await VerificationCapture(destination, "normal-expanded-" + page);
                }
                ToggleRail();
                await Task.Delay(350);
                snapshots.Add(VerificationSnapshot("normal-collapsed"));
                await VerificationCapture(destination, "normal-collapsed");
                if (AppWindow.Presenter is OverlappedPresenter presenter)
                {
                    presenter.Maximize();
                    await Task.Delay(500);
                    root.UpdateLayout();
                    snapshots.Add(VerificationSnapshot("maximized-collapsed"));
                    await VerificationCapture(destination, "maximized-collapsed");
                    ToggleRail();
                    await Task.Delay(350);
                    snapshots.Add(VerificationSnapshot("maximized-expanded"));
                    await VerificationCapture(destination, "maximized-expanded");
                }
                await VerifyFixtureInteractions(checks);
                await VerifyIconCatalog(checks);
                await VerifyToggleThumbShadow(destination, checks);
                await VerifyRingThicknessChoice(checks);
                await VerifyRequestedSettingGlyphs(destination, checks);
                await VerifyFixturePopup(destination, checks);
                await VerifyWheelNavigation(checks);
                await VerifyNavigationAndHeader(checks);
                await VerifyPageLandings(destination, "before-overscroll", checks);
                await VerifyOverscrollNavigation(destination, checks);
                await VerifyPageLandings(destination, "after-overscroll", checks);
                if (AppWindow.Presenter is OverlappedPresenter landingPresenter)
                {
                    landingPresenter.Maximize();
                    await Task.Delay(350);
                    await VerifyPageLandings(destination, "maximized-after-overscroll", checks);
                    landingPresenter.Restore();
                    await Task.Delay(350);
                }
                await VerifyGeneralFooter(checks);
                await VerifyMinimumWindow(destination, snapshots, checks);
                WriteVerificationReport(destination, new
                {
                    complete = true,
                    theme = dark ? "dark" : "light",
                    elapsedMs = clock.Elapsed.TotalMilliseconds,
                    captureKind = "RenderTargetBitmap of XAML root only; Mica, Composition ring, elastic translation and compositor effects are not captured",
                    intervalKind = "CompositionTarget.Rendering callback intervals during navigation, capture and resizing; not presented FPS",
                    intervalCount = intervals.Count,
                    intervalMeanMs = intervals.Count > 0 ? (double?)intervals.Average() : null,
                    intervalMaxMs = intervals.Count > 0 ? (double?)intervals.Max() : null,
                    intervalsMs = intervals,
                    fixtureCommands = Bridge.FixtureCommands,
                    checks,
                    expectedReference = new { initialWindowDip = new[] { 1280, 900 }, cardWidth = 800, contentWidth = 888, pagePadding = 44, expandedRail = 228, collapsedRail = 64, header = 56, pageRadius = 12, cardRadius = 20, fieldHeight = 30, toggleWidth = 40, toggleHeight = 22 },
                    snapshots
                });
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                Directory.CreateDirectory(destination);
                WriteVerificationReport(destination, new { complete = false, error = error.ToString(), checks, fixtureCommands = Bridge.FixtureCommands, snapshots });
            }
            finally { CompositionTarget.Rendering -= rendering; Close(); }
        })) throw new InvalidOperationException("Unable to enqueue fixture verification.");
    }

    // These actions run only after --fixture-state was validated above. The
    // bridge records envelopes in memory; no command reaches the Python host.
    private async Task VerifyFixtureInteractions(List<object> checks)
    {
        void Check(string name, bool passed, object? actual, object? expected)
        {
            checks.Add(new { name, passed, actual, expected });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        void Command(string name, int before, object expectedArgs)
        {
            Check(name + ": command count", Bridge.FixtureCommands.Count == before + 1, Bridge.FixtureCommands.Count - before, 1);
            using var json = JsonDocument.Parse(Bridge.FixtureCommands[before]);
            var command = json.RootElement;
            Check(name + ": envelope", command.GetProperty("type").GetString() == "command" && command.GetProperty("name").GetString() == name,
                new { type = command.GetProperty("type").GetString(), name = command.GetProperty("name").GetString() }, new { type = "command", name });
            var actual = command.GetProperty("args");
            var expected = JsonSerializer.SerializeToElement(expectedArgs, SettingsBridge.Json);
            var equal = actual.EnumerateObject().Count() == expected.EnumerateObject().Count() &&
                expected.EnumerateObject().All(p => actual.TryGetProperty(p.Name, out var v) && v.GetRawText() == p.Value.GetRawText());
            Check(name + ": arguments", equal, actual.Clone(), expected);
        }
        TControl Find<TControl>(string label) where TControl : FrameworkElement =>
            VerificationDescendants(frame).OfType<TControl>().Single(control => AutomationProperties.GetName(control) == label);
        async Task Go(string page)
        {
            Navigate(page, false);
            await Task.Delay(50);
            root.UpdateLayout();
        }
        // XAML rounds layout to physical pixels. At 125%, a requested 30 DIP
        // field becomes 37 or 38 pixels (29.6 or 30.4 DIP), while Height stays 30.
        bool RoundedSize(double actualDip, double requestedDip) =>
            actualDip * Scale >= Math.Floor(requestedDip * Scale) - .01 &&
            actualDip * Scale <= Math.Ceiling(requestedDip * Scale) + .01;
        object ExpectedSize(double requestedDip) => new
        {
            requestedDip,
            physicalUnrounded = requestedDip * Scale,
            physicalMinimum = Math.Floor(requestedDip * Scale),
            physicalMaximum = Math.Ceiling(requestedDip * Scale),
            scale = Scale
        };
        void Field(string name, FrameworkElement field)
        {
            Check(name + ": field height", field.Height == 30 && RoundedSize(field.ActualHeight, 30),
                new { requestedDip = field.Height, actualDip = field.ActualHeight, actualPixels = field.ActualHeight * Scale }, ExpectedSize(30));
        }
        void ToggleSize(string name, SettingToggle toggle)
        {
            Check(name + ": toggle geometry", toggle.Width == 40 && toggle.Height == 22 && RoundedSize(toggle.ActualWidth, 40) && RoundedSize(toggle.ActualHeight, 22),
                new { requestedDip = new[] { toggle.Width, toggle.Height }, actualDip = new[] { toggle.ActualWidth, toggle.ActualHeight }, actualPixels = new[] { toggle.ActualWidth * Scale, toggle.ActualHeight * Scale } },
                new { width = ExpectedSize(40), height = ExpectedSize(22) });
        }

        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.Minimize();
            await Task.Delay(100);
            NavigateFromBackend("general", selectedDeviceKey);
            await Task.Delay(100);
            Check("backend navigation restores minimized window", presenter.State != OverlappedPresenterState.Minimized && AppWindow.IsVisible,
                new { state = presenter.State.ToString(), visible = AppWindow.IsVisible }, new { minimized = false, visible = true });
        }
        await Go("general");
        var visibleCards = cards.Where(c => c.Visibility == Visibility.Visible).ToArray();
        Check("card widths", CardWidth == 800 && visibleCards.Length > 0 && visibleCards.All(c => RoundedSize(c.ActualWidth, 800)),
            new { configuredDip = CardWidth, actualDip = visibleCards.Select(c => c.ActualWidth).ToArray(), actualPixels = visibleCards.Select(c => c.ActualWidth * Scale).ToArray() }, ExpectedSize(800));
        Check("header height", header.Height == 56 && RoundedSize(header.ActualHeight, 56),
            new { requestedDip = header.Height, actualDip = header.ActualHeight, actualPixels = header.ActualHeight * Scale }, ExpectedSize(56));
        var micaSupported = Environment.OSVersion.Version.Build >= 22000 && Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported();
        Check("Mica backdrop when supported", !micaSupported || SystemBackdrop is MicaBackdrop, new { supported = micaSupported, backdrop = SystemBackdrop?.GetType().Name }, "MicaBackdrop when supported");

        var language = Find<ComboBox>(T("preferences.language"));
        Field("language", language);
        var nextLanguage = language.Items.Cast<Choice>().First(item => !ReferenceEquals(item, language.SelectedItem));
        var before = Bridge.FixtureCommands.Count;
        language.SelectedItem = nextLanguage;
        Command("setGlobal", before, new { key = "language", value = nextLanguage.Value });

        var autostart = Find<SettingToggle>(T("preferences.autostart"));
        ToggleSize("autostart", autostart);
        var autostartValue = autostart.IsChecked != true;
        before = Bridge.FixtureCommands.Count;
        autostart.IsChecked = autostartValue;
        Command("setGlobal", before, new { key = "autostart", value = autostartValue });

        await Go("devices");
        var selector = Find<ComboBox>(T("settings.select_device"));
        Field("device selector", selector);
        var otherDevice = State.Devices.FirstOrDefault(d => d.Key != selectedDeviceKey && d.Pictogram == "keyboard") ?? State.Devices.First(d => d.Key != selectedDeviceKey);
        var nextDevice = selector.Items.Cast<Choice>().Single(item => item.Value.GetString() == otherDevice.Key);
        before = Bridge.FixtureCommands.Count;
        selector.SelectedItem = nextDevice;
        Command("selectDevice", before, new { deviceKey = otherDevice.Key });

        // A fixture bridge has no backend. Apply the authoritative response that
        // a real host would emit, without editing or persisting configuration.
        var response = JsonSerializer.Deserialize<UiState>(JsonSerializer.Serialize(State, SettingsBridge.Json), SettingsBridge.Json)!;
        response.SelectedDeviceKey = otherDevice.Key;
        response.Revision++;
        ApplyState(response);
        await Task.Delay(50);
        root.UpdateLayout();
        var show = Find<SettingToggle>(T("settings.show_device"));
        ToggleSize("device visibility", show);
        var showValue = show.IsChecked != true;
        before = Bridge.FixtureCommands.Count;
        show.IsChecked = showValue;
        Command("setDevice", before, new { deviceKey = otherDevice.Key, field = "hidden", value = !showValue });

        await Go("icons");
        var content = Find<ComboBox>(T("settings.content"));
        Field("icon content", content);
        var nextContent = content.Items.Cast<Choice>().First(item => !ReferenceEquals(item, content.SelectedItem));
        before = Bridge.FixtureCommands.Count;
        content.SelectedItem = nextContent;
        Command("setIconContent", before, new { mode = nextContent.Value });

        await Go("advanced");
        cards.Single(c => c.Expandable).IsExpanded = true;
        root.UpdateLayout();
        var provider = State.Providers.First();
        var providerToggle = Find<SettingToggle>(provider.Label);
        ToggleSize("provider", providerToggle);
        var enabled = providerToggle.IsChecked != true;
        before = Bridge.FixtureCommands.Count;
        providerToggle.IsChecked = enabled;
        Command("setProvider", before, new { name = provider.Name, enabled });
    }

    private async Task VerifyFixturePopup(string destination, List<object> checks)
    {
        Navigate("general", false);
        await Task.Delay(100);
        root.UpdateLayout();
        var language = VerificationDescendants(frame).OfType<ComboBox>().Single(control => AutomationProperties.GetName(control) == T("preferences.language"));
        var commandsBefore = Bridge.FixtureCommands.Count;
        try
        {
            language.IsDropDownOpen = true;
            await Task.Delay(100);
            var popup = VisualTreeHelper.GetOpenPopupsForXamlRoot(root.XamlRoot)
                .FirstOrDefault(item => item.Child is not null && VerificationDescendants(item.Child).OfType<Border>().Any(border => border.Name == "PopupBorder"));
            var border = popup?.Child is { } child ? VerificationDescendants(child).OfType<Border>().First(item => item.Name == "PopupBorder") : null;
            var color = (border?.Background as SolidColorBrush)?.Color;
            var opaque = color?.A == 255;
            checks.Add(new { name = "language popup actual template background", passed = opaque, actual = new { color = color?.ToString(), alpha = color?.A }, expected = new { alpha = 255 } });
            if (!opaque) throw new InvalidOperationException("Language PopupBorder background is missing or translucent.");
            var selectedItems = VerificationDescendants(popup!.Child).OfType<ComboBoxItem>().Where(item => item.IsSelected).Select(item => new
            {
                name = item.Name,
                foreground = (item.Foreground as SolidColorBrush)?.Color.ToString(),
                foregroundAlpha = (item.Foreground as SolidColorBrush)?.Color.A,
                backgrounds = VerificationDescendants(item).OfType<FrameworkElement>().Where(element => element is Grid or Border).Select(element =>
                {
                    var background = element is Border itemBorder ? itemBorder.Background : ((Grid)element).Background;
                    var backgroundColor = (background as SolidColorBrush)?.Color;
                    return new { name = element.Name, type = element.GetType().Name, brushType = background?.GetType().Name, color = backgroundColor?.ToString(), alpha = backgroundColor?.A, opacity = element.Opacity, visibility = element.Visibility.ToString() };
                }).ToArray(),
                visualStates = VerificationDescendants(item).OfType<FrameworkElement>().SelectMany(element =>
                    VisualStateManager.GetVisualStateGroups(element).Select(group => new { element = element.Name, group = group.Name, state = group.CurrentState?.Name })).ToArray()
            }).ToArray();
            // Record live template values separately from bitmap pixels. Selection
            // backgrounds can be composed from several layers, so do not infer an
            // accent failure from a single layer or the captured image.
            checks.Add(new { name = "language popup selected item template diagnostics", diagnostic = true, actual = selectedItems, expected = new { accent = AccentHex } });
            var expectedSelectedColor = Brush(AccentHex).Color.ToString();
            var selectedRootColors = selectedItems.SelectMany(item => item.backgrounds.Where(background => background.name == "LayoutRoot")).Select(background => background.color).ToArray();
            var selectedColorCorrect = selectedItems.Length == 1 && selectedRootColors.Length == 1 && selectedRootColors[0] == expectedSelectedColor;
            checks.Add(new { name = "language popup selected background uses exact accent", passed = selectedColorCorrect, actual = new { selectedCount = selectedItems.Length, colors = selectedRootColors }, expected = new { selectedCount = 1, color = expectedSelectedColor } });
            if (!selectedColorCorrect) throw new InvalidOperationException("Native selected ComboBoxItem LayoutRoot background does not match the accent.");
            await VerificationCapture(destination, "language-popup", popup!.Child);
        }
        finally { language.IsDropDownOpen = false; }
        var noCommand = Bridge.FixtureCommands.Count == commandsBefore;
        checks.Add(new { name = "opening language popup sends no command", passed = noCommand, actual = Bridge.FixtureCommands.Count - commandsBefore, expected = 0 });
        if (!noCommand) throw new InvalidOperationException("Opening the language popup emitted a command.");
    }

    private async Task VerifyNavigationAndHeader(List<object> checks)
    {
        void Check(string name, bool passed, object? actual, object? expected)
        {
            checks.Add(new { name, passed, actual, expected });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var tolerance = 1 / Scale;
        double VisualY() => navigationHighlight.TransformToVisual(navigationHost).TransformPoint(new Windows.Foundation.Point()).Y;
        var initialPoint = navigationHighlight.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point());
        var width = navigationHighlight.ActualWidth;
        var height = navigationHighlight.ActualHeight;
        var opacity = navigationHighlight.Opacity;
        var samples = new List<object>();
        // Exercise the fresh-selection branch explicitly, before it could settle
        // after an incorrect startup animation and conceal a regression.
        navigationSelectionId = null;
        SetNavigationSelection("general", true);
        Check("navigation first selection is immediate", NavigationHighlightY == 0 && !NavigationHighlightAnimating,
            new { y = NavigationHighlightY, animating = NavigationHighlightAnimating }, new { y = 0, animating = false });
        SetNavigationSelection("general", false);
        SetNavigationSelection("about", true);
        if (AnimationsEnabled)
        {
            var initialMotion = navigationAnimation;
            var animation = initialMotion?.Children.OfType<Microsoft.UI.Xaml.Media.Animation.DoubleAnimation>().SingleOrDefault();
            Check("navigation uses 220ms cubic ease in out Y only", initialMotion?.Children.Count == 1 && animation is not null &&
                animation.Duration.TimeSpan.TotalMilliseconds == 220 &&
                animation.EasingFunction is Microsoft.UI.Xaml.Media.Animation.CubicEase { EasingMode: Microsoft.UI.Xaml.Media.Animation.EasingMode.EaseInOut } &&
                Microsoft.UI.Xaml.Media.Animation.Storyboard.GetTargetProperty(animation) == "Y",
                new { durationMs = animation is not null ? (double?)animation.Duration.TimeSpan.TotalMilliseconds : null, easing = animation?.EasingFunction?.GetType().Name },
                new { durationMs = 220, easing = "CubicEase EaseInOut", property = "Y", animationCount = 1 });
            await Task.Delay(85);
            var movingY = NavigationHighlightY;
            var aboutTarget = NavigationHighlightTargetY;
            Check("navigation has an intermediate visible position", movingY > 0 && movingY < aboutTarget && Math.Abs(movingY - VisualY()) <= tolerance,
                new { y = movingY, visualY = VisualY(), target = aboutTarget }, new { strictlyBetween = new[] { 0.0, aboutTarget }, tolerance });
            SetNavigationSelection("devices", true);
            var retargetY = NavigationHighlightY;
            var target = NavigationHighlightTargetY;
            var retargetMotion = navigationAnimation;
            Check("navigation retarget begins at visible position", Math.Abs(retargetY - movingY) <= tolerance && !ReferenceEquals(initialMotion, retargetMotion),
                new { before = movingY, after = retargetY, target }, new { maximumJumpDip = tolerance, replacedClock = true });
            SetNavigationSelection("devices", true);
            Check("navigation reselect preserves active clock", ReferenceEquals(retargetMotion, navigationAnimation), ReferenceEquals(retargetMotion, navigationAnimation), true);
            var previousY = retargetY;
            var lower = Math.Min(retargetY, target);
            var upper = Math.Max(retargetY, target);
            var increasing = target >= retargetY;
            var monotonic = true;
            var geometryStable = true;
            var coordinatesAgree = true;
            var elapsed = Stopwatch.StartNew();
            for (var i = 0; i < 8; i++)
            {
                await Task.Delay(35);
                var y = NavigationHighlightY;
                var visualY = VisualY();
                var point = navigationHighlight.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point());
                monotonic &= y >= lower - tolerance && y <= upper + tolerance && (increasing ? y >= previousY - tolerance : y <= previousY + tolerance);
                coordinatesAgree &= Math.Abs(y - visualY) <= tolerance;
                geometryStable &= Math.Abs(point.X - initialPoint.X) <= tolerance && Math.Abs(navigationHighlight.ActualWidth - width) <= tolerance &&
                    Math.Abs(navigationHighlight.ActualHeight - height) <= tolerance && navigationHighlight.Opacity == opacity;
                samples.Add(new { elapsedMs = elapsed.Elapsed.TotalMilliseconds, y, visualY, x = point.X, width = navigationHighlight.ActualWidth, height = navigationHighlight.ActualHeight, opacity = navigationHighlight.Opacity, animating = NavigationHighlightAnimating });
                previousY = y;
            }
            Check("navigation retarget is monotonic without overshoot", monotonic && coordinatesAgree, samples, new { lower, upper, tolerance, coordinatesAgree = true });
            Check("navigation preserves X size and opacity", geometryStable, samples, new { x = initialPoint.X, width, height, opacity, tolerance });
            Check("navigation retarget settles and releases clock", Math.Abs(NavigationHighlightY - 44) <= tolerance && !NavigationHighlightAnimating,
                new { y = NavigationHighlightY, target, animating = NavigationHighlightAnimating }, new { y = 44, animating = false });
        }
        else
        {
            Check("navigation respects disabled animations", NavigationHighlightY == NavigationHighlightTargetY && !NavigationHighlightAnimating,
                new { y = NavigationHighlightY, target = NavigationHighlightTargetY, animating = NavigationHighlightAnimating }, new { immediate = true });
        }
        SetNavigationSelection(current, false);

        if (AppWindow.Presenter is not OverlappedPresenter presenter) return;
        presenter.Restore();
        await Task.Delay(350);
        root.UpdateLayout();
        var originalPage = frame.Content;
        var originalScroll = pageHosts.Single().scroll;
        var originalOffset = originalScroll.VerticalOffset;
        var originalButton = maximizeButton;
        void HeaderState(bool maximized)
        {
            root.UpdateLayout();
            var expectedTag = maximized ? "restore" : "maximize";
            var tag = (maximizeButton?.Content as FrameworkElement)?.Tag as string;
            Check("header " + expectedTag + " updates without rebuild", maximizeButton is not null && ReferenceEquals(originalButton, maximizeButton) &&
                tag == expectedTag && AutomationProperties.GetName(maximizeButton) == T(maximized ? "settings.restore" : "settings.maximize") &&
                ReferenceEquals(originalPage, frame.Content) && ReferenceEquals(originalScroll, pageHosts.Single().scroll) && Math.Abs(originalScroll.VerticalOffset - originalOffset) <= tolerance,
                new { tag, label = maximizeButton is null ? null : AutomationProperties.GetName(maximizeButton), sameButton = ReferenceEquals(originalButton, maximizeButton), samePage = ReferenceEquals(originalPage, frame.Content), scrollOffset = originalScroll.VerticalOffset },
                new { tag = expectedTag, label = T(maximized ? "settings.restore" : "settings.maximize"), samePage = true, scrollOffset = originalOffset });
            var buttons = VerificationDescendants(header).OfType<Button>().ToArray();
            Check("header " + expectedTag + " hit targets remain 40 square", buttons.Length == 4 && buttons.All(b => b.Width == 40 && b.Height == 40 && Math.Abs(b.ActualWidth - 40) * Scale <= 1.01 && Math.Abs(b.ActualHeight - 40) * Scale <= 1.01),
                buttons.Select(b => new { requestedWidth = b.Width, requestedHeight = b.Height, width = b.ActualWidth, height = b.ActualHeight }).ToArray(), new { count = 4, sizeDip = 40, tolerancePixels = 1 });
            var brand = pill.Child as FrameworkElement;
            var bounds = brand?.TransformToVisual(header).TransformBounds(new Windows.Foundation.Rect(0, 0, brand.ActualWidth, brand.ActualHeight));
            Check("header " + expectedTag + " brand is centered", bounds is { } rect && Math.Abs(rect.X + rect.Width / 2 - header.ActualWidth / 2) * Scale <= 1.01 && Math.Abs(rect.Y + rect.Height / 2 - header.ActualHeight / 2) * Scale <= 1.01,
                new { bounds = bounds is { } actualBounds ? new { x = actualBounds.X, y = actualBounds.Y, width = actualBounds.Width, height = actualBounds.Height } : null, headerWidth = header.ActualWidth, headerHeight = header.ActualHeight, scale = Scale }, new { tolerancePixels = 1, horizontal = "full header center", vertical = "header center" });
        }
        presenter.Maximize();
        await Task.Delay(350);
        HeaderState(true);
        presenter.Restore();
        await Task.Delay(350);
        HeaderState(false);
    }

    [DllImport("user32.dll", EntryPoint = "PostMessageW", ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostFixtureWheel(nint window, uint message, nuint data, nint position);

    private async Task VerifyWheelNavigation(List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("general", false);
        int remainder = 0;
        Check("wheel accumulates a partial notch", !AdvanceWheelPage(-60, ref remainder) && current == "general");
        Check("wheel advances after one complete notch", AdvanceWheelPage(-60, ref remainder) && current == "devices");
        if (AnimationsEnabled)
        {
            Check("page entry blocks wheel immediately including its load wait", PageEntryBlocksWheel &&
                !AdvanceWheelPage(60, ref remainder) && remainder == 0 && current == "devices");
            for (var i = 0; pageEntry is null && i < 50; i++) await Task.Delay(10);
            var entry = pageEntry;
            Check("page entry has a real storyboard and native wheel filter", entry is not null && pageEntryWheelHook != 0);
            entry!.Pause();
            remainder = -60;
            Check("entry discards partial navigation and overscroll deltas", !AdvanceWheelPage(-60, ref remainder) &&
                remainder == 0 && HandleContentOverscroll(-OverscrollGesture.Threshold, 0, 0) && OverscrollState.Raw == 0 && pendingOverscrollNavigation is null);
            var scroll = pageHosts.Single().scroll;
            var offset = scroll.VerticalOffset;
            var dropped = discardedEntryWheelMessages;
            navigationWheelRemainder = 60;
            // Only post ordinary wheel input to this fixture's own window.
            // Windows rejects fabricated WM_POINTER* packets without pointer state.
            Check("fixture posts vertical and horizontal wheel messages", PostFixtureWheel(hwnd, 0x020A, 120u << 16, 0) &&
                PostFixtureWheel(hwnd, 0x020E, 120u << 16, 0));
            await Task.Delay(350);
            Check("paused entry keeps wheel blocked beyond nominal duration", PageEntryBlocksWheel && ReferenceEquals(pageEntry, entry) && current == "devices");
            Check("native queued wheel is discarded without scrolling or accumulating", discardedEntryWheelMessages >= dropped + 2 &&
                navigationWheelRemainder == 0 && OverscrollState.Raw == 0 && Math.Abs(scroll.VerticalOffset - offset) <= 1 / Scale);
            scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
            await Task.Delay(50);
            Check("programmatic end positioning works during entry", PageEntryBlocksWheel &&
                Math.Abs(scroll.VerticalOffset - scroll.ScrollableHeight) <= 1 / Scale);
            entry.Resume();
            for (var i = 0; PageEntryBlocksWheel && i < 50; i++) await Task.Delay(20);
            Check("entry completion restores wheel without replay", !PageEntryBlocksWheel && current == "devices" && navigationWheelRemainder == 0 && OverscrollState.Raw == 0);
            Check("post-entry wheel starts with a fresh partial notch", !AdvanceWheelPage(-60, ref remainder) && remainder == -60 && current == "devices");
        }
        else Check("disabled page animations do not block wheel", !PageEntryBlocksWheel);
        Check("wheel navigates backward", AdvanceWheelPage(120, ref remainder) && current == "general");
        for (var i = 0; PageEntryBlocksWheel && i < 50; i++) await Task.Delay(20);
        Check("wheel clamps at first page", !AdvanceWheelPage(120, ref remainder) && current == "general");
        SetNavigationSelection(current, false);
    }

    private async Task VerifyMinimumWindow(string destination, List<object> snapshots, List<object> checks)
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
        await Task.Delay(350);
        var normalWidth = root.ActualWidth;
        var normalHeightPixels = AppWindow.Size.Height;
        var minimumWidthPixels = (int)Math.Ceiling(MinimumWindowWidthDip * Scale);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(minimumWidthPixels, normalHeightPixels));
        await Task.Delay(500);
        root.UpdateLayout();
        void Check(string name, bool passed, object actual, object expected)
        {
            checks.Add(new { name, passed, actual, expected });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Check("minimum window shrinks root", root.ActualWidth < normalWidth && Math.Abs(AppWindow.Size.Width - minimumWidthPixels) <= 1,
            new { normalWidth, minimumWidth = root.ActualWidth, ratio = root.ActualWidth / normalWidth, windowPixels = AppWindow.Size.Width },
            new { windowPixels = minimumWidthPixels, shrinks = true });
        if (!expanded) ToggleRail();
        async Task Capture(string page, string name)
        {
            Navigate(page, false);
            await Task.Delay(350);
            root.UpdateLayout();
            var frameBounds = frame.TransformToVisual(root).TransformBounds(new Windows.Foundation.Rect(0, 0, frame.ActualWidth, frame.ActualHeight));
            var tolerance = 2 / Scale;
            // Scroll content may legitimately extend vertically beyond the viewport.
            // Inspect page/card horizontal extents, avoiding hidden template nodes.
            var visibleCards = cards.Where(c => c.Visibility == Visibility.Visible && c.ActualWidth > 0).ToArray();
            var bounds = pageHosts.Select(p => (FrameworkElement)p.content).Concat(visibleCards).Select(element =>
            {
                var rect = element.TransformToVisual(root).TransformBounds(new Windows.Foundation.Rect(0, 0, element.ActualWidth, element.ActualHeight));
                return new { type = element.GetType().Name, left = rect.Left, right = rect.Right, width = rect.Width };
            }).ToArray();
            Check(name + ": page and cards fit frame", bounds.All(b => b.left >= frameBounds.Left - tolerance && b.right <= frameBounds.Right + tolerance),
                bounds, new { left = frameBounds.Left, right = frameBounds.Right, tolerance });
            snapshots.Add(VerificationSnapshot(name));
            await VerificationCapture(destination, name);
        }
        foreach (var page in new[] { "general", "devices", "icons", "advanced" })
            await Capture(page, "minimum-expanded-" + page);
        ToggleRail();
        await Capture("devices", "minimum-collapsed-devices");
    }

    private static IEnumerable<DependencyObject> VerificationDescendants(DependencyObject node)
    {
        yield return node;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
            foreach (var child in VerificationDescendants(VisualTreeHelper.GetChild(node, i))) yield return child;
    }

    private static void WriteVerificationReport(string directory, object report) =>
        File.WriteAllText(Path.Combine(directory, "qa.json"), JsonSerializer.Serialize(report,
            new JsonSerializerOptions { WriteIndented = true }), new System.Text.UTF8Encoding(false));

    private object VerificationSnapshot(string name)
    {
        var elements = new List<object>();
        void Visit(DependencyObject node, string path)
        {
            if (node is FrameworkElement element)
            {
                var point = element.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point(0, 0));
                var radius = node is Border border ? border.CornerRadius : node is Control control ? control.CornerRadius : new CornerRadius();
                var text = node is TextBlock block ? block.Text : node is ContentControl content ? content.Content as string : null;
                var font = node is TextBlock tb ? tb.FontFamily.Source : node is Control c ? c.FontFamily.Source : null;
                var fontSize = node is TextBlock t ? t.FontSize : node is Control ctl ? (double?)ctl.FontSize : null;
                elements.Add(new { path, type = node.GetType().Name, name = element.Name, text, font, fontSize,
                    x = point.X, y = point.Y, width = element.ActualWidth, height = element.ActualHeight,
                    radius = new[] { radius.TopLeft, radius.TopRight, radius.BottomRight, radius.BottomLeft },
                    automationName = AutomationProperties.GetName(element),
                    opacity = element.Opacity, visibility = element.Visibility.ToString() });
            }
            for (var i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
                Visit(VisualTreeHelper.GetChild(node, i), path + "/" + i);
        }
        Visit(root, "root");
        var brand = pill.Child as FrameworkElement;
        var brandBounds = brand?.TransformToVisual(root).TransformBounds(new Windows.Foundation.Rect(0, 0, brand.ActualWidth, brand.ActualHeight));
        return new { name, page = current, rootWidth = root.ActualWidth, rootHeight = root.ActualHeight,
            scale = Scale, windowPixels = new[] { AppWindow.Size.Width, AppWindow.Size.Height },
            backdrop = SystemBackdrop?.GetType().Name, shadow = card.Shadow?.GetType().Name,
            shadowZ = card.Translation.Z, railWidth = rail.Width.Value, headerHeight = header.ActualHeight,
            configuredContentWidth = ContentWidth, configuredCardWidth = CardWidth,
            brandBounds = brandBounds is { } brandRect ? new { x = brandRect.X, y = brandRect.Y, width = brandRect.Width, height = brandRect.Height, centerX = brandRect.X + brandRect.Width / 2, centerY = brandRect.Y + brandRect.Height / 2 } : null,
            presenterState = (AppWindow.Presenter as OverlappedPresenter)?.State.ToString(),
            maximizeTag = (maximizeButton?.Content as FrameworkElement)?.Tag as string,
            navigationHighlight = new { y = NavigationHighlightY, targetY = NavigationHighlightTargetY, animating = NavigationHighlightAnimating },
            headerButtons = VerificationDescendants(header).OfType<Button>().Select(button => new
            {
                automationName = AutomationProperties.GetName(button), width = button.ActualWidth, height = button.ActualHeight,
                radius = button.CornerRadius.TopLeft,
                x = button.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point()).X,
                margin = new[] { button.Margin.Left, button.Margin.Top, button.Margin.Right, button.Margin.Bottom }
            }).ToArray(),
            brandRuns = VerificationDescendants(pill).OfType<TextBlock>().SelectMany(block => block.Inlines.OfType<Microsoft.UI.Xaml.Documents.Run>()).Select(run => new
            {
                text = run.Text, color = (run.Foreground as SolidColorBrush)?.Color.ToString()
            }).ToArray(),
            pillBackgroundAlpha = (pill.Background as SolidColorBrush)?.Color.A,
            pagePadding = pageHosts.Select(host => new[] { host.content.Padding.Left, host.content.Padding.Top, host.content.Padding.Right, host.content.Padding.Bottom }).ToArray(),
            navigationGlyphs = VerificationDescendants(nav).OfType<Viewbox>().Select(glyph => new { width = glyph.Width, height = glyph.Height, actualWidth = glyph.ActualWidth, actualHeight = glyph.ActualHeight }).ToArray(),
            headerGlyphs = VerificationDescendants(header).OfType<Viewbox>().Select(glyph => new { width = glyph.Width, height = glyph.Height, actualWidth = glyph.ActualWidth, actualHeight = glyph.ActualHeight }).ToArray(),
            expanded, elements };
    }

    private async Task VerificationCapture(string directory, string name, UIElement? target = null)
    {
        var bitmap = new RenderTargetBitmap();
        await bitmap.RenderAsync(target ?? root);
        var pixels = await bitmap.GetPixelsAsync();
        using var memory = new Windows.Storage.Streams.InMemoryRandomAccessStream();
        var encoder = await Windows.Graphics.Imaging.BitmapEncoder.CreateAsync(
            Windows.Graphics.Imaging.BitmapEncoder.PngEncoderId, memory);
        encoder.SetPixelData(Windows.Graphics.Imaging.BitmapPixelFormat.Bgra8,
            Windows.Graphics.Imaging.BitmapAlphaMode.Premultiplied, (uint)bitmap.PixelWidth,
            (uint)bitmap.PixelHeight, 96, 96, pixels.ToArray());
        await encoder.FlushAsync();
        memory.Seek(0);
        using var input = memory.AsStreamForRead();
        using var file = File.Create(Path.Combine(directory, name + ".png"));
        await input.CopyToAsync(file);
    }
}
