using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging;

namespace HaloBattery.Settings;

// Fixture-only capture of this process's window, including compositor-owned
// animations that XAML RenderTargetBitmap does not include. No input injection.
// https://learn.microsoft.com/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow
// https://learn.microsoft.com/uwp/api/windows.graphics.capture.direct3d11captureframepool.createfreethreaded
internal static class FixtureWindowCapture
{
    internal static async Task CaptureAsync(nint ownWindow, string pngPath)
    {
        var arguments = Environment.GetCommandLineArgs();
        var fixtureIndex = Array.IndexOf(arguments, "--fixture-state");
        if (fixtureIndex < 0 || fixtureIndex + 1 >= arguments.Length ||
            string.IsNullOrWhiteSpace(arguments[fixtureIndex + 1]))
            throw new InvalidOperationException("Window capture is available only with --fixture-state.");
        GetWindowThreadProcessId(ownWindow, out var owner);
        if (ownWindow == 0 || owner != (uint)Environment.ProcessId)
            throw new ArgumentException("Capture requires this process's own window.", nameof(ownWindow));
        if (!GraphicsCaptureSession.IsSupported())
            throw new NotSupportedException("Windows Graphics Capture is unavailable.");

        var item = CreateWindowItem(ownWindow);
        if (item.Size.Width <= 0 || item.Size.Height <= 0)
            throw new InvalidOperationException("The fixture window has no capturable area.");
        using var device = CreateDevice();
        using var pool = Direct3D11CaptureFramePool.CreateFreeThreaded(
            device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 2, item.Size);
        using var session = pool.CreateCaptureSession(item);
        session.IsCursorCaptureEnabled = false;
        var completion = new TaskCompletionSource<Direct3D11CaptureFrame>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        void FrameArrived(Direct3D11CaptureFramePool sender, object args)
        {
            try
            {
                var next = sender.TryGetNextFrame();
                if (next is not null && !completion.TrySetResult(next)) next.Dispose();
            }
            catch (Exception error) { completion.TrySetException(error); }
        }
        pool.FrameArrived += FrameArrived;
        try
        {
            session.StartCapture();
            using var frame = await completion.Task.WaitAsync(TimeSpan.FromSeconds(10));
            using var bitmap = await SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface);
            var destination = Path.GetFullPath(pngPath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            using var file = new FileStream(destination, FileMode.Create, FileAccess.ReadWrite, FileShare.None);
            using var stream = file.AsRandomAccessStream();
            var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
            encoder.SetSoftwareBitmap(bitmap);
            // A resized window can leave unused pixels in the frame-pool surface.
            encoder.BitmapTransform.Bounds = new BitmapBounds
            {
                Width = (uint)Math.Min(bitmap.PixelWidth, frame.ContentSize.Width),
                Height = (uint)Math.Min(bitmap.PixelHeight, frame.ContentSize.Height)
            };
            await encoder.FlushAsync();
        }
        finally
        {
            completion.TrySetCanceled();
            pool.FrameArrived -= FrameArrived;
        }
    }

    private static GraphicsCaptureItem CreateWindowItem(nint window)
    {
        const string className = "Windows.Graphics.Capture.GraphicsCaptureItem";
        Marshal.ThrowExceptionForHR(WindowsCreateString(className, className.Length, out var name));
        nint factory = 0, item = 0;
        try
        {
            var interopId = new Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356");
            Marshal.ThrowExceptionForHR(RoGetActivationFactory(name, ref interopId, out factory));
            // IGraphicsCaptureItemInterop derives from IUnknown; CreateForWindow
            // is its first method after QueryInterface/AddRef/Release.
            var method = Marshal.ReadIntPtr(Marshal.ReadIntPtr(factory), 3 * IntPtr.Size);
            var create = Marshal.GetDelegateForFunctionPointer<CreateForWindow>(method);
            var itemId = new Guid("79C3F95B-31F7-4EC2-A464-632EF5D30760");
            Marshal.ThrowExceptionForHR(create(factory, window, ref itemId, out item));
            return WinRT.MarshalInterface<GraphicsCaptureItem>.FromAbi(item);
        }
        finally
        {
            if (item != 0) Marshal.Release(item);
            if (factory != 0) Marshal.Release(factory);
            WindowsDeleteString(name);
        }
    }

    private static IDirect3DDevice CreateDevice()
    {
        nint native = 0, context = 0, dxgi = 0, inspectable = 0;
        try
        {
            // D3D_DRIVER_TYPE_HARDWARE, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            // D3D11_SDK_VERSION. No additional DirectX binding dependency.
            Marshal.ThrowExceptionForHR(D3D11CreateDevice(0, 1, 0, 0x20,
                0, 0, 7, out native, out _, out context));
            var dxgiId = new Guid("54EC77FA-1377-44E6-8C32-88FD5F44C84C");
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(native, in dxgiId, out dxgi));
            Marshal.ThrowExceptionForHR(CreateDirect3D11DeviceFromDXGIDevice(dxgi, out inspectable));
            return WinRT.MarshalInterface<IDirect3DDevice>.FromAbi(inspectable);
        }
        finally
        {
            if (inspectable != 0) Marshal.Release(inspectable);
            if (dxgi != 0) Marshal.Release(dxgi);
            if (context != 0) Marshal.Release(context);
            if (native != 0) Marshal.Release(native);
        }
    }

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int CreateForWindow(nint factory, nint window, ref Guid itemId, out nint item);

    [DllImport("user32.dll", ExactSpelling = true)]
    private static extern uint GetWindowThreadProcessId(nint window, out uint processId);
    [DllImport("combase.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int WindowsCreateString(string value, int length, out nint name);
    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int WindowsDeleteString(nint name);
    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int RoGetActivationFactory(nint name, ref Guid interfaceId, out nint factory);
    [DllImport("d3d11.dll", ExactSpelling = true)]
    private static extern int D3D11CreateDevice(nint adapter, uint driverType, nint software,
        uint flags, nint featureLevels, uint featureLevelCount, uint sdkVersion,
        out nint device, out uint featureLevel, out nint context);
    [DllImport("d3d11.dll", ExactSpelling = true)]
    private static extern int CreateDirect3D11DeviceFromDXGIDevice(nint dxgiDevice, out nint device);
}
