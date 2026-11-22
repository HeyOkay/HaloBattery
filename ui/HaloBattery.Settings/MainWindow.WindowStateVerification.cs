using System.Text.Json;
using Microsoft.UI.Windowing;
using Windows.Graphics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyWindowState(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new { name,passed });
            if(!passed) throw new InvalidOperationException("Fixture check failed: "+name);
        }
        await VerifyWindowStateStore(checks);
        VerifyWindowGeometryMath(checks);
        Check("fixture native profile is isolated",windowStateStore!.FilePath==Path.Combine(Path.GetFullPath(Arg("--verify-dir")),WindowStateStore.FileName));
        var initial=initialWindowState;
        var scale=GetDpiForWindow(hwnd)/96.0;
        var work=DisplayArea.GetFromWindowId(AppWindow.Id,DisplayAreaFallback.Nearest).WorkArea;
        Check("initial rail restored from native file",expanded==initial.RailExpanded && rail.Width.Value==(expanded?228:64));

        var expectedWidth=Math.Min(work.Width,Math.Max((int)Math.Ceiling(MinimumWindowWidthDip*scale),(int)Math.Round(initial.Width*scale)));
        var expectedHeight=Math.Min(work.Height,Math.Max((int)Math.Ceiling(560*scale),(int)Math.Round(initial.Height*scale)));
        if(initial.Maximized && AppWindow.Presenter is OverlappedPresenter initialPresenter)
        {
            Check("initial maximized intent restored",initialPresenter.State==OverlappedPresenterState.Maximized);
            initialPresenter.Restore();
            await Task.Delay(200);
        }
        Check("restored window fits current work area",AppWindow.Size.Width<=work.Width && AppWindow.Size.Height<=work.Height);
        Check("initial dimensions restored as DIPs",Math.Abs(AppWindow.Size.Width-expectedWidth)<=1 && Math.Abs(AppWindow.Size.Height-expectedHeight)<=1);
        if(initial.X is not null && initial.Y is not null)
        {
            var bounds=ConstrainWindowBounds(initial,work,scale,MinimumWindowWidthDip,AppWindow.Position);
            Check("initial physical position restored and clamped",AppWindow.Position.X==bounds.X && AppWindow.Position.Y==bounds.Y);
        }

        var stale=JsonSerializer.Deserialize<UiState>(JsonSerializer.Serialize(State,SettingsBridge.Json),SettingsBridge.Json)!;
        AppWindow.Resize(new SizeInt32(Math.Min(work.Width,(int)Math.Round(1040*scale)),Math.Min(work.Height,(int)Math.Round(720*scale))));
        await Task.Delay(100);
        CaptureWindowState();
        var normalSize=AppWindow.Size;
        var moved=new PointInt32(work.X+Math.Min(37,work.Width-normalSize.Width),work.Y+Math.Min(43,work.Height-normalSize.Height));
        AppWindow.Move(moved);
        await Task.Delay(100);
        CaptureWindowState();
        Check("position-only movement captured in physical pixels",normalWindowState.X==AppWindow.Position.X && normalWindowState.Y==AppWindow.Position.Y);
        Check("current monitor and DPI captured",normalWindowState.Monitor==CurrentWindowMonitor().Name && normalWindowState.Dpi==(int)GetDpiForWindow(hwnd));
        Check("normal size captured as DIPs",normalWindowState.Width==(int)Math.Round(normalSize.Width/scale) && normalWindowState.Height==(int)Math.Round(normalSize.Height/scale));
        ToggleRail();
        await Task.Delay(350);
        var targetExpanded=!initial.RailExpanded;
        Check("rail toggled to stable target",expanded==targetExpanded && rail.Width.Value==(expanded?228:64));
        ApplyState(stale);
        Check("periodic Python snapshot preserves native size and rail",AppWindow.Size.Width==normalSize.Width && AppWindow.Size.Height==normalSize.Height && expanded==targetExpanded);

        if(AppWindow.Presenter is OverlappedPresenter presenter)
        {
            var normal=normalWindowState;
            presenter.Maximize();
            await Task.Delay(200);
            CaptureWindowState();
            Check("maximized size does not replace normal bounds",normalWindowState==(normal with { Maximized=true }));
            presenter.Minimize();
            await Task.Delay(100);
            CaptureWindowState();
            Check("minimized size preserves normal bounds and maximize intent",normalWindowState==(normal with { Maximized=true }));
            presenter.Restore();
            await Task.Delay(200);
        }
        windowStateTimer.Stop();
        await FlushWindowStateAsync();
        Check("native state saved",await windowStateStore.LoadAsync()==normalWindowState);

        // Start IO, then change state before its UI continuation can complete.
        ToggleRail();
        windowStateTimer.Stop();
        var firstWrite=SaveWindowStateAsync();
        ToggleRail();
        windowStateTimer.Stop();
        var latest=normalWindowState;
        await firstWrite;
        await FlushWindowStateAsync();
        Check("latest change during IO wins",await windowStateStore.LoadAsync()==latest && savedWindowStateVersion==windowStateVersion);

        ToggleRail();
        windowStateTimer.Stop();
        var beforeQuit=Bridge.FixtureCommands.Count;
        await SendUiCommandAsync("quit");
        var exitCommands=Bridge.FixtureCommands.Skip(beforeQuit).Select(wire=>JsonDocument.Parse(wire)).ToArray();
        try
        {
            Check("Exit sends only Python quit",exitCommands.Length==1 && exitCommands[0].RootElement.GetProperty("name").GetString()=="quit");
            Check("Exit saves native geometry before quit",await windowStateStore.LoadAsync()==normalWindowState);
        }
        finally { foreach(var command in exitCommands) command.Dispose(); }
    }

    private async Task VerifyWindowStateStore(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new { name,passed });
            if(!passed) throw new InvalidOperationException("Fixture check failed: "+name);
        }
        var directory=Path.Combine(Path.GetFullPath(Arg("--verify-dir")),"native-store-tests-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var store=new WindowStateStore(directory);
        Check("missing native file uses defaults",await store.LoadAsync()==new SettingsWindowState());
        var invalid=new[] {
            "", "{", "null", "[]", "{}", "{\"width\":1000,\"height\":700}",
            "{\"width\":true,\"height\":700,\"railExpanded\":true}",
            "{\"width\":\"1000\",\"height\":700,\"railExpanded\":true}",
            "{\"width\":1000.5,\"height\":700,\"railExpanded\":true}",
            "{\"width\":319,\"height\":700,\"railExpanded\":true}",
            "{\"width\":16385,\"height\":700,\"railExpanded\":true}",
            "{\"width\":1000,\"height\":239,\"railExpanded\":true}",
            "{\"width\":1000,\"height\":16385,\"railExpanded\":true}",
            "{\"width\":1000,\"height\":700,\"railExpanded\":1}",
            "{\"width\":1000,\"height\":700,\"railExpanded\":true,\"extra\":0}",
            "{\"width\":1000,\"width\":1001,\"height\":700,\"railExpanded\":true}" };
        Check("malformed and invalid native values rejected",invalid.All(json=>!WindowStateStore.TryParse(json,out _)));
        foreach(var json in invalid)
        {
            await File.WriteAllTextAsync(store.FilePath,json);
            if(await store.LoadAsync()!=new SettingsWindowState()) throw new InvalidOperationException("Invalid file did not use defaults.");
        }
        Check("invalid native files fall back safely",true);
        Check("native bounds accepted",WindowStateStore.TryParse("{\"width\":320,\"height\":240,\"railExpanded\":false}",out _) &&
            WindowStateStore.TryParse("{\"width\":16384,\"height\":16384,\"railExpanded\":true}",out _));
        Check("legacy three-field state migrates without losing preferences",WindowStateStore.TryParse("{\"width\":1010,\"height\":710,\"railExpanded\":false}",out var legacy) &&
            legacy==new SettingsWindowState(1010,710,false));
        var positioned=new SettingsWindowState(1000,700,false,-1800,-40,144,"\\\\.\\DISPLAY2",true);
        await store.SaveAsync(positioned);
        Check("negative physical coordinates, DPI, monitor and maximize state round trip",await store.LoadAsync()==positioned);
        var extended=await File.ReadAllTextAsync(store.FilePath);
        var invalidExtended=new[] {
            extended.Replace("\"x\":-1800","\"x\":\"-1800\""),
            extended.Replace("\"x\":-1800","\"x\":null"),
            extended.Replace("\"y\":-40","\"y\":-1000001"),
            extended.Replace("\"dpi\":144","\"dpi\":0"),
            extended.Replace("\"dpi\":144","\"dpi\":144.5"),
            extended.Replace("\"maximized\":true","\"maximized\":1"),
            extended.Replace("\"monitor\":", "\"extra\":0,\"monitor\":"),
            extended.Replace("\"width\":1000", "\"width\":1000,\"width\":1001") };
        Check("invalid extended preference types and duplicate fields rejected",invalidExtended.All(json=>!WindowStateStore.TryParse(json,out _)));
        using(var legacyDocument=JsonDocument.Parse(extended))
        {
            var legacyFields=legacyDocument.RootElement.EnumerateObject().Where(item=>item.Name is not ("page" or "selectedDeviceKey" or "scrollOffset" or "expandedCards"))
                .ToDictionary(item=>item.Name,item=>item.Value.Clone());
            Check("legacy eight-field state migrates with default view",WindowStateStore.TryParse(JsonSerializer.Serialize(legacyFields),out var eight) && eight==positioned);
        }
        var withView=positioned with { Page="advanced",SelectedDeviceKey="hid:fixture/device",ScrollOffset=329.25,ExpandedCards="0101" };
        await store.SaveAsync(withView);
        Check("page device scroll and expanded cards round trip",await store.LoadAsync()==withView);
        var viewJson=await File.ReadAllTextAsync(store.FilePath);
        var invalidViews=new[] {
            viewJson.Replace("\"page\":\"advanced\"","\"page\":1"),
            viewJson.Replace("\"page\":\"advanced\"","\"page\":\"unknown\""),
            viewJson.Replace("\"selectedDeviceKey\":\"hid:fixture/device\"","\"selectedDeviceKey\":true"),
            viewJson.Replace("\"selectedDeviceKey\":\"hid:fixture/device\"","\"selectedDeviceKey\":\"\""),
            viewJson.Replace("\"scrollOffset\":329.25","\"scrollOffset\":\"329.25\""),
            viewJson.Replace("\"scrollOffset\":329.25","\"scrollOffset\":-1"),
            viewJson.Replace("\"scrollOffset\":329.25","\"scrollOffset\":10000001"),
            viewJson.Replace("\"scrollOffset\":329.25","\"scrollOffset\":1e999"),
            viewJson.Replace("\"expandedCards\":\"0101\"","\"expandedCards\":[false,true]"),
            viewJson.Replace("\"expandedCards\":\"0101\"","\"expandedCards\":\"0102\""),
            viewJson.Replace("\"expandedCards\":\"0101\"","\"expandedCards\":\""+new string('1',257)+"\""),
            viewJson.Replace("\"selectedDeviceKey\":\"hid:fixture/device\"","\"selectedDeviceKey\":\""+new string('d',1025)+"\""),
            viewJson.Replace("\"page\":\"advanced\"","\"page\":\"advanced\",\"page\":\"general\"") };
        Check("invalid view preference types bounds and duplicates rejected",invalidViews.All(json=>!WindowStateStore.TryParse(json,out _)));
        var boundaryView=withView with { SelectedDeviceKey=null,ScrollOffset=10000000,ExpandedCards=new string('1',256) };
        await store.SaveAsync(boundaryView);
        Check("view preference boundary values round trip",await store.LoadAsync()==boundaryView);
        var first=new SettingsWindowState(1000,700,false);
        var latest=new SettingsWindowState(1100,750,true);
        await store.SaveAsync(first);
        Check("native file round trips",await store.LoadAsync()==first);
        await Task.WhenAll(store.SaveAsync(first),store.SaveAsync(latest));
        Check("serialized atomic writes keep latest complete file",await store.LoadAsync()==latest && !Directory.EnumerateFiles(directory,"*.tmp").Any());
        var invalidRejected=false;
        try { await store.SaveAsync(new(0,700,false)); }
        catch(ArgumentOutOfRangeException) { invalidRejected=true; }
        Check("invalid write preserves previous native file",invalidRejected && await store.LoadAsync()==latest);
        var blocked=Path.Combine(directory,"blocked-directory");
        await File.WriteAllTextAsync(blocked,"preserve");
        var failed=false;
        try { await new WindowStateStore(blocked).SaveAsync(first); }
        catch(Exception error) when(error is IOException or UnauthorizedAccessException) { failed=true; }
        Check("directory write failure is reported without overwrite",failed && await File.ReadAllTextAsync(blocked)=="preserve");
        var blockedTarget=Path.Combine(directory,"blocked-target");
        Directory.CreateDirectory(Path.Combine(blockedTarget,WindowStateStore.FileName));
        failed=false;
        try { await new WindowStateStore(blockedTarget).SaveAsync(first); }
        catch(Exception error) when(error is IOException or UnauthorizedAccessException) { failed=true; }
        Check("failed atomic replacement cleans temporary file",failed && !Directory.EnumerateFiles(blockedTarget,"*.tmp").Any() && Directory.Exists(Path.Combine(blockedTarget,WindowStateStore.FileName)));
    }

    private static void VerifyWindowGeometryMath(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new { name,passed });
            if(!passed) throw new InvalidOperationException("Fixture check failed: "+name);
        }
        var negativeWork=new RectInt32(-1920,-100,1920,1080);
        var negative=ConstrainWindowBounds(new(1000,700,false,-1800,-40),negativeWork,1,800,new PointInt32());
        Check("negative monitor coordinates remain physical",negative.X==-1800 && negative.Y==-40 && negative.Width==1000 && negative.Height==700);
        var currentWork=new RectInt32(100,40,1920,1080);
        var removed=ConstrainWindowBounds(new(1000,700,true,-3800,-1400),currentWork,1,800,new PointInt32());
        Check("removed monitor moves complete window into available work area",removed.X==100 && removed.Y==40);
        var scaled=ConstrainWindowBounds(new(1000,700,true,150,80,96),currentWork,1.25,800,new PointInt32());
        Check("changed monitor DPI scales DIP size but preserves physical position",scaled.X==150 && scaled.Y==80 && scaled.Width==1250 && scaled.Height==875);
        var oversized=ConstrainWindowBounds(new(16384,16384,true,999999,999999),currentWork,2,800,new PointInt32());
        Check("oversized and off-screen bounds clamp to complete work area",oversized.X==100 && oversized.Y==40 && oversized.Width==1920 && oversized.Height==1080);
        var legacy=ConstrainWindowBounds(new(1000,700,false),currentWork,1,800,new PointInt32(180,110));
        Check("legacy size-only file retains valid default placement",legacy.X==180 && legacy.Y==110);
        var smallWork=new RectInt32(-800,50,640,400);
        var small=ConstrainWindowBounds(new(),smallWork,1.5,800,new PointInt32());
        Check("small display work area overrides minimum size safely",small.X==-800 && small.Y==50 && small.Width==640 && small.Height==400);
    }
    private async Task VerifyNativeCloseAsync(string destination,List<object> checks)
    {
        if(AppWindow.Presenter is OverlappedPresenter presenter && presenter.State!=OverlappedPresenterState.Restored)
        {
            presenter.Restore();
            await Task.Delay(150);
        }
        var scale=GetDpiForWindow(hwnd)/96.0;
        var work=DisplayArea.GetFromWindowId(AppWindow.Id,DisplayAreaFallback.Nearest).WorkArea;
        // No debounce delay: immediate close must capture both final changes.
        AppWindow.Resize(new SizeInt32(Math.Min(work.Width,(int)Math.Round(1080*scale)),Math.Min(work.Height,(int)Math.Round(760*scale))));
        ToggleRail();
        var before=Bridge.FixtureCommands.Count;
        SettingsWindowState? expected=null;
        Closed+=(_,_)=>
        {
            var passed=WindowStateStore.TryParse(File.ReadAllText(windowStateStore!.FilePath),out var actual) && actual==expected;
            checks.Add(new { name="immediate close saves final native size and rail",passed });
            var noIpc=Bridge.FixtureCommands.Count==before;
            checks.Add(new { name="close saves locally without Python command",passed=noIpc });
            WriteVerificationReport(destination,new { complete=passed && noIpc,scope="window-state",checks,fixtureCommands=Bridge.FixtureCommands,nativeFile=windowStateStore.FilePath });
        };
        Close();
        expected=normalWindowState;
    }
}
