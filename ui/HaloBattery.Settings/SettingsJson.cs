using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace HaloBattery.Settings;

// Every DTO is known at compile time. Outgoing protocol values are a bounded
// primitive/JSON union, never arbitrary CLR objects or reflected anonymous types.
internal static class SettingsJson
{
    internal static readonly SettingsJsonContext Context = new(new JsonSerializerOptions
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        MaxDepth = 32
    });
    internal static JsonSerializerOptions Options => Context.Options;

    internal static JsonObject Object(params (string Name, object? Value)[] fields)
    {
        var result = new JsonObject();
        foreach (var (name, value) in fields) result.Add(name, Node(value));
        return result;
    }

    internal static JsonNode? Node(object? value) => value switch
    {
        null => null,
        JsonElement element => JsonNode.Parse(element.GetRawText(), documentOptions: new() { MaxDepth = 32 }),
        JsonNode node => node.DeepClone(),
        string text => JsonValue.Create(text),
        bool boolean => JsonValue.Create(boolean),
        int number => JsonValue.Create(number),
        long number => JsonValue.Create(number),
        double number => JsonValue.Create(number),
        float number => JsonValue.Create(number),
        decimal number => JsonValue.Create(number),
        byte number => JsonValue.Create(number),
        sbyte number => JsonValue.Create(number),
        short number => JsonValue.Create(number),
        ushort number => JsonValue.Create(number),
        uint number => JsonValue.Create(number),
        ulong number => JsonValue.Create(number),
        _ => throw new NotSupportedException("Settings JSON value must be a primitive or explicit JSON value.")
    };

    internal static JsonElement Element(object? value) =>
        JsonSerializer.SerializeToElement(Node(value), Context.JsonNode);

    internal static string Choices(IEnumerable<(JsonElement Value, string Label)> choices)
    {
        var result = new JsonArray();
        foreach (var choice in choices) result.Add((JsonNode)Object(("value", choice.Value), ("label", choice.Label)));
        return result.ToJsonString();
    }

    internal static string Navigation(bool dark, string language, IEnumerable<UiPage> pages)
    {
        var items = new JsonArray();
        foreach (var page in pages) items.Add((JsonNode)Object(("Id", page.Id), ("Title", page.Title)));
        return Object(("dark", dark), ("language", language), ("pages", items)).ToJsonString();
    }

    internal static string WindowState(SettingsWindowState state) => Object(
        ("width", state.Width), ("height", state.Height), ("railExpanded", state.RailExpanded),
        ("x", state.X), ("y", state.Y), ("dpi", state.Dpi), ("monitor", state.Monitor),
        ("maximized", state.Maximized), ("page", state.Page), ("selectedDeviceKey", state.SelectedDeviceKey),
        ("scrollOffset", state.ScrollOffset), ("expandedCards", state.ExpandedCards)).ToJsonString();
}

[JsonSourceGenerationOptions(PropertyNameCaseInsensitive = true,
    PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase, MaxDepth = 32,
    GenerationMode = JsonSourceGenerationMode.Metadata)]
[JsonSerializable(typeof(UiState))]
[JsonSerializable(typeof(JsonNode))]
internal partial class SettingsJsonContext : JsonSerializerContext { }
