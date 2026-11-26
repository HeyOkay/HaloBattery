using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private int navigationWheelRemainder;
    private readonly WheelNavigationGuard wheelNavigationGuard = new();
    private bool PageEntryBlocksWheel => pendingEntryPage is not null || pageEntry is not null;
    private nint pageEntryWheelHook;
    private WheelMessageHook? pageEntryWheelCallback;
    private DispatcherTimer? pageEntryWheelTimer;
    private int discardedEntryWheelMessages;
    private UIElement? observedContentWheelHost;
    private PointerRoutedEventArgs? observedContentWheel;

    private delegate nint WheelMessageHook(int code, nuint flags, nint message);

    [StructLayout(LayoutKind.Sequential)]
    private struct WheelQueueMessage
    {
        public nint Window;
        public uint Message;
        public nuint WParam;
        public nint LParam;
        public uint Time;
        public int X, Y;
    }

    [DllImport("user32.dll", EntryPoint = "SetWindowsHookExW", ExactSpelling = true, SetLastError = true)]
    private static extern nint InstallWheelHook(int kind, WheelMessageHook callback, nint module, uint thread);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnhookWindowsHookEx(nint hook);
    [DllImport("user32.dll")]
    private static extern nint CallNextHookEx(nint hook, int code, nuint flags, nint message);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsChild(nint parent, nint child);
    [DllImport("user32.dll")]
    private static extern nint GetAncestor(nint window, uint flags);
    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    private bool DiscardNavigationWheel()
    {
        if (!wheelNavigationGuard.Consume(Environment.TickCount64, PageEntryBlocksWheel || pendingOverscrollNavigation is not null)) return false;
        navigationWheelRemainder = 0;
        return true;
    }

    private nint FilterPageEntryWheel(int code, nuint flags, nint messagePointer)
    {
        // WH_GETMESSAGE runs before native XAML controls consume the wheel.
        // Only inspect removed messages on this window's own UI thread.
        if (code == 0 && flags == 1 && (unchecked((uint)Marshal.ReadInt32(messagePointer, IntPtr.Size)) is 0x020A or 0x020E or 0x024E or 0x024F))
        {
            var message = Marshal.PtrToStructure<WheelQueueMessage>(messagePointer);
            if ((message.Message is 0x020A or 0x020E or 0x024E or 0x024F) &&
                (message.Window == hwnd || IsChild(hwnd, message.Window) || GetAncestor(message.Window, 3) == hwnd) && DiscardNavigationWheel())
            {
                navigationWheelRemainder = 0;
                discardedEntryWheelMessages++;
                // Rewrite only the message and payload; preserve native MSG
                // timestamps, coordinates and reserved/padding words.
                Marshal.WriteInt32(messagePointer, IntPtr.Size, 0); // WM_NULL
                Marshal.WriteIntPtr(messagePointer, 2 * IntPtr.Size, 0);
                Marshal.WriteIntPtr(messagePointer, 3 * IntPtr.Size, 0);
            }
        }
        return CallNextHookEx(pageEntryWheelHook, code, flags, messagePointer);
    }

    private void ArmWheelNavigation(long now)
    {
        wheelNavigationGuard.Arm(now);
        EnsurePageEntryWheelFilter();
    }

    private void EnsurePageEntryWheelFilter()
    {
        if (closing || !wheelNavigationGuard.IsBlocked(Environment.TickCount64,
            PageEntryBlocksWheel || pendingOverscrollNavigation is not null) || pageEntryWheelHook != 0) return;
        pageEntryWheelCallback ??= FilterPageEntryWheel;
        pageEntryWheelHook = InstallWheelHook(3, pageEntryWheelCallback, 0, GetCurrentThreadId());
        if (pageEntryWheelHook == 0)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Unable to install the settings window wheel filter.");
        // The hook protects embedded native controls during a transition, but
        // ordinary scrolling and window dragging need no managed message hook.
        // Poll only while blocked; wheel packets never restart this timer or
        // extend the guard's fixed deadline.
        pageEntryWheelTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(50) };
        pageEntryWheelTimer.Tick += PageEntryWheelFilterTick;
        pageEntryWheelTimer.Start();
    }

    private void PageEntryWheelFilterTick(object? sender, object args)
    {
        if (closing || !wheelNavigationGuard.IsBlocked(Environment.TickCount64,
            PageEntryBlocksWheel || pendingOverscrollNavigation is not null)) StopPageEntryWheelFilter();
    }

    private void StopPageEntryWheelFilter()
    {
        if (pageEntryWheelTimer is { } timer)
        {
            timer.Stop();
            timer.Tick -= PageEntryWheelFilterTick;
            pageEntryWheelTimer = null;
        }
        if (pageEntryWheelHook != 0) UnhookWindowsHookEx(pageEntryWheelHook);
        pageEntryWheelHook = 0;
    }

    private void AttachPageWheelNavigation(UIElement navigation)
    {
        Closed += (_, _) => StopPageEntryWheelFilter();
        if (navigation is Panel panel) panel.Background = Brush("#00000000");
        navigation.AddHandler(UIElement.PointerWheelChangedEvent,
            new PointerEventHandler(NavigationWheelChanged), true);
        frame.AddHandler(UIElement.PointerWheelChangedEvent,
            new PointerEventHandler(ContentWheelChanged), true);
    }

    private bool WheelDelta(PointerRoutedEventArgs args, out int delta)
    {
        var properties = args.GetCurrentPoint(root).Properties;
        delta = properties.MouseWheelDelta;
        return !closing && IsPlainVerticalWheel(delta, properties.IsHorizontalMouseWheel, args.KeyModifiers);
    }

    private static bool IsPlainVerticalWheel(int delta, bool horizontal, VirtualKeyModifiers modifiers) =>
        delta != 0 && !horizontal && modifiers == VirtualKeyModifiers.None;

    private void AttachContentWheelNavigation(UIElement host)
    {
        // Observe boundaries before the ScrollViewer's native wheel handler.
        // Interior input remains unhandled so DirectManipulation owns its
        // distance, accumulation and animation; arrow-key line units do not
        // describe mouse-wheel scrolling.
        // microsoft/microsoft-ui-xaml, ScrollViewer_Partial.cpp:2614-2620.
        host.AddHandler(UIElement.PointerWheelChangedEvent,
            new PointerEventHandler(ContentWheelNavigationChanged), true);
        if (host is FrameworkElement element)
            element.Unloaded += (_, _) => { if (ReferenceEquals(observedContentWheelHost, host)) ResetContentWheelObservation(); };
    }

    private void ResetContentWheelObservation()
    {
        observedContentWheelHost = null;
        observedContentWheel = null;
    }

    private static bool ContentWheelBelongsToPage(DependencyObject? source, ScrollViewer pageScroll, DependencyObject stop)
    {
        for (var node = source; node is not null && node != stop; node = VisualTreeHelper.GetParent(node))
            if (node is ComboBox or ComboBoxItem or TextBox or PasswordBox or RichEditBox or Slider or NumberBox or ListViewBase or ScrollBar ||
                node is ScrollViewer nested && !ReferenceEquals(nested, pageScroll)) return false;
        return true;
    }

    private void ContentWheelNavigationChanged(object sender, PointerRoutedEventArgs args)
    {
        if (DiscardNavigationWheel()) { args.Handled = true; return; }
        if (args.Handled || !WheelDelta(args, out var delta) ||
            frame.Content is not Page { Content: ScrollViewer scroll }) return;
        if (!ContentWheelBelongsToPage(args.OriginalSource as DependencyObject, scroll, (DependencyObject)sender))
        {
            CancelOverscroll();
            return;
        }
        observedContentWheelHost = (UIElement)sender;
        observedContentWheel = args;
        // Record both consumed and passed-through packets. The outer frame
        // must not feed this original delta into overscroll a second time.
        if (ObserveContentWheelNavigation(scroll, delta)) args.Handled = true;
    }

    private bool ObserveContentWheelNavigation(ScrollViewer scroll, int delta)
    {
        if (DiscardNavigationWheel()) return true;
        if (closing || delta == 0) return false;
        return HandleContentOverscroll(delta, scroll.VerticalOffset, scroll.ScrollableHeight);
    }

    private void NavigationWheelChanged(object sender, PointerRoutedEventArgs args)
    {
        if (DiscardNavigationWheel()) { args.Handled = true; return; }
        if (!WheelDelta(args, out var delta)) return;
        args.Handled = true;
        AdvanceWheelPage(delta, ref navigationWheelRemainder);
    }

    private void ContentWheelChanged(object sender, PointerRoutedEventArgs args)
    {
        if (DiscardNavigationWheel()) { args.Handled = true; return; }
        // The inner host already observed this packet before native scrolling,
        // including interior packets it deliberately left unhandled. Empty
        // viewport events still reach this fallback boundary observer.
        if (ReferenceEquals(args, observedContentWheel)) { ResetContentWheelObservation(); return; }
        if (!WheelDelta(args, out var delta)) return;
        // Native editors and pickers keep their own wheel behavior.
        if (frame.Content is not Page { Content: ScrollViewer scroll }) return;
        if (!ContentWheelBelongsToPage(args.OriginalSource as DependencyObject, scroll, frame))
        {
            CancelOverscroll();
            return;
        }
        FeedContentOverscroll(delta, scroll, args);
    }

    private bool AdvanceWheelPage(int delta, ref int remainder)
    {
        if (DiscardNavigationWheel()) { remainder = 0; return false; }
        if (delta == 0 || closing) return false;
        if (Math.Sign(delta) != Math.Sign(remainder)) remainder = 0;
        // Accumulate high-resolution wheel packets without queuing navigation.
        remainder += Math.Clamp(delta, -12000, 12000);
        var steps = remainder / 120;
        remainder %= 120;
        if (steps == 0) return false;
        var pages = State.Pages.Select(page => page.Id).ToArray();
        if (pages.Length == 0) return false;
        var index = Math.Max(0, Array.IndexOf(pages, current));
        var next = Math.Clamp(index - steps, 0, pages.Length - 1);
        if (next == index) return false;
        ArmWheelNavigation(Environment.TickCount64);
        Navigate(pages[next], true, preserveWheelBurst: true);
        return true;
    }
}
