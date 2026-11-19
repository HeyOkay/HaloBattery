// Console-only surface for compiling the real bridge without WinUI or an HWND.
// No window is instantiated; unexpected GUI callbacks fail the test.
namespace Microsoft.UI.Xaml { }
namespace HaloBattery.Settings
{
    public sealed class MainWindow
    {
        public event EventHandler? Closed { add { } remove { } }
        public Dispatcher DispatcherQueue { get; } = new();
        public void ShowError(string message) => throw new InvalidOperationException("Unexpected UI: " + message);
        public void ApplyState(UiState state) => throw new InvalidOperationException("Unexpected UI");
        public void ShowSettingsWindow(string? page, string? key, long? id) => throw new InvalidOperationException("Unexpected UI");
        public void NavigateFromBackend(string page, string? key) => throw new InvalidOperationException("Unexpected UI");
        public void ApplyPreview(string? key, string png) => throw new InvalidOperationException("Unexpected UI");
        public void ApplyIconCatalogResponse(System.Text.Json.JsonElement message) => throw new InvalidOperationException("Unexpected UI");
        public Task ShutdownSettingsWindowAsync() => throw new InvalidOperationException("Unexpected UI");
    }
    public sealed class Dispatcher
    {
        public bool TryEnqueue(Action action) => throw new InvalidOperationException("Unexpected UI dispatcher");
    }
}
