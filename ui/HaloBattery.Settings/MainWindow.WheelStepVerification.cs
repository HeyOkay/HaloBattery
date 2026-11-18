using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.System;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private bool wheelFixtureRoutingValidated;

    [StructLayout(LayoutKind.Sequential)]
    private struct WheelFixturePoint { public int X, Y; }

    [DllImport("user32.dll", EntryPoint = "ClientToScreen", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool WheelFixtureClientToScreen(nint window, ref WheelFixturePoint point);

    [DllImport("user32.dll", EntryPoint = "ScreenToClient", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool WheelFixtureScreenToClient(nint window, ref WheelFixturePoint point);

    [DllImport("user32.dll", EntryPoint = "ChildWindowFromPointEx", ExactSpelling = true)]
    private static extern nint WheelFixtureChildWindowFromPoint(nint parent, WheelFixturePoint point, uint flags);

    [DllImport("user32.dll", EntryPoint = "GetClassNameW", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int WheelFixtureClassName(nint window, System.Text.StringBuilder name, int capacity);

    private async Task VerifyPageEntryWheelFilterLifetime(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Check("idle window has no native wheel hook or timer", !PageEntryBlocksWheel &&
            pageEntryWheelHook == 0 && pageEntryWheelTimer is null);
        EnsurePageEntryWheelFilter();
        Check("idle filter request installs no hook or timer", pageEntryWheelHook == 0 && pageEntryWheelTimer is null);
        try
        {
            var began = Environment.TickCount64;
            var dropped = discardedEntryWheelMessages;
            ArmWheelNavigation(began);
            var hook = pageEntryWheelHook;
            var timer = pageEntryWheelTimer;
            Check("wheel cooldown installs its native hook and bounded timer", hook != 0 && timer is { IsEnabled: true } &&
                timer.Interval == TimeSpan.FromMilliseconds(50));
            EnsurePageEntryWheelFilter();
            Check("repeated filter request retains one hook and timer", pageEntryWheelHook == hook && ReferenceEquals(pageEntryWheelTimer, timer));
            var posted = true;
            var packets = 0;
            do
            {
                posted &= PostFixtureWheel(hwnd, 0x020A, 120u << 16, 0);
                posted &= PostFixtureWheel(hwnd, 0x020E, 120u << 16, 0);
                packets++;
                await Task.Delay(25);
                if (packets == 4)
                    Check("active cooldown filters queued native wheel messages", posted && pageEntryWheelHook != 0 &&
                        discardedEntryWheelMessages >= dropped + 8);
            } while (Environment.TickCount64 - began < WheelNavigationGuard.CooldownMilliseconds + 200);
            Check("continuous wheel packets cannot extend the native filter lifetime", posted &&
                !wheelNavigationGuard.IsBlocked(Environment.TickCount64, false) && pageEntryWheelHook == 0 && pageEntryWheelTimer is null &&
                discardedEntryWheelMessages > dropped && discardedEntryWheelMessages < dropped + packets * 2,
                new { packets, discarded = discardedEntryWheelMessages - dropped, elapsed = Environment.TickCount64 - began });
            ArmWheelNavigation(Environment.TickCount64);
            Check("a later transition reinstalls native filtering", pageEntryWheelHook != 0 && pageEntryWheelTimer is { IsEnabled: true });
            StopPageEntryWheelFilter();
            StopPageEntryWheelFilter();
            Check("filter cleanup is immediate and idempotent", pageEntryWheelHook == 0 && pageEntryWheelTimer is null);
        }
        finally
        {
            wheelNavigationGuard.Reset();
            StopPageEntryWheelFilter();
        }
    }

    private async Task VerifyContentWheelNavigation(List<object> checks)
    {
        wheelFixtureRoutingValidated = false;
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        Navigate("devices", false);
        await VerifyPageEntryWheelFilterLifetime(checks);
        cards.Clear();
        var content = new StackPanel();
        var editor = new TextBox { Height = 40, Text = "Native editor keeps its wheel" };
        var inner = new ScrollViewer { Height = 40, Content = new Border { Height = 200 } };
        content.Children.Add(editor);
        content.Children.Add(inner);
        for (var i = 0; i < 80; i++)
            content.Children.Add(new Border { Height = 40, Background = Brush(i % 2 == 0 ? "#242424" : "#282828") });
        var scroll = Scroll(content);
        var host = (UIElement)scroll.Content;
        frame.Content = new Page { Content = scroll };
        await Task.Delay(150);
        root.UpdateLayout();
        var tolerance = 1 / Scale + .01;
        Check("wheel fixture has native scroll range", scroll.ScrollableHeight > 1000, scroll.ScrollableHeight);
        var baseline = Math.Floor(scroll.ScrollableHeight / 2);
        double origin = baseline;

        async Task Land(double target)
        {
            ResetContentWheelObservation();
            CancelOverscroll();
            wheelNavigationGuard.Reset();
            StopPageEntryWheelFilter();
            scroll.ChangeView(null, target, null, true);
            await At("fixture positioning", target);
            // Read the actual positioned offset after native pixel rounding.
            origin = scroll.VerticalOffset;
        }
        async Task At(string name, double target)
        {
            for (var i = 0; i < 100 && Math.Abs(scroll.VerticalOffset - target) > tolerance; i++) await Task.Delay(20);
            Check(name, Math.Abs(scroll.VerticalOffset - target) <= tolerance, new { expected = target, actual = scroll.VerticalOffset });
            await Task.Delay(30);
        }

        await Land(baseline);
        var bar = PageScrollBar(scroll)!;
        foreach (var delta in new[] { -120, -60, -1, 120, 60, 1, -1200 })
        {
            Check("interior wheel remains available to native scrolling", !ObserveContentWheelNavigation(scroll, delta), new { delta });
            Check("interior observer leaves the native offset unchanged", Math.Abs(scroll.VerticalOffset - origin) <= tolerance,
                new { delta, origin, actual = scroll.VerticalOffset });
        }
        var highResolutionPassedThrough = true;
        for (var i = 0; i < 120; i++)
            highResolutionPassedThrough &= !ObserveContentWheelNavigation(scroll, -1);
        Check("high resolution packets remain native input", highResolutionPassedThrough);
        await Task.Delay(100);
        Check("observer schedules no custom scroll animation", Math.Abs(scroll.VerticalOffset - origin) <= tolerance,
            new { origin, actual = scroll.VerticalOffset });
        Check("interior wheel does not charge overscroll", OverscrollState.Raw == 0);
        Check("zero wheel stays unhandled", !ObserveContentWheelNavigation(scroll, 0));

        await Land(scroll.ScrollableHeight);
        Check("bottom overscroll consumes the original half-notch", ObserveContentWheelNavigation(scroll, -60) && OverscrollState.Raw == 60,
            OverscrollState.Raw);
        Check("two boundary packets charge exactly their original deltas", ObserveContentWheelNavigation(scroll, -60) && OverscrollState.Raw == 120,
            OverscrollState.Raw);
        Check("boundary observation leaves the native offset unchanged", Math.Abs(scroll.VerticalOffset - origin) <= tolerance);

        await Land(0);
        Check("top overscroll consumes the original notch", ObserveContentWheelNavigation(scroll, 120) && OverscrollState.Raw == 120,
            OverscrollState.Raw);
        Check("reversal releases overscroll to native scrolling", !ObserveContentWheelNavigation(scroll, -60) && OverscrollState.Raw == 0);
        await Task.Delay(100);
        Check("reversal observer schedules no custom movement", Math.Abs(scroll.VerticalOffset - origin) <= tolerance);

        await Land(baseline);
        ArmWheelNavigation(Environment.TickCount64);
        Check("existing cooldown consumes wheel without moving content", ObserveContentWheelNavigation(scroll, -120) &&
            Math.Abs(scroll.VerticalOffset - origin) <= tolerance && OverscrollState.Raw == 0);
        wheelNavigationGuard.Reset();
        StopPageEntryWheelFilter();
        Check("editor and nested scroller retain native wheel handling", !ContentWheelBelongsToPage(editor, scroll, host) &&
            !ContentWheelBelongsToPage(inner.Content as DependencyObject, scroll, host) && !ContentWheelBelongsToPage(bar, scroll, frame) &&
            ContentWheelBelongsToPage(content.Children.Last(), scroll, host));
        Check("horizontal modified and zero wheel are excluded", !IsPlainVerticalWheel(120, true, VirtualKeyModifiers.None) &&
            !IsPlainVerticalWheel(120, false, VirtualKeyModifiers.Control) && !IsPlainVerticalWheel(120, false, VirtualKeyModifiers.Shift) &&
            !IsPlainVerticalWheel(0, false, VirtualKeyModifiers.None) && IsPlainVerticalWheel(-60, false, VirtualKeyModifiers.None));
        checks.Add(new
        {
            name = "native wheel observer verification scope",
            diagnostic = true,
            scope = "Decision logic and unchanged native ScrollViewer offset; no wheel distance or smoothing is simulated."
        });

        // Exercise real message -> XAML routing at a supplied content point.
        // Posting wheel messages does not move the user's cursor or use hardware.
        var point = scroll.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point(scroll.ActualWidth / 2, scroll.ActualHeight / 2));
        var screen = new WheelFixturePoint { X = (int)Math.Round(point.X * Scale), Y = (int)Math.Round(point.Y * Scale) };
        Check("native wheel content point resolves to screen coordinates", WheelFixtureClientToScreen(hwnd, ref screen));
        // WinUI receives mouse messages in its XAML input child, not the outer
        // application HWND. Descend only through this fixture's own children,
        // so other windows may overlap it without changing the input target.
        bool BelongsToFixture(nint window) => window != 0 && (window == hwnd || IsChild(hwnd, window) || GetAncestor(window, 3) == hwnd);
        var inputWindow = hwnd;
        Check("content hit-test starts at the fixture HWND", BelongsToFixture(inputWindow), new { inputWindow = inputWindow.ToInt64(), hwnd = hwnd.ToInt64() });
        for (var depth = 0; depth < 16; depth++)
        {
            var local = screen;
            Check("native child hit-test point converts safely", WheelFixtureScreenToClient(inputWindow, ref local));
            var child = WheelFixtureChildWindowFromPoint(inputWindow, local, 0x0001 | 0x0002 | 0x0004);
            if (child == 0 || child == inputWindow) break;
            Check("nested input HWND belongs to this fixture", BelongsToFixture(child));
            inputWindow = child;
        }
        var inputClass = new System.Text.StringBuilder(256);
        WheelFixtureClassName(inputWindow, inputClass, inputClass.Capacity);
        Check("native wheel targets the verified content input HWND", BelongsToFixture(inputWindow),
            new { inputWindow = inputWindow.ToInt64(), className = inputClass.ToString(), screen.X, screen.Y });
        var position = unchecked((nint)(uint)(((uint)screen.Y & 0xffff) << 16 | ((uint)screen.X & 0xffff)));
        var inputClient = screen;
        Check("native input client point resolves", WheelFixtureScreenToClient(inputWindow, ref inputClient));
        var clientPosition = unchecked((nint)(uint)(((uint)inputClient.Y & 0xffff) << 16 | ((uint)inputClient.X & 0xffff)));
        async Task<bool> PostContentWheel()
        {
            // Establish XAML hover state using a local message, without moving
            // the physical cursor or injecting any global input.
            var moved = PostFixtureWheel(inputWindow, 0x0200, 0, clientPosition);
            await Task.Delay(30);
            return PostFixtureWheel(inputWindow, 0x020A, unchecked((nuint)(uint)(-120 << 16)), position) && moved;
        }

        // A native positive control distinguishes an unsupported legacy
        // message harness from a defect in the page's native input path.
        var observedPage = frame.Content;
        var nativeScroll = new ScrollViewer
        {
            Content = new Border { Height = 3400, Background = Brush("#242424") },
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        };
        frame.Content = new Page { Content = nativeScroll };
        await Task.Delay(100);
        root.UpdateLayout();
        nativeScroll.ChangeView(null, baseline, null, true);
        await Task.Delay(100);
        var nativeBefore = nativeScroll.VerticalOffset;
        var nativeViewSettled = false;
        void NativeViewChanged(object? sender, ScrollViewerViewChangedEventArgs args)
        {
            if (!args.IsIntermediate) nativeViewSettled = true;
        }
        nativeScroll.ViewChanged += NativeViewChanged;
        var nativePosted = await PostContentWheel();
        for (var i = 0; i < 100 && (!nativeViewSettled || Math.Abs(nativeScroll.VerticalOffset - nativeBefore) <= tolerance); i++)
            await Task.Delay(20);
        var nativeAfter = nativeScroll.VerticalOffset;
        nativeScroll.ViewChanged -= NativeViewChanged;
        frame.Content = observedPage;
        await Task.Delay(100);
        root.UpdateLayout();
        Check("legacy positive-control input messages were posted", nativePosted);
        var nativeWheelRoutingSupported = nativeViewSettled && nativeAfter > nativeBefore + tolerance;
        checks.Add(new
        {
            name = "legacy native wheel routing capability",
            diagnostic = true,
            nativeWheelRoutingSupported,
            nativeViewSettled,
            before = nativeBefore,
            after = nativeAfter,
            inputClass = inputClass.ToString(),
            reason = nativeWheelRoutingSupported ? "Plain native ScrollViewer accepted the posted wheel input."
                : "Posted WM_MOUSEMOVE/WM_MOUSEWHEEL reached a verified owned HWND but did not produce a completed scroll in an unmodified native ScrollViewer. This runtime did not validate this synthetic legacy-message harness; end-to-end wheel routing is not validated.",
            source = "https://github.com/microsoft/microsoft-ui-xaml/blob/main/dxaml/xcp/dxaml/lib/InputSiteAdapter.cpp#L301-L364"
        });
        if (!nativeWheelRoutingSupported)
        {
            ResetContentWheelObservation();
            CancelOverscroll();
            return;
        }

        await Land(baseline);
        Check("native content wheel message posts without cursor control", await PostContentWheel());
        await At("page wheel uses the unmodified native ScrollViewer distance", origin + nativeAfter - nativeBefore);
        Check("native routed interior wheel is not charged again by the frame", OverscrollState.Raw == 0, OverscrollState.Raw);
        await Land(scroll.ScrollableHeight);
        await PostContentWheel();
        await Task.Delay(80);
        Check("host and frame do not double-feed boundary overscroll", OverscrollState.Raw == 120, OverscrollState.Raw);
        wheelFixtureRoutingValidated = true;
        ResetContentWheelObservation();
        CancelOverscroll();
    }
}
