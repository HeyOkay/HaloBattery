// Native WinUI visual shell extracted from Chrome Mica Patcher MainWindow.cs.
// Halo-specific state and controls are supplied by MainWindow.Pages.cs.
using System.Diagnostics;
using System.Numerics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Runtime.InteropServices.WindowsRuntime;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics;
using Windows.UI;
using Windows.UI.ViewManagement;


namespace HaloBattery.Settings;
public sealed partial class MainWindow : Window
{
 internal readonly SettingsBridge Bridge;
 internal UiState State=UiState.Empty;
 internal string? selectedDeviceKey;
 private readonly UISettings settings=new();
 private readonly Grid root=new(),header=new(){Height=56},body=new();
 private readonly StackPanel nav=new(){Spacing=4,Margin=new(12,6,12,8)};
 private readonly Frame frame=new();
 private readonly Border card=new(){CornerRadius=new(12),Margin=new(0,0,8,8)};
 private readonly Border pill=new(){Height=40,Padding=new(0),HorizontalAlignment=HorizontalAlignment.Center};
 private readonly SvgImageSource darkHeaderLogo=new(new Uri("ms-appx:///Assets/AppIconHeaderDark.svg")),lightHeaderLogo=new(new Uri("ms-appx:///Assets/AppIconHeaderLight.svg"));
 private const double HeaderButtonSize=40,HeaderIconSize=20,HeaderEdgeInset=12,HeaderButtonGap=4;
 private readonly StackPanel windowButtons=new(){Orientation=Orientation.Horizontal,Spacing=HeaderButtonGap,Margin=new(0,0,HeaderEdgeInset,0),HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Center};
 private readonly ColumnDefinition rail=new(){Width=new(228)};

 private readonly string themeOverride,diagnostics;
 private string current="general";
 private bool expanded=true,dark,closing,changingTheme;
 private Color accent;
 private readonly FontFamily bodyFont=new("Segoe UI Variable");
 // Статичные начертания с хинтингом: вариативный Outfit по умолчанию Thin, и WinUI дорисовывал жирность сам - отсюда «лесенка».
 internal FontFamily TitleFont=>new(State.Language=="ru"?"ms-appx:///Assets/Fonts/Onest-Bold.ttf#Onest":"ms-appx:///Assets/Fonts/Outfit-SemiBold.ttf#Outfit");
 internal static readonly FontFamily MonoFont=new("Cascadia Mono, Consolas");
 internal bool Dark=>dark;
 internal bool AnimationsEnabled=>settings.AnimationsEnabled;
 internal string AccentHex=>$"#{accent.R:X2}{accent.G:X2}{accent.B:X2}";
 private readonly List<CardView> cards=new();
 private readonly List<UIElement> navLabels=new();
 private EventHandler<object>? railTick;
 private Page? pendingEntryPage;
 private RoutedEventHandler? pendingEntryLoaded;
 private Storyboard? pageEntry;
 private TranslateTransform? pageEntryTransform;
 private nint hwnd;
 internal string Later=>T("settings.unsupported");
 internal SolidColorBrush TextBrush=>Brush(dark?"#FFFFFF":"#1B1B1B");
 internal SolidColorBrush SubtleBrush=>Brush(dark?"#B5B5B5":"#646464");
 internal SolidColorBrush CardBrush=>Brush(dark?"#242424":"#FFFFFF");
 internal SolidColorBrush CardHoverBrush=>Brush(dark?"#2D2D2D":"#F6F6F6");
 internal SolidColorBrush CardPressedBrush=>Brush(dark?"#282828":"#F0F0F0");
 // ControlElevationBorderBrush WinUI: тонкая обводка, нижний край темнее.
 internal Microsoft.UI.Xaml.Media.Brush ElevationBorderBrush()
 {
  var brush=new LinearGradientBrush{StartPoint=new Windows.Foundation.Point(0,0),EndPoint=new Windows.Foundation.Point(0,1)};
  foreach(var (hex,offset) in dark?new[]{("#4A4A4A",0.0),("#383838",0.12),("#383838",0.88),("#141414",1.0)}:new[]{("#ECECEC",0.0),("#E3E3E3",0.12),("#E3E3E3",0.88),("#C2C2C2",1.0)})
   brush.GradientStops.Add(new GradientStop{Color=Brush(hex).Color,Offset=offset});
  return brush;
 }
 internal SolidColorBrush LineBrush=>Brush(dark?"#333333":"#E3E3E3");
 private static string Arg(string name,string fallback="") {var args=Environment.GetCommandLineArgs();var i=Array.IndexOf(args,name);return i>=0&&i+1<args.Length?args[i+1]:fallback;}
 public MainWindow(SettingsBridge bridge)
 {
  Bridge=bridge;
  themeOverride=Arg("--theme");diagnostics=Arg("--diagnostics");Title="HaloBattery";
  hwnd=WinRT.Interop.WindowNative.GetWindowHandle(this);
  try{AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory,"Assets","AppIcon.ico"));}catch(Exception error){Debug.WriteLine(error);}
  if(AppWindow.Presenter is OverlappedPresenter p)p.SetBorderAndTitleBar(true,false);
  AppWindow.Resize(new SizeInt32((int)(1280*GetDpiForWindow(hwnd)/96.0),(int)(900*GetDpiForWindow(hwnd)/96.0)));
  root.RowDefinitions.Add(new(){Height=new(56)});root.RowDefinitions.Add(new(){Height=new(1,GridUnitType.Star)});
  body.ColumnDefinitions.Add(rail);body.ColumnDefinitions.Add(new(){Width=new(1,GridUnitType.Star)});
  root.Children.Add(header);Grid.SetRow(body,1);root.Children.Add(body);Grid.SetColumn(card,1);frame.HorizontalAlignment=HorizontalAlignment.Stretch;frame.VerticalAlignment=VerticalAlignment.Stretch;frame.HorizontalContentAlignment=HorizontalAlignment.Stretch;frame.VerticalContentAlignment=VerticalAlignment.Stretch;card.HorizontalAlignment=HorizontalAlignment.Stretch;card.Child=BuildOverscrollHost(frame);body.Children.Add(card);
  var navigation=BuildNavigationHost();AttachPageWheelNavigation(navigation);body.Children.Add(navigation);Content=root;
  header.Background=Brush("#00000000");
  header.SizeChanged+=(_,_)=>{PositionPill();UpdateDragRegion();};
  windowButtons.SizeChanged+=(_,_)=>UpdateDragRegion();
  root.SizeChanged+=(_,_)=>UpdateDragRegion();
  root.AddHandler(UIElement.PointerMovedEvent,new Microsoft.UI.Xaml.Input.PointerEventHandler((_,e)=>{foreach(var c in cards)c.SetSpotlight(e.GetCurrentPoint(c).Position);}),true);
  root.PointerExited+=(_,_)=>{foreach(var c in cards)c.SetSpotlight(null);};
  settings.ColorValuesChanged+=OnColorsChanged;
  AppWindow.Changed+=OnWindowChanged;
  Closed+=(_,_)=>{closing=true;settings.ColorValuesChanged-=OnColorsChanged;AppWindow.Changed-=OnWindowChanged;StopRail();ResetPageEntry();};
  ApplyTheme();
  root.Loaded+=(_,_)=>{UpdateDragRegion();UpdateMinimumWidth();};
 }
 [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint window);
 private double Scale=>root.XamlRoot?.RasterizationScale??GetDpiForWindow(hwnd)/96.0;
 private void UpdateDragRegion()
 {
  try {
   if(root.XamlRoot is null||root.ActualWidth<=0)return;var s=Scale;
   var left=(HeaderEdgeInset+HeaderButtonSize+HeaderButtonGap)*s;
   var right=(root.ActualWidth-HeaderControlsWidth)*s;
   if(windowButtons.IsLoaded&&windowButtons.ActualWidth>0)
    right=windowButtons.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point(0,0)).X*s;
   var source=Microsoft.UI.Input.InputNonClientPointerSource.GetForWindowId(AppWindow.Id);
   source.SetRegionRects(Microsoft.UI.Input.NonClientRegionKind.Caption,new[]{new RectInt32((int)left,0,(int)Math.Max(0,right-left),(int)(56*s))});
  } catch(Exception error){Debug.WriteLine(error);}
 }
 private void OnColorsChanged(UISettings sender,object args)=>DispatcherQueue.TryEnqueue(async()=>
 {
  if(closing||changingTheme)return;changingTheme=true;
  try {
   ResetPageEntry();
   if(!settings.AnimationsEnabled){ApplyTheme();Rebuild();return;}
   var previous=new RenderTargetBitmap();await previous.RenderAsync(root);ApplyTheme();Rebuild();
   var overlay=new Image{Source=previous,Stretch=Stretch.Fill,IsHitTestVisible=false};Grid.SetRowSpan(overlay,2);root.Children.Add(overlay);
   var visual=ElementCompositionPreview.GetElementVisual(overlay);var fade=visual.Compositor.CreateScalarKeyFrameAnimation();fade.InsertKeyFrame(1,0);fade.Duration=TimeSpan.FromMilliseconds(300);visual.StartAnimation("Opacity",fade);await Task.Delay(300);root.Children.Remove(overlay);
  } finally {changingTheme=false;}
 });
 private void ApplyTheme()
 {
  dark=themeOverride.Length==0?settings.GetColorValue(UIColorType.Background).R<128:themeOverride.Equals("dark",StringComparison.OrdinalIgnoreCase);root.RequestedTheme=dark?ElementTheme.Dark:ElementTheme.Light;accent=settings.GetColorValue(dark?UIColorType.AccentLight2:UIColorType.AccentDark1);
  Application.Current.Resources["SystemAccentColor"]=accent;Application.Current.Resources["ControlCornerRadius"]=new CornerRadius(15);Application.Current.Resources["OverlayCornerRadius"]=new CornerRadius(16);Application.Current.Resources["ContentControlThemeFontFamily"]=bodyFont;
  if(Environment.OSVersion.Version.Build>=22000&&Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()){SystemBackdrop=new MicaBackdrop();root.Background=Brush("#00000000");}else{SystemBackdrop=null;root.Background=Brush(dark?"#202020":"#F3F3F3");}
  card.Background=Brush(dark?"#181818":"#FFFFFF");card.Shadow=new ThemeShadow();card.Translation=new(0,0,12);
 }
 private void Rebuild(){Navigate(current,false);}
 private string MenuName=>V(expanded?"paneClose":"paneOpen");
 private double HeaderControlsWidth=>HeaderEdgeInset+3*HeaderButtonSize+2*HeaderButtonGap;
 private Button? hamburger,maximizeButton;
 private bool? displayedMaximized;
 private void OnWindowChanged(AppWindow sender,AppWindowChangedEventArgs args)
 {
  if(closing)return;
  DispatcherQueue.TryEnqueue(()=>{if(!closing){UpdateMaximizeButton();PositionPill();UpdateDragRegion();}});
 }
 private void UpdateMaximizeButton()
 {
  if(maximizeButton is null)return;
  var maximized=AppWindow.Presenter is OverlappedPresenter{State:OverlappedPresenterState.Maximized};
  if(displayedMaximized==maximized)return;
  displayedMaximized=maximized;
  // These Lucide paths span 18 grid units; Close spans 12. Match their
  // visible bounds and stroke width while keeping every hit target 40 x 40.
  var glyph=(FrameworkElement)LucideIcon(maximized?"minimize-2":"maximize-2",HeaderIconSize*2/3,null,2.25);
  glyph.Tag=maximized?"restore":"maximize";maximizeButton.Content=glyph;
  var label=T(maximized?"settings.restore":"settings.maximize");
  ToolTipService.SetToolTip(maximizeButton,label);AutomationProperties.SetName(maximizeButton,label);
 }
 private Button HeaderButton(string glyph,string label)
 {
  var button=Button("",true,true);
  button.Content=LucideIcon(glyph,HeaderIconSize);
  button.Width=button.Height=button.MinWidth=button.MinHeight=button.MaxWidth=button.MaxHeight=HeaderButtonSize;
  button.HorizontalContentAlignment=HorizontalAlignment.Center;button.VerticalContentAlignment=VerticalAlignment.Center;button.Padding=new(0);button.CornerRadius=new(10);
  if(!dark)
  {
   button.Resources["ButtonBackgroundPointerOver"]=Brush("#1F000000");
   button.Resources["ButtonBackgroundPressed"]=Brush("#2C000000");
   button.Resources["ButtonForegroundPointerOver"]=TextBrush;
   button.Resources["ButtonForegroundPressed"]=TextBrush;
  }
  ToolTipService.SetToolTip(button,label);AutomationProperties.SetName(button,label);
  return button;
 }
 private void BuildHeader()
 {
  header.Children.Clear();
  var textAccent=new SolidColorBrush(Desaturate(accent,0.5,dark?0.72:-1));
  hamburger=HeaderButton("menu",MenuName);hamburger.Margin=new(HeaderEdgeInset,0,0,0);hamburger.HorizontalAlignment=HorizontalAlignment.Left;hamburger.Click+=(_,_)=>ToggleRail();header.Children.Add(hamburger);
  pill.Background=Brush("#00000000");var content=new Grid();foreach(var width in new[]{GridLength.Auto,GridLength.Auto})content.ColumnDefinitions.Add(new(){Width=width});
  var logo=new Image{Source=dark?darkHeaderLogo:lightHeaderLogo,Width=22,Height=22,Stretch=Stretch.Uniform,VerticalAlignment=VerticalAlignment.Center,Margin=new(0,0,2,0)};content.Children.Add(logo);
  var brand=new TextBlock{FontSize=16,FontFamily=new FontFamily("ms-appx:///Assets/Fonts/Outfit-SemiBold.ttf#Outfit"),FontWeight=Microsoft.UI.Text.FontWeights.SemiBold,Foreground=TextBrush,CharacterSpacing=-12,VerticalAlignment=VerticalAlignment.Center,TextLineBounds=TextLineBounds.Tight};brand.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run{Text="Halo",FontFamily=new FontFamily("ms-appx:///Assets/Fonts/Outfit-Medium.ttf#Outfit"),FontWeight=Microsoft.UI.Text.FontWeights.Medium,Foreground=TextBrush});brand.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run{Text="Battery",Foreground=textAccent});Grid.SetColumn(brand,1);content.Children.Add(brand);
  pill.Child=content;pill.IsHitTestVisible=false;header.Children.Add(pill);
  windowButtons.Children.Clear();var minimize=HeaderButton("minus",T("settings.minimize"));maximizeButton=HeaderButton("maximize-2",T("settings.maximize"));displayedMaximized=null;var close=HeaderButton("x",T("settings.close"));foreach(var button in new[]{minimize,maximizeButton,close})windowButtons.Children.Add(button);minimize.Click+=(_,_)=>((OverlappedPresenter)AppWindow.Presenter).Minimize();maximizeButton.Click+=(_,_)=>{var p=(OverlappedPresenter)AppWindow.Presenter;if(p.State==OverlappedPresenterState.Maximized)p.Restore();else p.Maximize();UpdateMaximizeButton();};close.Click+=(_,_)=>Close();header.Children.Add(windowButtons);UpdateMaximizeButton();PositionPill();
 }
 // В развёрнутом окне страница и омнибокс центрируются по всему окну, а не по области справа от панели вкладок.
 // Если места не хватает - сдвигаются вправо ровно настолько, чтобы не заходить под панель и не вылезать за подложку.
 private double PageWidth(double frameWidth)=>Math.Max(0,Math.Min(contentWidth,frameWidth));
 private double PageLeft(double frameWidth)
 {
  var pane=rail.Width.Value;var page=PageWidth(frameWidth);
  // Только в развёрнутом окне - по центру всего окна; в обычном окне - по центру области страницы, как было.
  var maximized=AppWindow.Presenter is OverlappedPresenter{State:OverlappedPresenterState.Maximized};
  var centered=maximized?(root.ActualWidth-page)/2-pane:(frameWidth-page)/2;
  return Math.Max(0,Math.Min(centered,frameWidth-page));
 }
 private readonly List<(StackPanel content,ScrollViewer scroll)> pageHosts=new();
 private double PageInset(double width)=>44*width/contentWidth;
 private void LayoutPage(StackPanel content,double viewport)
 {
  var width=PageWidth(viewport);var inset=PageInset(width);
  content.Width=width;content.Padding=new(inset,36,inset,36);content.Margin=new(PageLeft(viewport),0,0,0);
 }
 private void LayoutPages(){foreach(var (content,scroll) in pageHosts)LayoutPage(content,scroll.ViewportWidth>0?scroll.ViewportWidth:scroll.ActualWidth);PositionOverscrollIndicator();PositionGeneralFooter();}
 private void PositionPill()
 {
  pill.HorizontalAlignment=HorizontalAlignment.Center;pill.Margin=new(0);
  LayoutPages();
 }
 private void StopRail(){if(railTick is null)return;CompositionTarget.Rendering-=railTick;railTick=null;}
 // Сворачивание панели: только ширина колонки, подписи гаснут; страница не пересоздаётся.
 private void ToggleRail()
 {
  expanded=!expanded;var target=expanded?228.0:64.0;StopRail();
  if(hamburger is not null){ToolTipService.SetToolTip(hamburger,MenuName);AutomationProperties.SetName(hamburger,MenuName);}
  if(!settings.AnimationsEnabled){rail.Width=new(target);FinishRail();return;}
  var start=rail.Width.Value;var began=Stopwatch.StartNew();var fromOpacity=navLabels.FirstOrDefault()?.Opacity??(expanded?0.0:1.0);var toOpacity=expanded?1.0:0.0;
  railTick=(_,_)=>{var t=Math.Min(1,began.Elapsed.TotalMilliseconds/250);var e=1-Math.Pow(1-t,3);rail.Width=new(start+(target-start)*e);PositionPill();var o=fromOpacity+(toOpacity-fromOpacity)*Math.Min(1,t*1.6);foreach(var label in navLabels)label.Opacity=o;if(t>=1){StopRail();FinishRail();}};
  CompositionTarget.Rendering+=railTick;
 }
 private void FinishRail(){BuildNavigation();PositionPill();UpdateDragRegion();UpdateMinimumWidth();}
 private void BuildNavigation()
 {
  RefreshNavigation();
 }
 internal void Navigate(string page,bool motion)
 {
  ResetOverscrollForNavigation();
  var next=State.Pages.Any(p=>p.Id==page)?page:State.Pages.FirstOrDefault()?.Id??"general";
  var switching=frame.Content is not null&&next!=current;
  // Перестройка той же страницы (после действия, смены переключателя) сохраняет раскрытые карточки.
  var keep=next==current&&frame.Content is not null?cards.Select(c=>c.IsExpanded).ToArray():null;
  var oldScroll=keep is not null&&frame.Content is Page{Content:ScrollViewer o}?o:null;
  current=next;cards.Clear();var built=BuildPage(current);
  if(keep is not null&&keep.Length==cards.Count)for(var i=0;i<keep.Length;i++)if(cards[i].Expandable)cards[i].IsExpanded=keep[i];
  // Перестройка той же страницы - на месте: новое содержимое ставится в прежний ScrollViewer, без смены страницы и прыжка прокрутки.
  if(oldScroll is not null&&built.Content is ScrollViewer{Content:UIElement inner} fresh&&pageHosts.Count==1&&pageHosts[0].scroll==fresh)
  {
   // Content-mode clocks target individual children: finish before replacing
   // those children, so detached targets cannot leave the refreshed UI half faded.
   if(pageFadeMode=="content")ResetPageEntry();
   var content=pageHosts[0].content;fresh.Content=null;oldScroll.Content=inner;pageHosts[0]=(content,oldScroll);
   LayoutPage(content,oldScroll.ViewportWidth>0?oldScroll.ViewportWidth:oldScroll.ActualWidth);
  }
  else
  {
   ResetPageEntry();
   var animate=motion&&switching&&settings.AnimationsEnabled;
   if(animate)PrepareEntryOpacity(built);
   frame.Content=built;
   if(animate)FadePageEntry(built);
  }
  BuildNavigation();BuildHeader();
  DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,UpdateMinimumWidth);
 }
 private void ResetPageEntry()
 {
  if(pendingEntryPage is not null&&pendingEntryLoaded is not null)pendingEntryPage.Loaded-=pendingEntryLoaded;
  pendingEntryPage=null;pendingEntryLoaded=null;
  var finished=pageEntry;pageEntry=null;
  foreach(var target in entryOpacityTargets)target.Opacity=1;entryOpacityTargets.Clear();
  if(frame.Content is Page visible)visible.Opacity=1;
  if(pageEntryTransform is not null)pageEntryTransform.Y=0;pageEntryTransform=null;
  // Stop exposes the base values. Commit the endpoint first so it cannot
  // briefly reveal the original zero opacity / 40 DIP offset on landing.
  finished?.Stop();
 }
 private void FadePageEntry(Page entered)
 {
  navigationWheelRemainder=0;
  ElementCompositionPreview.GetElementVisual(entered).IsPixelSnappingEnabled=true;
  var transform=new TranslateTransform{Y=40};entered.RenderTransform=transform;pageEntryTransform=transform;
  void Begin()
  {
   if(!ReferenceEquals(frame.Content,entered)||closing)return;
   if(!settings.AnimationsEnabled){ResetPageEntry();return;}
   PrepareEntryOpacity(entered);
   // Animate the XAML properties through one clock. Directly changing the
   // backing Visual.Opacity competes with XAML's initial opacity propagation.
   var transition=new Storyboard();pageEntry=transition;
   void Track(DependencyObject target,string property,double from,double to)
   {
    EasingFunctionBase easing=property=="Opacity"&&pageFadeMode=="content"?new QuadraticEase{EasingMode=EasingMode.EaseOut}:new CubicEase{EasingMode=EasingMode.EaseOut};
    var animation=new DoubleAnimation{From=from,To=to,Duration=new Duration(TimeSpan.FromMilliseconds(300)),EasingFunction=easing};
    Storyboard.SetTarget(animation,target);Storyboard.SetTargetProperty(animation,property);transition.Children.Add(animation);
   }
   foreach(var target in entryOpacityTargets)Track(target,"Opacity",0,1);
   Track(transform,"Y",40,0);
   transition.Completed+=(_,_)=>{if(ReferenceEquals(pageEntry,transition)&&ReferenceEquals(frame.Content,entered))ResetPageEntry();};
   transition.Begin();
  }
  if(entered.IsLoaded){Begin();return;}
  pendingEntryPage=entered;
  pendingEntryLoaded=(_,_)=>
  {
   if(pendingEntryLoaded is not null)entered.Loaded-=pendingEntryLoaded;
   pendingEntryPage=null;pendingEntryLoaded=null;Begin();
  };
  entered.Loaded+=pendingEntryLoaded;
 }
 // Cards are at most 800 DIP. Below the reference width, page insets scale
 // proportionally and descriptions wrap; the minimum window is 70% as wide.
 private const double CardWidth=800;
 private readonly double contentWidth=CardWidth+88;
 internal double ContentWidth=>contentWidth;
 private double MinimumWindowWidthDip=>(228+8+contentWidth+2)*0.7;
 private void UpdateMinimumWidth()
 {
  try {
   var s=Scale;var min=(int)Math.Ceiling(MinimumWindowWidthDip*s);
   if(AppWindow.Presenter is OverlappedPresenter p){p.PreferredMinimumWidth=min;p.PreferredMinimumHeight=(int)(560*s);}
   if(AppWindow.Size.Width<min)AppWindow.Resize(new SizeInt32(min,AppWindow.Size.Height));
   PositionPill();UpdateDragRegion();
  } catch(Exception error){Debug.WriteLine(error);}
 }
 // Тот же оттенок, насыщенность x factor; светлота - заданная (0..1) или как у исходного цвета (lightness<0).
 private static Color Desaturate(Color c,double factor,double lightness)
 {
  double r=c.R/255.0,g=c.G/255.0,b=c.B/255.0,max=Math.Max(r,Math.Max(g,b)),min=Math.Min(r,Math.Min(g,b)),l=(max+min)/2,h=0,s=0,d=max-min;
  if(d>0){s=l>0.5?d/(2-max-min):d/(max+min);h=max==r?(g-b)/d+(g<b?6:0):max==g?(b-r)/d+2:(r-g)/d+4;h/=6;}
  s*=factor;if(lightness>=0)l=lightness;
  double Hue(double p,double q,double t){if(t<0)t+=1;if(t>1)t-=1;return t<1.0/6?p+(q-p)*6*t:t<0.5?q:t<2.0/3?p+(q-p)*(2.0/3-t)*6:p;}
  double R=l,G=l,B=l;if(s>0){var q=l<0.5?l*(1+s):l+s-l*s;var p=2*l-q;R=Hue(p,q,h+1.0/3);G=Hue(p,q,h);B=Hue(p,q,h-1.0/3);}
  return Color.FromArgb(255,(byte)Math.Round(R*255),(byte)Math.Round(G*255),(byte)Math.Round(B*255));
 }
 private static Color Mix(Color a,Color b,double t)=>Color.FromArgb(255,(byte)Math.Round(a.R+(b.R-a.R)*t),(byte)Math.Round(a.G+(b.G-a.G)*t),(byte)Math.Round(a.B+(b.B-a.B)*t));
 internal SolidColorBrush Brush(string hex){hex=hex.TrimStart('#');uint v=Convert.ToUInt32(hex,16);return new(Color.FromArgb(hex.Length==8?(byte)(v>>24):(byte)255,(byte)(v>>16),(byte)(v>>8),(byte)v));}
 internal TextBlock Text(string value,double size=14,bool subtle=false)=>new(){Text=value,FontSize=size,FontFamily=bodyFont,Foreground=subtle?SubtleBrush:TextBrush,TextWrapping=TextWrapping.Wrap,VerticalAlignment=VerticalAlignment.Center};
 internal Button Button(string label,bool enabled=false,bool chrome=false,bool primary=false)
 {
  var b=new Button{Content=label,FontFamily=bodyFont,FontSize=13,Height=30,MinHeight=30,Padding=new(14,0,14,0),CornerRadius=new(15),BorderThickness=new(0),Background=chrome?Brush("#00000000"):Brush(dark?"#303030":"#ECECEC"),Foreground=TextBrush,IsEnabled=enabled,VerticalAlignment=VerticalAlignment.Center};
  if(!chrome&&!primary)
  {
   // Match PillComboBox's opaque states instead of compositing WinUI's
   // translucent defaults over the card's changing hover background.
   var hover=Brush(dark?"#3A3A3A":"#E0E0E0");
   foreach(var key in new[]{"ButtonBackgroundPointerOver","ButtonBackgroundPressed"})b.Resources[key]=hover;
   foreach(var key in new[]{"ButtonForegroundPointerOver","ButtonForegroundPressed"})b.Resources[key]=TextBrush;
  }
  if(primary){b.Background=Brush($"#{accent.R:X2}{accent.G:X2}{accent.B:X2}");b.Foreground=Brush(dark?"#000000":"#FFFFFF");b.FontWeight=Microsoft.UI.Text.FontWeights.SemiBold;}
  if(!enabled){ToolTipService.SetToolTip(b,Later);AutomationProperties.SetHelpText(b,Later);}
  return b;
 }
 internal StackPanel PageContent(string title)
 {
  var page=new StackPanel{MaxWidth=contentWidth,Spacing=12,Padding=new(44,36,44,36),HorizontalAlignment=HorizontalAlignment.Stretch};var heading=Text(title,State.Language=="ru"?28:30);heading.FontFamily=TitleFont;heading.FontWeight=State.Language=="ru"?Microsoft.UI.Text.FontWeights.Bold:Microsoft.UI.Text.FontWeights.SemiBold;heading.CharacterSpacing=State.Language=="ru"?-11:-10;page.Children.Add(heading);return page;
 }
 internal void Section(StackPanel parent,string name){var text=Text(name);text.FontWeight=Microsoft.UI.Text.FontWeights.SemiBold;text.Margin=new(0,14,0,0);parent.Children.Add(text);}
 internal CardView Card(string title,string description,UIElement? action=null,object? icon=null)=>MakeCard(title,description,action,icon??"info",false);
 internal CardView Expander(string title,string description,UIElement? action=null,object? icon=null)=>MakeCard(title,description,action,icon??"info",true);
 private CardView MakeCard(string title,string description,UIElement? action,object icon,bool expandable)
 {
  var iconElement=icon switch{string glyph=>(UIElement)Icon(glyph),UIElement element=>element,_=>null};
  var card=new CardView(this,iconElement,Text(title),description.Length>0?Text(description,12,true):null,action,expandable);cards.Add(card);return card;
 }
 // Все иконки карточек - один стиль: контур 1,5 в сетке 24, размер 20 (как «Адресная строка» и «Полосы прокрутки»).
 private UIElement Icon(string name)=>LucideIcon(name);
 internal void AddBody(CardView expander,UIElement body)=>expander.AddBody(body);
 internal string V(string key)=>T(key=="expand"?"settings.expand":key=="paneClose"?"settings.collapse":key=="paneOpen"?"settings.expand":key);
 internal string T(string key)=>State.Labels.TryGetValue(key,out var value)?value:key;
 internal UIElement TitleWithTag(string title,string tag,bool warning)
 {
  var row=new StackPanel{Orientation=Orientation.Horizontal,Spacing=8};row.Children.Add(Text(title));
  var label=Text(tag,12);label.FontWeight=Microsoft.UI.Text.FontWeights.SemiBold;label.Foreground=warning?Brush(dark?"#FCE100":"#7A4800"):SubtleBrush;
  row.Children.Add(new Border{CornerRadius=new(10),Padding=new(10,2,10,2),VerticalAlignment=VerticalAlignment.Center,Background=warning?Brush(dark?"#433519":"#FFF4CE"):Brush(dark?"#0FFFFFFF":"#0A000000"),Child=label});return row;
 }
 internal Border Warning(string title,string message)
 {
  var grid=new Grid{ColumnSpacing=14};grid.ColumnDefinitions.Add(new(){Width=GridLength.Auto});grid.ColumnDefinitions.Add(new(){Width=new(1,GridUnitType.Star)});
  var markFg=dark?"#000000":"#FFFFFF";var mark=(UIElement)XamlReader.Load("<Canvas xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Width='16' Height='16'><Path Data='M8,4.2 L8,8.9' Stroke='"+markFg+"' StrokeThickness='1.7' StrokeStartLineCap='Round' StrokeEndLineCap='Round'/><Ellipse Canvas.Left='7.05' Canvas.Top='10.65' Width='1.9' Height='1.9' Fill='"+markFg+"'/></Canvas>");
  grid.Children.Add(new Border{Width=16,Height=16,CornerRadius=new(8),Background=Brush(dark?"#FCE100":"#9D5D00"),VerticalAlignment=VerticalAlignment.Center,Child=mark});
  var line=new TextBlock{TextWrapping=TextWrapping.Wrap,FontSize=14,Foreground=TextBrush,VerticalAlignment=VerticalAlignment.Center};line.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run{Text=title+"  ",FontWeight=Microsoft.UI.Text.FontWeights.SemiBold});line.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run{Text=message});Grid.SetColumn(line,1);grid.Children.Add(line);
  return new Border{CornerRadius=new(16),Padding=new(20,12,24,12),BorderThickness=new(0),Background=Brush(dark?"#433519":"#FFF4CE"),Child=grid};
 }
 internal ComboBox PillComboBox()
 {
  var fill=Brush(dark?"#303030":"#ECECEC");var hover=Brush(dark?"#3A3A3A":"#E0E0E0");
  var box=new ComboBox{Height=30,MinHeight=30,MinWidth=240,FontSize=13,CornerRadius=new(15),BorderThickness=new(0),Padding=new(14,0,8,0),Background=fill,Foreground=TextBrush,HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Center};
  foreach(var key in new[]{"ComboBoxBackground","ComboBoxBackgroundUnfocused","ComboBoxBackgroundFocused"})box.Resources[key]=fill;
  foreach(var key in new[]{"ComboBoxBackgroundPointerOver","ComboBoxBackgroundPressed"})box.Resources[key]=hover;
  box.Resources["ComboBoxDropDownBackground"]=Brush(dark?"#FF2C2C2C":"#FFFCFCFC");box.Resources["ComboBoxDropDownBorderBrush"]=LineBrush;
  var item=new Style(typeof(ComboBoxItem));item.Setters.Add(new Setter(ComboBoxItem.CornerRadiusProperty,new CornerRadius(15)));item.Setters.Add(new Setter(ComboBoxItem.MinHeightProperty,30.0));item.Setters.Add(new Setter(ComboBoxItem.FontSizeProperty,13.0));item.Setters.Add(new Setter(ComboBoxItem.PaddingProperty,new Thickness(12,0,12,0)));item.Setters.Add(new Setter(ComboBoxItem.MarginProperty,new Thickness(4,0,4,4)));box.ItemContainerStyle=item;
  box.Resources["ComboBoxDropdownContentMargin"]=new Thickness(0,4,0,0);
  ConfigureComboSelection(box);
  return box;
 }
 // Ширина поля - по самому длинному пункту (+ отступы и стрелка), чтобы пункты меню целиком помещались; потом не меняется.
 internal void FitComboBox(ComboBox box)
 {
  double widest=0;foreach(var item in box.Items){var probe=new TextBlock{Text=item?.ToString()??"",FontSize=13,FontFamily=bodyFont};probe.Measure(new Windows.Foundation.Size(double.PositiveInfinity,double.PositiveInfinity));widest=Math.Max(widest,probe.DesiredSize.Width);}
  box.MinWidth=0;box.Width=Math.Ceiling(widest+12+12+4+4+36);
 }
 internal UIElement DetailRow(string label,UIElement value,bool divider=true){var grid=new Grid{ColumnSpacing=16,MinHeight=44,Tag="row"};grid.ColumnDefinitions.Add(new(){Width=new(1,GridUnitType.Star)});grid.ColumnDefinitions.Add(new(){Width=GridLength.Auto});var name=Text(label,14);grid.Children.Add(name);if(value is FrameworkElement fe){fe.HorizontalAlignment=HorizontalAlignment.Right;fe.VerticalAlignment=VerticalAlignment.Center;}if(value is TextBlock tb){tb.TextWrapping=TextWrapping.NoWrap;}Grid.SetColumn((FrameworkElement)value,1);grid.Children.Add(value);return new Border{Child=grid,BorderBrush=LineBrush,BorderThickness=new(0,divider?1:0,0,0),Padding=new(0,6,0,6)};}
 internal UIElement DetailRow(string label,string value,bool divider=true){var text=Text(value,14,true);text.TextAlignment=TextAlignment.Right;return DetailRow(label,text,divider);}
 internal StackPanel ButtonRow(params UIElement[] buttons){var row=new StackPanel{Orientation=Orientation.Horizontal,Spacing=8,HorizontalAlignment=HorizontalAlignment.Right};foreach(var b in buttons)row.Children.Add(b);return row;}
 internal UIElement ReserveChevron(UIElement action)=>new Grid{Margin=new(0,0,CardView.ChevronSpace,0),Children={action}};
 internal Border StepCircle(int step,bool done,bool current){var mark=Text(done?"\uE73E":step.ToString(),done?10:12);if(done)mark.FontFamily=new FontFamily("Segoe Fluent Icons");mark.FontWeight=Microsoft.UI.Text.FontWeights.SemiBold;mark.HorizontalAlignment=HorizontalAlignment.Center;mark.TextAlignment=TextAlignment.Center;mark.TextWrapping=TextWrapping.NoWrap;mark.TextLineBounds=TextLineBounds.Tight;if(!done)mark.Margin=new(1,1,0,0);mark.Foreground=done||current?Brush(dark?"#000000":"#FFFFFF"):SubtleBrush;var accentHex=$"#{accent.R:X2}{accent.G:X2}{accent.B:X2}";return new Border{Width=24,Height=24,CornerRadius=new(12),Background=done?Brush(dark?"#6CCB5F":"#0F7B0F"):current?Brush(accentHex):Brush("#00000000"),BorderBrush=SubtleBrush,BorderThickness=new(done||current?0:1.5),VerticalAlignment=VerticalAlignment.Center,Child=mark};}
 // Ширина содержимого задаётся явно (min(общая ширина, видимая ширина)) и не зависит от того, сколько «просит» сама страница:
 // иначе страницы с узким содержимым при растягивании окна вставали не по центру.
 internal ScrollViewer Scroll(StackPanel content)
 {
  content.HorizontalAlignment=HorizontalAlignment.Left;content.MaxWidth=double.PositiveInfinity;
  var host=new Grid{HorizontalAlignment=HorizontalAlignment.Stretch};host.Children.Add(content);
  var scroll=new ScrollViewer{Content=host,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled};
  pageHosts.Clear();pageHosts.Add((content,scroll));
  // Содержимое берётся из pageHosts: при перестройке на месте в этот же ScrollViewer подставляется новое содержимое.
  scroll.SizeChanged+=(sender,e)=>{foreach(var (c,s) in pageHosts)if(ReferenceEquals(s,sender))LayoutPage(c,s.ViewportWidth>0?s.ViewportWidth:e.NewSize.Width);};
  return scroll;
 }

}
