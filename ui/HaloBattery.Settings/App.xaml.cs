using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
namespace HaloBattery.Settings;
public partial class App : Application
{
 private MainWindow? window;
 // Explicit identity prevents Windows from heuristically grouping this native
 // companion with an unrelated application. Set it before creating any UI.
 // https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid
 [DllImport("shell32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]
 private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
 public App()
 {
  var identityResult=SetCurrentProcessExplicitAppUserModelID("HaloBattery.Settings");
  if(identityResult<0)LogStartup("Setting application identity failed",Marshal.GetExceptionForHR(identityResult));
  else LogStartup("Application identity: HaloBattery.Settings");
  UnhandledException+=(_,e)=>LogStartup("Unhandled XAML exception",e.Exception);
  LogStartup("Before InitializeComponent");
  try{InitializeComponent();LogStartup("Initialized XAML resources");}
  catch(Exception error){LogStartup("InitializeComponent failed",error);throw;}
 }
 protected override void OnLaunched(LaunchActivatedEventArgs args)
 {
  try
  {
   LogStartup("Launching native window");
   var bridge=new SettingsBridge();window=new MainWindow(bridge);bridge.Attach(window);window.Activate();
   LogStartup("Activated native window");
  }
  catch(Exception error){LogStartup("Window launch failed",error);throw;}
 }
 private static void LogStartup(string stage,Exception? error=null)
 {
  if(error is not null)Console.Error.WriteLine(error);
  // Fixture diagnostics stay beside the explicitly selected test fixture.
  // Production launches do not create diagnostic files or touch user settings.
  var args=Environment.GetCommandLineArgs();var at=Array.IndexOf(args,"--fixture-state");
  if(at<0||at+1>=args.Length)return;
  try
  {
   var directory=Path.GetDirectoryName(Path.GetFullPath(args[at+1]))!;
   var line=$"{DateTimeOffset.UtcNow:O} {stage}; base={AppContext.BaseDirectory}; appPri={File.Exists(Path.Combine(AppContext.BaseDirectory,"HaloBattery.Settings.pri"))}; appXbf={File.Exists(Path.Combine(AppContext.BaseDirectory,"App.xbf"))}";
   if(error is not null)line+="\n"+error;
   if(stage=="Activated native window")
    foreach(ProcessModule module in Process.GetCurrentProcess().Modules)
     if(module.ModuleName.StartsWith("Microsoft.UI.Xaml",StringComparison.OrdinalIgnoreCase)||module.ModuleName.StartsWith("Microsoft.WindowsAppRuntime",StringComparison.OrdinalIgnoreCase))line+="\n"+module.FileName;
   File.AppendAllText(Path.Combine(directory,"native-errors.log"),line+Environment.NewLine,System.Text.Encoding.UTF8);
  }
  catch(Exception loggingError){Debug.WriteLine(loggingError);}
 }
}
