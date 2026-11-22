using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct CloseProbePoint { public int X, Y; }
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
    private static extern bool ClientToScreen(nint window, ref CloseProbePoint point);
    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "SendMessageW", ExactSpelling = true)]
    private static extern nint ProbeCloseHit(nint window, uint message, nuint data, nint point);

    private void VerifyCloseCornerTarget(Action<string, bool, object?> check, string phase)
    {
        var button = closeButton!;
        var bounds = button.TransformToVisual(header).TransformBounds(new Rect(0, 0, button.ActualWidth, button.ActualHeight));
        var face = VerificationDescendants(button).OfType<ContentPresenter>().Single(p => p.Name == "CloseFace");
        var faceBounds = face.TransformToVisual(header).TransformBounds(new Rect(0, 0, face.ActualWidth, face.ActualHeight));
        var tolerance = 1.01 / Scale;
        check(phase + ": Close reaches top and right edges", Math.Abs(bounds.Top) <= tolerance &&
            Math.Abs(bounds.Right - header.ActualWidth) <= tolerance && Math.Abs(bounds.Height - header.Height) <= tolerance,
            new { bounds, header.ActualWidth, header.ActualHeight });
        check(phase + ": Close face keeps its size and original inset", Math.Abs(faceBounds.Width - 40) <= tolerance &&
            Math.Abs(faceBounds.Height - 40) <= tolerance && Math.Abs(faceBounds.Right - (header.ActualWidth - HeaderEdgeInset)) <= tolerance &&
            Math.Abs(faceBounds.Top - (header.Height - HeaderButtonSize) / 2) <= tolerance, new { faceBounds });
        var nativePoint = new CloseProbePoint { X = (int)Math.Ceiling(root.ActualWidth * Scale) - 1, Y = 1 };
        if (!ClientToScreen(hwnd, ref nativePoint)) throw new InvalidOperationException("Close hit-test coordinates unavailable");
        var packed = (nint)((uint)(ushort)nativePoint.X | ((uint)(ushort)nativePoint.Y << 16));
        var nativeHit = ProbeCloseHit(hwnd, 0x0084, 0, packed); // WM_NCHITTEST, no click or cursor movement.
        check(phase + ": native top-right routes to client button", nativeHit == 1, new { nativeHit = (long)nativeHit });
        foreach (var point in new[] { new Point(header.ActualWidth - .5, .5),
                     new Point(header.ActualWidth - .5, header.Height / 2), new Point(bounds.Left + .5, .5) })
        {
            var hit = VisualTreeHelper.FindElementsInHostCoordinates(point, header).Any(element => ReferenceEquals(element, button));
            check(phase + ": expanded Close area is hit-testable", hit, new { point });
        }
        foreach (var state in new[] { ("PointerOver", ExitHoverBackground), ("Pressed", ExitPressedBackground) })
        {
            var applied = VisualStateManager.GoToState(button, state.Item1, false);
            var actual = (face.Background as SolidColorBrush)?.Color;
            check(phase + ": Close " + state.Item1 + " retains feedback", applied && actual == Brush(state.Item2).Color,
                new { state = state.Item1, color = actual?.ToString() });
        }
        VisualStateManager.GoToState(button, "Normal", false);
    }
}
