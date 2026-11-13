// Geometry from artwork/settings/lucide-*.svg, lucide-static v1.17.0, ISC.
// See artwork/settings/README.md and sources.json for attribution and provenance.
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
namespace HaloBattery.Settings;
public sealed partial class MainWindow
{
 private static readonly Dictionary<string,string[]> LucidePaths=new()
 {
  ["battery-medium"]=new[]{"M10 14v-4","M22 14v-4","M6 14v-4","M4.0,6.0 H16.0 A2.0,2.0 0 0 1 18.0,8.0 V16.0 A2.0,2.0 0 0 1 16.0,18.0 H4.0 A2.0,2.0 0 0 1 2.0,16.0 V8.0 A2.0,2.0 0 0 1 4.0,6.0 Z"},
  ["bell"]=new[]{"M10.268 21a2 2 0 0 0 3.464 0","M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"},
  ["check"]=new[]{"M20 6 9 17l-5-5"},
  ["chevron-down"]=new[]{"m6 9 6 6 6-6"},
  ["chevron-right"]=new[]{"m9 18 6-6-6-6"},
  ["download"]=new[]{"M12 15V3","M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4","m7 10 5 5 5-5"},
  ["external-link"]=new[]{"M15 3h6v6","M10 14 21 3","M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"},
  ["eye-off"]=new[]{"M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49","M14.084 14.158a3 3 0 0 1-4.242-4.242","M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143","m2 2 20 20"},
  ["eye"]=new[]{"M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0","M9.0,12.0 A3.0,3.0 0 1 0 15.0,12.0 A3.0,3.0 0 1 0 9.0,12.0"},
  ["folder-open"]=new[]{"m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"},
  ["info"]=new[]{"M2.0,12.0 A10.0,10.0 0 1 0 22.0,12.0 A10.0,10.0 0 1 0 2.0,12.0","M12 16v-4","M12 8h.01"},
  ["maximize-2"]=new[]{"M15 3h6v6","m21 3-7 7","m3 21 7-7","M9 21H3v-6"},
  ["menu"]=new[]{"M4 5h16","M4 12h16","M4 19h16"},
  ["minimize-2"]=new[]{"m14 10 7-7","M20 10h-6V4","m3 21 7-7","M4 14h6v6"},
  ["minus"]=new[]{"M5 12h14"},
  ["mouse"]=new[]{"M12.0,2.0 H12.0 A7.0,7.0 0 0 1 19.0,9.0 V15.0 A7.0,7.0 0 0 1 12.0,22.0 H12.0 A7.0,7.0 0 0 1 5.0,15.0 V9.0 A7.0,7.0 0 0 1 12.0,2.0 Z","M12 6v4"},
  ["palette"]=new[]{"M12 22a1 1 0 0 1 0-20 10 9 0 0 1 10 9 5 5 0 0 1-5 5h-2.25a1.75 1.75 0 0 0-1.4 2.8l.3.4a1.75 1.75 0 0 1-1.4 2.8z","M13.0,6.5 A0.5,0.5 0 1 0 14.0,6.5 A0.5,0.5 0 1 0 13.0,6.5","M17.0,10.5 A0.5,0.5 0 1 0 18.0,10.5 A0.5,0.5 0 1 0 17.0,10.5","M6.0,12.5 A0.5,0.5 0 1 0 7.0,12.5 A0.5,0.5 0 1 0 6.0,12.5","M8.0,7.5 A0.5,0.5 0 1 0 9.0,7.5 A0.5,0.5 0 1 0 8.0,7.5"},
  ["pencil"]=new[]{"M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z","m15 5 4 4"},
  ["plus"]=new[]{"M5 12h14","M12 5v14"},
  ["refresh-cw"]=new[]{"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8","M21 3v5h-5","M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16","M8 16H3v5"},
  ["search"]=new[]{"m21 21-4.34-4.34","M3.0,11.0 A8.0,8.0 0 1 0 19.0,11.0 A8.0,8.0 0 1 0 3.0,11.0"},
  ["settings"]=new[]{"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915","M9.0,12.0 A3.0,3.0 0 1 0 15.0,12.0 A3.0,3.0 0 1 0 9.0,12.0"},
  ["sliders-horizontal"]=new[]{"M10 5H3","M12 19H3","M14 3v4","M16 17v4","M21 12h-9","M21 19h-5","M21 5h-7","M8 10v4","M8 12H3"},
  ["wrench"]=new[]{"M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"},
  ["x"]=new[]{"M18 6 6 18","m6 6 12 12"},
 };
 internal UIElement LucideIcon(string name,double size=20,string? strokeColor=null,double strokeThickness=1.5)
 {
  name=name.Replace("lucide-","");
  if(!LucidePaths.TryGetValue(name,out var paths))paths=LucidePaths["info"];
  var stroke=strokeColor??(dark?"#FFFFFF":"#1B1B1B");
  var geometry=string.Concat(paths.Select(d=>FormattableString.Invariant($"<Path Data='{d}' Stroke='{stroke}' StrokeThickness='{strokeThickness}' StrokeLineJoin='Round' StrokeStartLineCap='Round' StrokeEndLineCap='Round'/>")));
  return (UIElement)XamlReader.Load(FormattableString.Invariant($"<Viewbox xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Width='{size}' Height='{size}' VerticalAlignment='Center'><Canvas Width='24' Height='24'>{geometry}</Canvas></Viewbox>"));
 }
 private static string NavigationIcon(string id)=>id switch
 {"general"=>"sliders-horizontal","devices"=>"mouse","icons"=>"palette","notifications"=>"bell","advanced"=>"wrench","about"=>"info",_=>"sliders-horizontal"};
}
