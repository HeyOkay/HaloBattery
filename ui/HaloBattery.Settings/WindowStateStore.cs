using System.Text;
using System.Text.Json;

namespace HaloBattery.Settings;

// Width/height are DIPs; X/Y are physical virtual-screen coordinates. Keeping
// these units distinct preserves the apparent size across per-monitor DPI changes.
internal sealed record SettingsWindowState(int Width=1280,int Height=900,bool RailExpanded=true,
    int? X=null,int? Y=null,int Dpi=96,string Monitor="",bool Maximized=false);

/// <summary>Native window preferences, isolated from Python's config.json.</summary>
internal sealed class WindowStateStore
{
    internal const string FileName="settings-window.json";
    private readonly SemaphoreSlim writer=new(1,1);
    internal string FilePath { get; }

    internal WindowStateStore(string directory)=>FilePath=Path.Combine(Path.GetFullPath(directory),FileName);

    internal static bool TryParse(string json,out SettingsWindowState state)
    {
        state=new();
        try
        {
            using var document=JsonDocument.Parse(json,new JsonDocumentOptions { MaxDepth=4 });
            var root=document.RootElement;
            if(root.ValueKind!=JsonValueKind.Object) return false;
            var fields=root.EnumerateObject().ToArray();
            if(fields.Select(item=>item.Name).Distinct().Count()!=fields.Length ||
               !root.TryGetProperty("width",out var width) || width.ValueKind!=JsonValueKind.Number || !width.TryGetInt32(out var w) || w<320 || w>16384 ||
               !root.TryGetProperty("height",out var height) || height.ValueKind!=JsonValueKind.Number || !height.TryGetInt32(out var h) || h<240 || h>16384 ||
               !root.TryGetProperty("railExpanded",out var rail) || rail.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) return false;
            // Upgrade the original size-and-rail-only file without discarding it.
            if(fields.Length==3) { state=new(w,h,rail.GetBoolean());return true; }
            if(fields.Length!=8 ||
               !root.TryGetProperty("x",out var x) || !TryCoordinate(x,out var px) ||
               !root.TryGetProperty("y",out var y) || !TryCoordinate(y,out var py) || px.HasValue!=py.HasValue ||
               !root.TryGetProperty("dpi",out var dpi) || dpi.ValueKind!=JsonValueKind.Number || !dpi.TryGetInt32(out var d) || d<48 || d>768 ||
               !root.TryGetProperty("monitor",out var monitor) || monitor.ValueKind!=JsonValueKind.String ||
               !root.TryGetProperty("maximized",out var maximized) || maximized.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) return false;
            var name=monitor.GetString()!;
            if(name.Length>128 || name.Any(char.IsControl)) return false;
            state=new(w,h,rail.GetBoolean(),px,py,d,name,maximized.GetBoolean());
            return true;
        }
        catch(JsonException) { return false; }
    }

    private static bool TryCoordinate(JsonElement element,out int? value)
    {
        value=null;
        if(element.ValueKind==JsonValueKind.Null) return true;
        if(element.ValueKind!=JsonValueKind.Number || !element.TryGetInt32(out var number) || number is < -1000000 or > 1000000) return false;
        value=number;
        return true;
    }

    internal Task<SettingsWindowState> LoadAsync()=>Task.Run(()=>
    {
        try
        {
            if(new FileInfo(FilePath).Length>4096) return new SettingsWindowState();
            return TryParse(File.ReadAllText(FilePath,Encoding.UTF8),out var state)?state:new SettingsWindowState();
        }
        catch(Exception error) when(error is IOException or UnauthorizedAccessException) { return new SettingsWindowState(); }
    });

    internal async Task SaveAsync(SettingsWindowState state)
    {
        var json=JsonSerializer.Serialize(new { width=state.Width,height=state.Height,railExpanded=state.RailExpanded,
            x=state.X,y=state.Y,dpi=state.Dpi,monitor=state.Monitor,maximized=state.Maximized });
        if(!TryParse(json,out _)) throw new ArgumentOutOfRangeException(nameof(state));
        await writer.WaitAsync().ConfigureAwait(false);
        try
        {
            // All disk work, including durable flush, runs off the XAML thread.
            // Rename in the same directory replaces one complete file atomically.
            await Task.Run(()=>
            {
                var directory=Path.GetDirectoryName(FilePath)!;
                Directory.CreateDirectory(directory);
                var temporary=Path.Combine(directory,".settings-window-"+Guid.NewGuid().ToString("N")+".tmp");
                try
                {
                    var bytes=Encoding.UTF8.GetBytes(json);
                    using(var stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None))
                    {
                        stream.Write(bytes);
                        stream.Flush(flushToDisk:true);
                    }
                    File.Move(temporary,FilePath,overwrite:true);
                }
                finally
                {
                    try { File.Delete(temporary); }
                    catch(Exception error) when(error is IOException or UnauthorizedAccessException) { }
                }
            }).ConfigureAwait(false);
        }
        finally { writer.Release(); }
    }
}
