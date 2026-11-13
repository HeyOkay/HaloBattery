using System.Text.Json;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace HaloBattery.Settings;
public sealed partial class MainWindow
{
 private string pageFadeMode="content";
 private readonly List<UIElement> entryOpacityTargets=new();
 private IEnumerable<UIElement> GetEntryOpacityTargets(Page entered)
 {
  if(pageFadeMode=="full")return new UIElement[]{entered};
  var targets=new List<UIElement>();
  void Visit(UIElement element)
  {
   if(element is CardView panel){targets.AddRange(panel.EntryFadeTargets);return;}
   if(element is InfoBar info)
   {
    // The existing InfoBar template draws its fill in an outer Border.
    // Fade its Grid children (icon/text/action), leaving that fill opaque.
    info.ApplyTemplate();
    if(VisualTreeHelper.GetChildrenCount(info)>0&&VisualTreeHelper.GetChild(info,0) is Border{Child:Panel body})
     targets.AddRange(body.Children);
    return;
   }
   if(element is Panel container){foreach(var child in container.Children)Visit(child);return;}
   if(element is ScrollViewer{Content:UIElement scrollContent}){Visit(scrollContent);return;}
   if(element is Border{Child:UIElement borderContent}){Visit(borderContent);return;}
   targets.Add(element);
  }
  if(entered.Content is UIElement content)Visit(content);
  return targets.Distinct();
 }
 private void PrepareEntryOpacity(Page entered)
 {
  foreach(var target in GetEntryOpacityTargets(entered))
   if(!entryOpacityTargets.Contains(target)){target.Opacity=0;entryOpacityTargets.Add(target);}
 }
}
