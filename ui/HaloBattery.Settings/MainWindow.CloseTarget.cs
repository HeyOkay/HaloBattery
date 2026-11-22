using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private Button? closeButton;

    private void ConfigureCloseCornerTarget(Button button)
    {
        closeButton = button;
        InstallCloseCornerRouting();
        // Absorb the old 12-DIP right inset and the header's vertical gaps into
        // this same native Button. Its visible 40-DIP face stays in place.
        button.Width = button.MinWidth = button.MaxWidth = HeaderButtonSize + HeaderEdgeInset;
        button.Height = button.MinHeight = button.MaxHeight = header.Height;
        button.FocusVisualMargin = new(0, (header.Height - HeaderButtonSize) / 2,
            HeaderEdgeInset, (header.Height - HeaderButtonSize) / 2);
        button.Template = (ControlTemplate)XamlReader.Load($$"""
            <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="Button">
                <Grid Background="Transparent">
                    <VisualStateManager.VisualStateGroups>
                        <VisualStateGroup x:Name="CommonStates">
                            <VisualState x:Name="Normal"/>
                            <VisualState x:Name="PointerOver">
                                <VisualState.Setters>
                                    <Setter Target="CloseFace.Background" Value="{{ExitHoverBackground}}"/>
                                </VisualState.Setters>
                            </VisualState>
                            <VisualState x:Name="Pressed">
                                <VisualState.Setters>
                                    <Setter Target="CloseFace.Background" Value="{{ExitPressedBackground}}"/>
                                </VisualState.Setters>
                            </VisualState>
                            <VisualState x:Name="Disabled">
                                <VisualState.Setters>
                                    <Setter Target="CloseFace.Opacity" Value="0.4"/>
                                </VisualState.Setters>
                            </VisualState>
                        </VisualStateGroup>
                    </VisualStateManager.VisualStateGroups>
                    <ContentPresenter x:Name="CloseFace" Width="40" Height="40"
                                      HorizontalAlignment="Left" VerticalAlignment="Center"
                                      Background="{TemplateBinding Background}"
                                      CornerRadius="{TemplateBinding CornerRadius}"
                                      Content="{TemplateBinding Content}"
                                      ContentTemplate="{TemplateBinding ContentTemplate}"
                                      HorizontalContentAlignment="Center" VerticalContentAlignment="Center"/>
                </Grid>
            </ControlTemplate>
            """);
    }

    private const nuint CloseCornerSubclassId = 0x48424354;
    private CloseCornerSubclassProc? closeCornerCallback;

    private void InstallCloseCornerRouting()
    {
        if (closeCornerCallback is not null) return;
        closeCornerCallback = RouteCloseCorner;
        if (!SetCloseCornerSubclass(hwnd, closeCornerCallback, CloseCornerSubclassId, 0))
        {
            closeCornerCallback = null;
            System.Diagnostics.Debug.WriteLine("Unable to extend Close through the native resize border.");
            return;
        }
        Closed += (_, _) =>
        {
            if (closeCornerCallback is not { } callback) return;
            RemoveCloseCornerSubclass(hwnd, callback, CloseCornerSubclassId);
            closeCornerCallback = null;
        };
    }

    private nint RouteCloseCorner(nint window, uint message, nint data, nint position, nuint id, nuint context)
    {
        var hit = DefCloseCornerSubclass(window, message, data, position);
        // Keep native resize behavior everywhere except this actual Button.
        // Returning HTCLIENT lets WinUI handle hover, capture, Click and keyboard
        // activation normally; no synthetic pointer events or duplicate actions.
        if (message != 0x0084 || hit is not (11 or 12 or 14)) return hit; // WM_NCHITTEST; right/top/top-right.
        try
        {
            if (closing || closeButton is not { IsLoaded: true } || root.XamlRoot is null) return hit;
            var point = new CloseCornerPoint { X = (short)((long)position & 0xffff), Y = (short)(((long)position >> 16) & 0xffff) };
            if (!ScreenToCloseClient(window, ref point)) return hit;
            var scale = Scale;
            if (point.X >= Math.Floor((root.ActualWidth - HeaderButtonSize - HeaderEdgeInset) * scale) &&
                point.X < Math.Ceiling(root.ActualWidth * scale) && point.Y >= 0 && point.Y < Math.Ceiling(header.Height * scale))
                return 1; // HTCLIENT, including the upper and right edges of Close.
        }
        catch (Exception error) { System.Diagnostics.Debug.WriteLine(error); }
        return hit;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct CloseCornerPoint { public int X, Y; }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate nint CloseCornerSubclassProc(nint window, uint message, nint data, nint position, nuint id, nuint context);
    [DllImport("comctl32.dll", EntryPoint = "SetWindowSubclass", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetCloseCornerSubclass(nint window, CloseCornerSubclassProc callback, nuint id, nuint context);
    [DllImport("comctl32.dll", EntryPoint = "RemoveWindowSubclass", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool RemoveCloseCornerSubclass(nint window, CloseCornerSubclassProc callback, nuint id);
    [DllImport("comctl32.dll", EntryPoint = "DefSubclassProc", ExactSpelling = true)]
    private static extern nint DefCloseCornerSubclass(nint window, uint message, nint data, nint position);
    [DllImport("user32.dll", EntryPoint = "ScreenToClient", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ScreenToCloseClient(nint window, ref CloseCornerPoint point);

}
