using System.Text.Json;
using HaloBattery.Settings;

var framework=System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription;
if(framework!=".NET 10.0.15") throw new Exception("Wrong security test runtime: "+framework);
Console.Error.WriteLine("Security parity runtime: "+framework);

// Exercise the production bridge/DTO/store with JSON reflection disabled.
// Goldens were independently captured from the pre-sourcegen implementation.
var rootIndex = Array.IndexOf(args, "--test-root");
if (rootIndex < 0 || rootIndex + 1 >= args.Length) throw new Exception("Test root required");
var root = Path.GetFullPath(args[rootIndex + 1]);
Directory.CreateDirectory(root);
if (args.Contains("--pipe-test"))
{
    using var pipe = new SettingsBridge();
    await pipe.InitializeAsync();
    pipe.SendVisibility(true, long.MaxValue);
    pipe.SendVisibility(false);
    pipe.SendCommand("setGlobal", SettingsJson.Object(("key", "value"), ("value", null)));
    pipe.RequestCatalog("details", "fixture:一", "", 0, 1, "icon:test");
    if (!pipe.RequestPreview(null)) throw new Exception("Preview was not queued");
    pipe.SendClosing();
    await pipe.FlushAsync();
    return;
}
var fixture = """
{"type":"state","protocol":1,"revision":9223372036854775806,"sessionId":"test-session","version":"1.17.0","language":"ja","dataDir":"D:\\fixture","statusPath":"D:\\fixture\\status.json","selectedDeviceKey":null,"page":"devices","labels":{"settings.general":"全般 <>& \"","settings.devices":"Устройства"},"settings":{"flag":true,"low":0,"high":100,"number":1.25,"nil":null,"array":[1,"é",false]},"autostart":true,"iconLightTaskbar":false,"devices":[{"key":"fixture:one","name":"鼠标 😀","level":0,"charging":null,"online":true},{"key":"fixture:two","level":100,"charging":false,"customIconUnavailable":true}],"providers":[{"name":"fixture","label":"Test","enabled":false}],"options":{"languages":[{"value":"ja","label":"日本語"}],"intervals":[{"value":1.25,"label":"Fraction"}],"thresholds":[{"value":null,"label":"Default"}]},"update":{"version":"2.0","progress":null,"installOk":false,"status":"idle","message":""}}
""";
var fixtureIndex = Array.IndexOf(args, "--fixture-state");
if (fixtureIndex < 0 || fixtureIndex + 1 >= args.Length) throw new Exception("Fixture path required");
if (!Path.GetFullPath(args[fixtureIndex + 1]).StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))
    throw new Exception("Fixture must stay in the test-owned directory");
File.WriteAllText(args[fixtureIndex + 1], fixture);
using var bridge = new SettingsBridge();
var state = await bridge.InitializeAsync();
var actual = new SortedDictionary<string, string>(StringComparer.Ordinal);
void Add(string key, string value) => actual.Add(key, value);
if (JsonSerializer.IsReflectionEnabledByDefault) throw new Exception("Checks must disable JSON reflection");
string Serialize(object? value) => SettingsJson.Node(value)?.ToJsonString(SettingsBridge.Json) ?? "null";
Add("state", JsonSerializer.Serialize(state, SettingsJson.Context.UiState));
object?[] primitives = { null, true, false, 0, 100, -12, int.MaxValue, long.MaxValue, ulong.MaxValue,
    (byte)255, (sbyte)-128, (short)-32768, (ushort)65535, uint.MaxValue,
    0.0, -0.0, 1.25, 1e-7, 1e100, 0.125f, 1.2300m, "é 😀 <>& \"\\\n\t" };
for (int i=0; i<primitives.Length; i++)
{
    Add("value-"+i, Serialize(primitives[i]));
    Add("option-"+i, UiOption.Create(primitives[i], "label").Value.GetRawText());
}
using var nested = JsonDocument.Parse("{\"x\":[null,true,1.20,\"é\"],\"empty\":{}}");
Add("element", Serialize(nested.RootElement));
Add("empty-state", SerializeState(UiState.Empty));
string SerializeState(UiState value)
{
    return JsonSerializer.Serialize(value, SettingsJson.Context.UiState);
}
foreach (var value in primitives.Take(8))
{
    bridge.SendCommand("setGlobal", SettingsJson.Object(("key", "setting"), ("value", value)));
}
bridge.SendCommand("setDevice", SettingsJson.Object(("deviceKey", "fixture:one"), ("field", "customIcon"), ("value", null)));
bridge.SendCommand("selectDevice", SettingsJson.Object(("deviceKey", null)));
bridge.SendCommand("setProvider", SettingsJson.Object(("name", "fixture"), ("enabled", false)));
bridge.SendCommand("setIconContent", SettingsJson.Object(("mode", "percent")));
bridge.SendCommand("quit", SettingsJson.Object());
bridge.RequestCatalog("search", null, "é <>&", 0, 40);
bridge.RequestCatalog("details", "fixture:one", "", 40, 1, "catalog:test");
for (int i=0; i<bridge.FixtureCommands.Count; i++) Add("command-"+i, bridge.FixtureCommands[i]);
// Remaining envelope shapes, with IDs at the signed 64-bit boundary.
Add("hello", SettingsJson.Object(("type", "hello"), ("protocol", 1), ("uiVersion", "1")).ToJsonString(SettingsBridge.Json));
Add("visible-id", SettingsJson.Object(("type", "visibility"), ("visible", true), ("requestId", long.MaxValue)).ToJsonString(SettingsBridge.Json));
Add("visible", SettingsJson.Object(("type", "visibility"), ("visible", false)).ToJsonString(SettingsBridge.Json));
Add("closing", SettingsJson.Object(("type", "closing")).ToJsonString(SettingsBridge.Json));
Add("preview", SettingsJson.Object(("type", "preview"), ("id", long.MaxValue), ("deviceKey", null)).ToJsonString(SettingsBridge.Json));
Add("navigation", SettingsJson.Navigation(true, "ja", state.Pages));
Add("choices", SettingsJson.Choices(state.Options.Intervals.Select(o=>(o.Value,o.Label))));
var states = new[] { new SettingsWindowState(), new SettingsWindowState(1280,900,false,-100,0,144,"é ",true,"devices","fixture:一",1.25,"101"),
    new SettingsWindowState(320,240,true,1000000,-1000000,768,"",false,"icons",null,10000000,""),
    new SettingsWindowState(16384,16384,false,null,null,48,"",false,"general",null,0,"") };
for (int i=0; i<states.Length; i++)
{
    var store = new WindowStateStore(Path.Combine(root,"window-"+i));
    await store.SaveAsync(states[i]);
    var saved = File.ReadAllText(store.FilePath);
    Add("window-"+i, saved);
    if (!WindowStateStore.TryParse(saved,out var parsed) || parsed!=states[i]) throw new Exception("Window roundtrip failed");
}
var invalid = new[] { "{}", "[]", "null", "{\"width\":true}",
    "{\"width\":320,\"width\":321,\"height\":240,\"railExpanded\":true}",
    "{\"width\":319,\"height\":240,\"railExpanded\":true}" };
foreach (var value in invalid) if (WindowStateStore.TryParse(value,out _)) throw new Exception("Invalid window state accepted");
if (!WindowStateStore.TryParse("{\"width\":1280,\"height\":900,\"railExpanded\":true}",out _)) throw new Exception("Legacy state refused");
// Case-insensitive typed deserialization remains supported.
var mixed = JsonSerializer.Deserialize("{\"VERSION\":\"mixed\",\"devices\":[{\"level\":100}]}",SettingsJson.Context.UiState)!;
bool refused=false; try { SettingsJson.Node(new object()); } catch(NotSupportedException) { refused=true; }
if (!refused) throw new Exception("Unknown CLR shape must fail closed");
using var inputNode = JsonDocument.Parse("[true,null,1]");
var child = SettingsJson.Object(("values", inputNode.RootElement));
var one = SettingsJson.Object(("args", child));
var two = SettingsJson.Object(("args", child));
child["changed"] = true;
if (one.ToJsonString()!=two.ToJsonString() || one.ToJsonString().Contains("changed")) throw new Exception("Node snapshot aliasing");
if (mixed.Version!="mixed" || mixed.Devices[0].Level!=100) throw new Exception("Case-insensitive DTO mismatch");
foreach (var bad in new[] { "[]", "{\"devices\":[{\"level\":\"bad\"}]}", "{\"autostart\":12}",
    "{\"revision\":9223372036854775808}", "{\"settings\":{\"tooDeep\":"+new string('[',40)+"0"+new string(']',40)+"}}" })
{
    bool rejected=false;
    try
    {
        JsonSerializer.Deserialize(bad,SettingsJson.Context.UiState);
    }
    catch(JsonException) { rejected=true; }
    if (!rejected) throw new Exception("Malformed state accepted: "+bad);
}
using var golden = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "wire-goldens.json")));
var expected = golden.RootElement.EnumerateObject().ToDictionary(item => item.Name, item => item.Value.GetString());
if (expected.Count!=actual.Count) throw new Exception("Vector count mismatch");
foreach (var (key,value) in actual) if (!expected.TryGetValue(key,out var old) || old!=value)
    throw new Exception($"Byte mismatch {key}\nOLD {old}\nNEW {value}");
Console.WriteLine($"PASS: {actual.Count} byte-exact baseline vectors; reflection disabled; fixture bridge, state/window roundtrips, legacy validation and primitive/JSON snapshots");
