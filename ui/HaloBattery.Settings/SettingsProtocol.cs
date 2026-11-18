using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Microsoft.UI.Xaml;

namespace HaloBattery.Settings;

public sealed class UiState
{
    public string Type { get; set; } = "state";
    public int Protocol { get; set; } = 1;
    public long Revision { get; set; }
    public string SessionId { get; set; } = "";
    public string Version { get; set; } = "";
    public string Language { get; set; } = "en";
    public string DataDir { get; set; } = "";
    public string StatusPath { get; set; } = "";
    public string? SelectedDeviceKey { get; set; }
    public string Page { get; set; } = "general";
    public Dictionary<string,string> Labels { get; set; } = new();
    public Dictionary<string,JsonElement> Settings { get; set; } = new();
    public bool Autostart { get; set; }
    public bool IconLightTaskbar { get; set; }
    public List<DeviceState> Devices { get; set; } = new();
    public List<ProviderState> Providers { get; set; } = new();
    public UiOptions Options { get; set; } = new();
    public UpdateState Update { get; set; } = new();
    public IReadOnlyList<UiPage> Pages => PageIds.Select(id => new UiPage(id,
        Labels.GetValueOrDefault("settings."+id, id))).ToArray();
    public static readonly string[] PageIds = { "general", "devices", "icons", "notifications", "advanced", "about" };
    public static UiState Empty => new();
}

public sealed record UiPage(string Id,string Title);
public sealed class DeviceState
{
    public string Key { get; set; } = "";
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public string Kind { get; set; } = "bluetooth";
    public string Pictogram { get; set; } = "";
    public string CustomIconId { get; set; } = "";
    public string CustomIconName { get; set; } = "";
    public bool CustomIconUnavailable { get; set; }
    public bool Controller { get; set; }
    public string Brand { get; set; } = "default";
    public bool BrandColor { get; set; }
    public int? LowOverride { get; set; }
    public bool Hidden { get; set; }
    public int? Level { get; set; }
    public bool? Charging { get; set; }
    public bool Online { get; set; }
    public bool Paused { get; set; }
}
public sealed class ProviderState
{
    public string Name { get; set; } = "";
    public string Label { get; set; } = "";
    public bool Enabled { get; set; }
}
public sealed class UiOption
{
    public JsonElement Value { get; set; }
    public string Label { get; set; } = "";
    public static UiOption Create(object? value,string label) => new() { Value=SettingsJson.Element(value), Label=label };
}
public sealed class UiOptions
{
    public List<UiOption> Languages { get; set; } = new();
    public List<UiOption> Intervals { get; set; } = new();
    public List<UiOption> Thresholds { get; set; } = new();
    public List<UiOption> Pictograms { get; set; } = new();
    public List<UiOption> Brands { get; set; } = new();
    public List<UiOption> IconThemes { get; set; } = new();
}
public sealed class UpdateState
{
    public string Version { get; set; } = "";
    public int? Progress { get; set; }
    public bool InstallOk { get; set; }
    public string Status { get; set; } = "";
    public string Message { get; set; } = "";
}

/// <summary>Asynchronous private child-process protocol; never block the XAML thread.</summary>
public sealed partial class SettingsBridge : IDisposable
{
#if HALO_BATTERY_VERIFICATION
    // The existing nonshipping QA reports contain anonymous diagnostic shapes.
    // Keep their JIT-only serializer until that separate harness is migrated.
    public static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive=true,
        PropertyNamingPolicy=JsonNamingPolicy.CamelCase, MaxDepth=32 };
#else
    public static readonly JsonSerializerOptions Json = SettingsJson.Options;
#endif
    private readonly Channel<object> outgoing = Channel.CreateBounded<object>(new BoundedChannelOptions(128) {
        SingleReader=true, SingleWriter=false, FullMode=BoundedChannelFullMode.Wait });
    private readonly CancellationTokenSource cancel = new();
    private MainWindow? window;
    private StreamReader? input;
    private StreamWriter? output;
    private long nextId;
    private long lastRevision=-1;
    private long pendingPreview;
    private string session="";
    private bool disposed;
    private bool fixture;
    internal bool FixtureMode=>fixture;
    public List<string> FixtureCommands { get; } = new();

    private string? fixturePath;
    private bool initialized;
    private sealed record FlushBarrier(TaskCompletionSource<bool> Completion);

    // The splash can be shown while the real model arrives over this handshake.
    public async Task<UiState> InitializeAsync()
    {
        if(initialized) throw new InvalidOperationException("Settings bridge already initialized.");
        var args=Environment.GetCommandLineArgs();
        var at=Array.IndexOf(args,"--fixture-state");
        if(at>=0 && at+1<args.Length)
        {
            fixture=true;
            fixturePath=Path.GetFullPath(args[at+1]);
            var state=await Task.Run(()=>
            {
                if(new FileInfo(fixturePath).Length>1024*1024) throw new InvalidDataException("Fixture exceeds protocol limit");
                return JsonSerializer.Deserialize(File.ReadAllText(fixturePath,Encoding.UTF8),SettingsJson.Context.UiState)
                    ?? throw new InvalidDataException("Invalid fixture state");
            });
            initialized=true;
            return state;
        }
        input=new StreamReader(Console.OpenStandardInput(),new UTF8Encoding(false,true));
        output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false)) { AutoFlush=false };
        _=Task.Run(WriteLoop);
        Enqueue(SettingsJson.Object(("type", "hello"), ("protocol", 1), ("uiVersion", "1")));
        using var startup=CancellationTokenSource.CreateLinkedTokenSource(cancel.Token);
        startup.CancelAfter(TimeSpan.FromSeconds(12));
        while(true)
        {
            var line=await input.ReadLineAsync(startup.Token);
            if(line is null) throw new EndOfStreamException("Settings backend closed before initial state.");
            if(Encoding.UTF8.GetByteCount(line)>4*1024*1024) throw new InvalidDataException("Oversized settings message");
            using var document=JsonDocument.Parse(line,new JsonDocumentOptions {MaxDepth=32});
            var message=document.RootElement;
            switch(message.GetProperty("type").GetString())
            {
                case "welcome":
                    if(message.GetProperty("protocol").GetInt32()!=1) throw new InvalidDataException("Incompatible settings protocol");
                    session=message.GetProperty("sessionId").GetString()??"";
                    break;
                case "state":
                    var state=message.Deserialize(SettingsJson.Context.UiState)??throw new InvalidDataException("Missing initial state");
                    if(state.Protocol!=1 || session.Length==0 || state.SessionId!=session) throw new InvalidDataException("Settings session mismatch");
                    lastRevision=state.Revision;
                    initialized=true;
                    return state;
                case "shutdown": throw new EndOfStreamException("Settings backend shut down during initialization.");
                default: throw new InvalidDataException("Unexpected initial settings message");
            }
        }
    }

    public void Attach(MainWindow owner)
    {
        if(!initialized || window is not null) throw new InvalidOperationException("Invalid settings bridge attachment.");
        window=owner;
        owner.Closed+=(_,_)=>Dispose();
        if(fixture)
        {
#if HALO_BATTERY_VERIFICATION
            owner.StartFixtureVerification(fixturePath!);
#endif
            return;
        }
        _=Task.Run(ReadLoop);
    }

    internal void SendVisibility(bool visible,long? requestId=null)
    {
        if(!fixture)
        {
            if(requestId is {} id)Enqueue(SettingsJson.Object(("type", "visibility"), ("visible", visible), ("requestId", id)));
            else Enqueue(SettingsJson.Object(("type", "visibility"), ("visible", visible)));
        }
    }

    internal void SendClosing()
    {
        if(!fixture)Enqueue(SettingsJson.Object(("type", "closing")));
    }

    internal async Task FlushAsync()
    {
        if(fixture || disposed)return;
        var drained=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(cancel.Token);
        deadline.CancelAfter(TimeSpan.FromSeconds(2));
        try
        {
            // A barrier in the same queue proves every earlier edit reached the
            // pipe before Dispose cancels the writer on native Window.Closed.
            await outgoing.Writer.WriteAsync(new FlushBarrier(drained),deadline.Token);
            await drained.Task.WaitAsync(deadline.Token);
        }
        catch(Exception error) when(error is OperationCanceledException or ChannelClosedException)
        { System.Diagnostics.Debug.WriteLine(error); }
    }

    public long SendCommand(string name,JsonObject args)
    {
        var id=Interlocked.Increment(ref nextId);
        var message=SettingsJson.Object(("type", "command"), ("protocol", 1), ("id", id), ("name", name), ("args", args));
        if(fixture)
        {
            FixtureCommands.Add(message.ToJsonString(Json));
            return id;
        }
        if(!Enqueue(message))
            window?.ShowError("The settings connection is busy. Please try again.");
        return id;
    }

    public long RequestCatalog(string action, string? deviceKey, string query = "", int start = 0, int size = 40, string? iconId = null)
    {
        if (disposed) return 0;
        var id = Interlocked.Increment(ref nextId);
        var message = SettingsJson.Object(("type", "catalog"), ("protocol", 1), ("id", id), ("action", action), ("deviceKey", deviceKey), ("query", query), ("start", start), ("size", size), ("iconId", iconId));
        if (fixture) { FixtureCommands.Add(message.ToJsonString(Json)); return id; }
        return Enqueue(message) ? id : 0;
    }

    public bool RequestPreview(string? deviceKey)
    {
        if(fixture || disposed || Interlocked.Read(ref pendingPreview)!=0) return false;
        var id=Interlocked.Increment(ref nextId);
        if(Interlocked.CompareExchange(ref pendingPreview,id,0)!=0) return false;
        if(Enqueue(SettingsJson.Object(("type", "preview"), ("id", id), ("deviceKey", deviceKey)))) return true;
        Interlocked.Exchange(ref pendingPreview,0);
        return false;
    }

    private bool Enqueue(JsonObject message) => !disposed && outgoing.Writer.TryWrite(message);

    private async Task WriteLoop()
    {
        try
        {
            await foreach(var message in outgoing.Reader.ReadAllAsync(cancel.Token))
            {
                if(message is FlushBarrier barrier)
                {
                    await output!.FlushAsync(cancel.Token);
                    barrier.Completion.TrySetResult(true);
                    continue;
                }
                await output!.WriteLineAsync(((JsonObject)message).ToJsonString(Json));
                await output.FlushAsync(cancel.Token);
            }
        }
        catch(Exception error) when(error is IOException or OperationCanceledException or ObjectDisposedException)
        { Disconnect(); }
    }

    private async Task ReadLoop()
    {
        try
        {
            while(!cancel.IsCancellationRequested)
            {
                var line=await input!.ReadLineAsync(cancel.Token);
                if(line is null) break;
                if(Encoding.UTF8.GetByteCount(line)>4*1024*1024) throw new InvalidDataException("Oversized settings message");
                using var document=JsonDocument.Parse(line,new JsonDocumentOptions { MaxDepth=32 });
                var message=document.RootElement.Clone();
                if(window is null || !window.DispatcherQueue.TryEnqueue(()=>Handle(message))) break;
            }
        }
        catch(Exception error) when(error is IOException or OperationCanceledException or JsonException or ObjectDisposedException)
        { if(!disposed) Console.Error.WriteLine(error.Message); }
        finally { Disconnect(); }
    }

    private void Handle(JsonElement message)
    {
        if(disposed || window is null) return;
        try
        {
            var type=message.GetProperty("type").GetString();
            switch(type)
            {
                case "welcome":
                    if(message.GetProperty("protocol").GetInt32()!=1) throw new InvalidDataException("Incompatible settings protocol");
                    session=message.GetProperty("sessionId").GetString() ?? "";
                    break;
                case "state":
                    var state=message.Deserialize(SettingsJson.Context.UiState) ?? throw new InvalidDataException("Missing state");
                    if(state.Protocol!=1 || session.Length==0 || state.SessionId!=session) throw new InvalidDataException("Settings session mismatch");
                    if(state.Revision<lastRevision) return;
                    lastRevision=state.Revision;
                    window.ApplyState(state);
                    break;
                case "show":
                    var restore=message.TryGetProperty("restoreView",out var restoreView) && restoreView.ValueKind==JsonValueKind.True;
                    window.ShowSettingsWindow(restore?null:message.GetProperty("page").GetString(),
                        !restore && message.TryGetProperty("deviceKey",out var showKey) && showKey.ValueKind==JsonValueKind.String?showKey.GetString():null,
                        message.TryGetProperty("requestId",out var requestId) && requestId.ValueKind==JsonValueKind.Number && requestId.TryGetInt64(out var showId)?showId:null);
                    break;
                case "navigate":
                    window.NavigateFromBackend(message.GetProperty("page").GetString() ?? "general",
                        message.TryGetProperty("deviceKey",out var key) && key.ValueKind==JsonValueKind.String?key.GetString():null);
                    break;
                case "preview":
                    var id=message.GetProperty("id").GetInt64();
                    if(id!=Interlocked.Read(ref pendingPreview)) return;
                    Interlocked.Exchange(ref pendingPreview,0);
                    if(message.GetProperty("size").GetInt32()!=80) throw new InvalidDataException("Preview must be 80 physical pixels");
                    var deviceKey=message.TryGetProperty("deviceKey",out var dk) && dk.ValueKind==JsonValueKind.String?dk.GetString():null;
                    window.ApplyPreview(deviceKey,message.GetProperty("png").GetString() ?? "");
                    break;
                case "catalog":
                    window.ApplyIconCatalogResponse(message);
                    break;
                case "result":
                    if(!message.GetProperty("ok").GetBoolean())
                        window.ShowError(message.TryGetProperty("message",out var error)?error.GetString() ?? "": "Could not apply settings");
                    break;
                case "shutdown": _=window.ShutdownSettingsWindowAsync(); break;
                default: throw new InvalidDataException("Unknown settings message");
            }
        }
        catch(Exception error) when(error is JsonException or InvalidOperationException or KeyNotFoundException or InvalidDataException)
        { Console.Error.WriteLine(error); Disconnect(); }
    }

    private void Disconnect()
    {
        if(disposed) return;
        window?.DispatcherQueue.TryEnqueue(()=> { if(!disposed) _=window.ShutdownSettingsWindowAsync(); });
    }

    public void Dispose()
    {
        if(disposed) return;
        disposed=true;
        outgoing.Writer.TryComplete();
        cancel.Cancel();
        input?.Dispose();
        output?.Dispose();
    }
}
