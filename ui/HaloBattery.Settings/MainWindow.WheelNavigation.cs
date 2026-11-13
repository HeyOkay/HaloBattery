using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private int navigationWheelRemainder;
    private bool PageEntryBlocksWheel => pendingEntryPage is not null || pageEntry is not null;
    private nint pageEntryWheelHook;
    private WheelMessageHook? pageEntryWheelCallback;
    private int discardedEntryWheelMessages;

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

    private bool DiscardWheelDuringPageEntry()
    {
        if (!PageEntryBlocksWheel) return false;
        navigationWheelRemainder = 0;
        return true;
    }

    private nint FilterPageEntryWheel(int code, nuint flags, nint messagePointer)
    {
        // WH_GETMESSAGE runs before native XAML controls consume the wheel.
        // Only inspect removed messages on this window's own UI thread.
        if (code == 0 && flags == 1 && PageEntryBlocksWheel)
        {
            var message = Marshal.PtrToStructure<WheelQueueMessage>(messagePointer);
            if ((message.Message is 0x020A or 0x020E or 0x024E or 0x024F) &&
                (message.Window == hwnd || IsChild(hwnd, message.Window) || GetAncestor(message.Window, 3) == hwnd))
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

    private void AttachPageWheelNavigation(UIElement navigation)
    {
        pageEntryWheelCallback = FilterPageEntryWheel;
        pageEntryWheelHook = InstallWheelHook(3, pageEntryWheelCallback, 0, GetCurrentThreadId());
        if (pageEntryWheelHook == 0)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Unable to install the settings window wheel filter.");
        Closed += (_, _) =>
        {
            if (pageEntryWheelHook != 0) UnhookWindowsHookEx(pageEntryWheelHook);
            pageEntryWheelHook = 0;
        };
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
        return !closing && args.KeyModifiers == VirtualKeyModifiers.None &&
            !properties.IsHorizontalMouseWheel && delta != 0;
    }

    private void NavigationWheelChanged(object sender, PointerRoutedEventArgs args)
    {
        if (DiscardWheelDuringPageEntry()) { args.Handled = true; return; }
        if (!WheelDelta(args, out var delta)) return;
        args.Handled = true;
        AdvanceWheelPage(delta, ref navigationWheelRemainder);
    }

    private void ContentWheelChanged(object sender, PointerRoutedEventArgs args)
    {
        if (DiscardWheelDuringPageEntry()) { args.Handled = true; return; }
        if (!WheelDelta(args, out var delta)) return;
        // Native editors and pickers keep their own wheel behavior.
        for (var node = args.OriginalSource as DependencyObject; node is not null && node != frame;
            node = VisualTreeHelper.GetParent(node))
            if (node is ComboBox or ComboBoxItem or TextBox or Slider or ListViewBase)
            {
                CancelOverscroll();
                return;
            }
        if (frame.Content is not Page { Content: ScrollViewer scroll }) return;
        FeedContentOverscroll(delta, scroll, args);
    }

    private bool AdvanceWheelPage(int delta, ref int remainder)
    {
        if (DiscardWheelDuringPageEntry()) { remainder = 0; return false; }
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
        Navigate(pages[next], true);
        return true;
    }
}
