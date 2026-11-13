using System.Text;
using System.Text.Json;
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
    public string Pictogram { get; set; } = "";
    public bool Controller { get; set; }
    public string Brand { get; set; } = "default";
    public bool BrandColor { get; set; }
    public int? LowOverride { get; set; }
    public bool Hidden { get; set; }
    public int? Level { get; set; }
    public bool? Charging { get; set; }
    public bool Online { get; set; }
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
    public static UiOption Create(object? value,string label) => new() { Value=JsonSerializer.SerializeToElement(value), Label=label };
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
public sealed class SettingsBridge : IDisposable
{
    public static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive=true,
        PropertyNamingPolicy=JsonNamingPolicy.CamelCase, MaxDepth=32 };
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
    public List<string> FixtureCommands { get; } = new();

    public void Attach(MainWindow owner)
    {
        window=owner;
        owner.Closed+=(_,_)=>Dispose();
        var args=Environment.GetCommandLineArgs();
        var at=Array.IndexOf(args,"--fixture-state");
        if(at>=0 && at+1<args.Length)
        {
            fixture=true;
            var path=Path.GetFullPath(args[at+1]);
            if(new FileInfo(path).Length>1024*1024) throw new InvalidDataException("Fixture exceeds protocol limit");
            var state=JsonSerializer.Deserialize<UiState>(File.ReadAllText(path,Encoding.UTF8),Json)
                ?? throw new InvalidDataException("Invalid fixture state");
            owner.ApplyState(state);
            owner.StartFixtureVerification(path);
            return;
        }
        input=new StreamReader(Console.OpenStandardInput(),new UTF8Encoding(false,true));
        output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false)) { AutoFlush=false };
        _=Task.Run(WriteLoop);
        _=Task.Run(ReadLoop);
        Enqueue(new { type="hello",protocol=1,uiVersion="1" });
    }

    public long SendCommand(string name,object args)
    {
        var id=Interlocked.Increment(ref nextId);
        if(fixture)
        {
            FixtureCommands.Add(JsonSerializer.Serialize(new { type="command",protocol=1,id,name,args },Json));
            return id;
        }
        if(!Enqueue(new { type="command",protocol=1,id,name,args }))
            window?.ShowError("The settings connection is busy. Please try again.");
        return id;
    }

    public bool RequestPreview(string? deviceKey)
    {
        if(fixture || disposed || Interlocked.Read(ref pendingPreview)!=0) return false;
        var id=Interlocked.Increment(ref nextId);
        if(Interlocked.CompareExchange(ref pendingPreview,id,0)!=0) return false;
        if(Enqueue(new { type="preview",id,deviceKey })) return true;
        Interlocked.Exchange(ref pendingPreview,0);
        return false;
    }

    private bool Enqueue(object message) => !disposed && outgoing.Writer.TryWrite(message);

    private async Task WriteLoop()
    {
        try
        {
            await foreach(var message in outgoing.Reader.ReadAllAsync(cancel.Token))
            {
                await output!.WriteLineAsync(JsonSerializer.Serialize(message,Json));
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
                if(Encoding.UTF8.GetByteCount(line)>1024*1024) throw new InvalidDataException("Oversized settings message");
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
                    var state=message.Deserialize<UiState>(Json) ?? throw new InvalidDataException("Missing state");
                    if(state.Protocol!=1 || session.Length==0 || state.SessionId!=session) throw new InvalidDataException("Settings session mismatch");
                    if(state.Revision<lastRevision) return;
                    lastRevision=state.Revision;
                    window.ApplyState(state);
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
                case "result":
                    if(!message.GetProperty("ok").GetBoolean())
                        window.ShowError(message.TryGetProperty("message",out var error)?error.GetString() ?? "": "Could not apply settings");
                    break;
                case "shutdown": window.Close(); break;
                default: throw new InvalidDataException("Unknown settings message");
            }
        }
        catch(Exception error) when(error is JsonException or InvalidOperationException or KeyNotFoundException or InvalidDataException)
        { Console.Error.WriteLine(error); Disconnect(); }
    }

    private void Disconnect()
    {
        if(disposed) return;
        window?.DispatcherQueue.TryEnqueue(()=> { if(!disposed) window.Close(); });
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
