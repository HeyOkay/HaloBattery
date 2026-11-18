using System.Diagnostics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private readonly Grid startupSplash=new() { IsHitTestVisible=false };
    private const float StartupLogoSize=96;
    private readonly Image startupLogo=new() { Width=StartupLogoSize,Height=StartupLogoSize,Stretch=Stretch.Uniform };
    private readonly Canvas startupShadowHost=new();
    private SpriteVisual? startupShadowVisual;
    private DropShadow? startupShadow;
    private bool startupContourShadowCreated,startupShadowIsSoft;
    private readonly TaskCompletionSource<bool> startupIconReady=new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly TaskCompletionSource<bool> startupCancelled=new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly List<Action> startupSubscriptions=new();
    private SvgImageSource? startupLogoSource;
    private TaskCompletionSource<bool>? startupPreviewReady;
    private bool buildingStartupContent,startupSplashShown,startupCompleted,startupRevealing;
    private StartupShowRequest? showAfterStartup;
    private sealed record StartupShowRequest(string? Page,string? DeviceKey,long? RequestId);
    private string? pendingStartupPage,pendingStartupDeviceKey;
    private long startupStartedAt,contentReadyAt;
    private bool splashFrameRendered,contentFrameRendered;
    private long startupNavigationVersion;
    private readonly Dictionary<UIElement,long> startupFadeVersions=new();
    private Task<bool>? splashPresented;
    private Task? startupEntranceFade;
    private StartupWindowFade? startupWindowFade;
    private Task? startupWindowFadeCompletion;
    private StartupWindowBackground? startupWindowBackground;
    private uint? startupBackgroundPaintSample;
    private CancellationTokenSource? startupRevealDeadline;
    private bool startupFramePreparedWhileCloaked,startupRevealFallbackUsed;
    internal bool IsClosingSettings=>closing || closingWindowState;

    private void InitializeStartupSplash()
    {
        // The first visible tree contains only the approved app logo and window
        // controls. Geometry, rail width and Mica are already final at this point.
        Canvas.SetZIndex(header,2);
        Canvas.SetZIndex(startupSplash,1);
        Grid.SetRowSpan(startupSplash,2);
        var logoHost=new Grid { Width=StartupLogoSize,Height=StartupLogoSize,HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center };
        logoHost.Children.Add(startupShadowHost);
        logoHost.Children.Add(startupLogo);
        startupSplash.Children.Add(logoHost);
        startupLogo.Loaded+=StartupLogoLoaded;
        root.Children.Add(startupSplash);
        ElementCompositionPreview.GetElementVisual(body).Opacity=0;
        ElementCompositionPreview.GetElementVisual(startupSplash).Opacity=0;
        body.IsHitTestVisible=false;
        startupLogoSource=new SvgImageSource();
        startupLogoSource.Opened+=StartupIconOpened;
        startupLogoSource.OpenFailed+=StartupIconFailed;
        startupLogo.Source=startupLogoSource;
        // Subscribe before UriSource: SVG URI loading is asynchronous.
        startupLogoSource.UriSource=new Uri(dark?"ms-appx:///Assets/AppIconHeaderDark.svg":"ms-appx:///Assets/AppIconHeaderLight.svg");
    }

    private void StartupIconOpened(SvgImageSource sender,SvgImageSourceOpenedEventArgs args)
    {
        startupIconReady.TrySetResult(true);
        InitializeStartupShadow();
    }
    private void StartupLogoLoaded(object sender,RoutedEventArgs args)=>InitializeStartupShadow();
    private void InitializeStartupShadow()
    {
        if(startupShadowVisual is not null || !startupLogo.IsLoaded || !startupIconReady.Task.IsCompletedSuccessfully || !startupIconReady.Task.Result)return;
        // Keep every SVG contour, including the bolt and inside of the ring.
        // A low-opacity, broad, centred shadow avoids the dark offset patches
        // produced by the previous stronger directional shadow.
        var mask=startupLogo.GetAlphaMask();
        if(mask is null)return;
        var compositor=ElementCompositionPreview.GetElementVisual(startupShadowHost).Compositor;
        startupShadow=compositor.CreateDropShadow();
        startupShadow.Mask=mask;
        startupShadow.Color=Windows.UI.Color.FromArgb(255,0,0,0);
        startupShadow.Opacity=dark?0.16f:0.10f;
        startupShadow.BlurRadius=28;
        startupShadow.Offset=System.Numerics.Vector3.Zero;
        startupShadowVisual=compositor.CreateSpriteVisual();
        startupShadowVisual.Size=new(StartupLogoSize,StartupLogoSize);
        startupShadowVisual.Shadow=startupShadow;
        ElementCompositionPreview.SetElementChildVisual(startupShadowHost,startupShadowVisual);
        startupContourShadowCreated=true;
        startupShadowIsSoft=startupShadow.Offset==System.Numerics.Vector3.Zero && startupShadow.BlurRadius>=24 && startupShadow.Opacity<=0.18f;
    }
    private void StartupIconFailed(SvgImageSource sender,SvgImageSourceFailedEventArgs args)
    {
        Debug.WriteLine("Splash icon could not be loaded: "+args.Status);
        startupIconReady.TrySetResult(false);
    }

    private void SetStartupChromeVisibility()
    {
        var visibility=pagesInitializing?Visibility.Collapsed:Visibility.Visible;
        pill.Visibility=visibility;
        if(hamburger is not null)hamburger.Visibility=visibility;
    }

    internal void ShowStartupSplash()
    {
        if(IsClosingSettings || startupSplashShown)return;
        startupSplashShown=true;
        startupStartedAt=Stopwatch.GetTimestamp();
        if(Bridge.FixtureMode && Arg("--verify-scope")=="startup-splash")
            startupBackgroundPaintSample=startupWindowBackground!.VerifyPaintColor();
        PrepareSettingsWindowForShow();
        AppWindow.Show();Activate();
        Bridge.SendVisibility(true);
        startupRevealDeadline=new();
        _=RevealStartupOnFailureAsync(startupRevealDeadline.Token);
        splashPresented=ShowSplashEntranceAsync();
    }

    private async Task<bool> ShowSplashEntranceAsync()
    {
        await Task.WhenAny(startupIconReady.Task,startupCancelled.Task);
        if(IsClosingSettings)return false;
        if(!await NextStartupFrameAsync())return false;
        startupFramePreparedWhileCloaked=StartupWindowBackground.IsAppCloaked(hwnd);
        // Commit the prepared theme/backdrop while the native window is still
        // concealed. This waits for readiness rather than a fixed loading delay.
        var commit=ElementCompositionPreview.GetElementVisual(root).Compositor.RequestCommitAsync().AsTask();
        await Task.WhenAny(commit,startupCancelled.Task,startupRevealDeadline is {} deadline?Task.Delay(Timeout.Infinite,deadline.Token):Task.CompletedTask);
        if(IsClosingSettings)return false;
        RevealPreparedStartupWindow();
        startupEntranceFade=FadeStartupElementAsync(startupSplash,1,180);
        if(!await NextStartupFrameAsync())return false;
        if(startupWindowFadeCompletion is not null)
            await Task.WhenAny(startupWindowFadeCompletion,startupCancelled.Task);
        if(IsClosingSettings)return false;
        InitializeStartupShadow();
        splashFrameRendered=true;
        return true;
    }

    internal async Task CompleteStartupAsync()
    {
        if(startupCompleted || IsClosingSettings)return;
        if(splashPresented is null || !await splashPresented)return;
        if(Bridge.FixtureMode && Arg("--capture-startup")=="yes")
        {
            if(startupEntranceFade is not null)await startupEntranceFade;
            await FixtureWindowCapture.CaptureAsync(hwnd,Path.Combine(Arg("--verify-dir"),"splash.composited.png"));
        }
        // Loaded is not a rendered frame. Let the compositor receive the splash
        // before building controls; never do that work inside a rendering event.
        if(!await EnqueueStartupAsync(()=>
        {
            buildingStartupContent=true;
            try { ApplyState(State); }
            finally { buildingStartupContent=false; }
        }))return;
        if(IsClosingSettings)return;
        long preparedNavigation;
        do
        {
            preparedNavigation=startupNavigationVersion;
            root.UpdateLayout();
            await WaitForRestoredSettingsViewAsync();
            if(IsClosingSettings)return;
            // The preview comes from Python, just as it does in the tray. A
            // failed optional preview cannot leave the window loading forever.
            if(previewImage is not null && !Bridge.FixtureMode)
            {
                startupPreviewReady=new(TaskCreationOptions.RunContinuationsAsynchronously);
                if(previewImage.Source is not null)startupPreviewReady.TrySetResult(true);
                await Task.WhenAny(startupPreviewReady.Task,startupCancelled.Task,Task.Delay(TimeSpan.FromSeconds(3)));
            }
            if(IsClosingSettings)return;
            root.UpdateLayout();
            if(!await NextStartupFrameAsync())return;
        }
        while(preparedNavigation!=startupNavigationVersion);
        contentFrameRendered=true;
        contentReadyAt=Stopwatch.GetTimestamp();
        firstPreparedView??=new(AppWindow.Position.X,AppWindow.Position.Y,AppWindow.Size.Width,AppWindow.Size.Height,
            expanded,rail.Width.Value,current,frame.Content is not null,State.Labels.Count,true);
        // Both fades share the compositor clock. There is no artificial minimum
        // loading duration; replacing the entrance animation starts at its live
        // opacity, even when the content was ready before fade-in completed.
        startupRevealing=true;
        await Task.WhenAll(FadeStartupElementAsync(startupSplash,0,140),FadeStartupElementAsync(body,1,140));
        if(IsClosingSettings)return;
        root.Children.Remove(startupSplash);
        startupCompleted=true;
        pagesInitializing=false;
        SetStartupChromeVisibility();
        body.IsHitTestVisible=true;
        DisposeStartupSubscriptions();
        if(showAfterStartup is {} request)
        {
            showAfterStartup=null;
            ShowSettingsWindow(request.Page,request.DeviceKey,request.RequestId);
        }
    }

    private Task<bool> EnqueueStartupAsync(Action action)
    {
        var ready=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        if(!DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,()=>
        {
            if(IsClosingSettings){ready.TrySetResult(false);return;}
            try { action();ready.TrySetResult(true); }
            catch(Exception error){ready.TrySetException(error);}
        }))ready.TrySetResult(false);
        return ready.Task;
    }

    private async Task<bool> NextStartupFrameAsync()
    {
        if(IsClosingSettings)return false;
        var ready=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        EventHandler<object>? rendered=null;
        Action detach=()=>CompositionTarget.Rendered-=rendered;
        rendered=(_,_)=>{detach();startupSubscriptions.Remove(detach);ready.TrySetResult(true);};
        startupSubscriptions.Add(detach);
        CompositionTarget.Rendered+=rendered;
        await Task.WhenAny(ready.Task,startupCancelled.Task);
        return !IsClosingSettings && ready.Task.IsCompletedSuccessfully;
    }

    private async Task FadeStartupElementAsync(UIElement element,float opacity,int milliseconds)
    {
        var visual=ElementCompositionPreview.GetElementVisual(element);
        var version=startupFadeVersions.GetValueOrDefault(element)+1;
        startupFadeVersions[element]=version;
        if(!AnimationsEnabled){visual.Opacity=opacity;return;}
        using var animation=visual.Compositor.CreateScalarKeyFrameAnimation();
        animation.InsertExpressionKeyFrame(0,"this.StartingValue");
        animation.InsertKeyFrame(1,opacity);
        animation.Duration=TimeSpan.FromMilliseconds(milliseconds);
        animation.StopBehavior=AnimationStopBehavior.SetToFinalValue;
        using var batch=visual.Compositor.CreateScopedBatch(CompositionBatchTypes.Animation);
        var ready=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void Finished(object sender,CompositionBatchCompletedEventArgs args)=>ready.TrySetResult(true);
        batch.Completed+=Finished;
        Action detach=()=>batch.Completed-=Finished;
        startupSubscriptions.Add(detach);
        visual.StartAnimation("Opacity",animation);
        batch.End();
        try { await Task.WhenAny(ready.Task,startupCancelled.Task); }
        finally
        {
            detach();startupSubscriptions.Remove(detach);
            if(startupFadeVersions.GetValueOrDefault(element)==version)
            { visual.StopAnimation("Opacity");visual.Opacity=opacity; }
        }
    }

    private void DisposeStartupSubscriptions()
    {
        foreach(var detach in startupSubscriptions.ToArray())detach();
        startupSubscriptions.Clear();
        startupLogo.Loaded-=StartupLogoLoaded;
        if(startupShadowVisual is not null)
        {
            ElementCompositionPreview.SetElementChildVisual(startupShadowHost,null);
            startupShadowVisual.Dispose();startupShadowVisual=null;
            startupShadow?.Dispose();startupShadow=null;
        }
        if(startupLogoSource is {} source)
        {
            source.Opened-=StartupIconOpened;source.OpenFailed-=StartupIconFailed;
            startupLogoSource=null;
        }
    }

    private void CancelStartupSplash()
    {
        startupCancelled.TrySetResult(true);
        if(IsClosingSettings && (startupWindowBackground is {Cloaked:true} || startupWindowFade is not null))AppWindow.Hide();
        startupWindowFade?.Dispose();
        ReleaseStartupWindowBackground();
        DisposeStartupSubscriptions();
    }

    private void ReleaseStartupWindowBackground()
    {
        startupRevealDeadline?.Cancel();startupRevealDeadline?.Dispose();startupRevealDeadline=null;
        if(!IsClosingSettings)startupWindowBackground?.Reveal();
        startupWindowBackground?.Dispose();startupWindowBackground=null;
    }

    private void RevealPreparedStartupWindow()
    {
        // Animate the whole native window rather than just its XAML children.
        // Its temporary opacity mode is removed at 100%, preserving normal
        // Mica, window movement and native minimize/restore after entrance.
        var fadeWindow=AnimationsEnabled && startupWindowBackground is {Cloaked:true} && AppWindow.IsVisible &&
            AppWindow.Presenter is not Microsoft.UI.Windowing.OverlappedPresenter {State:Microsoft.UI.Windowing.OverlappedPresenterState.Minimized};
        if(fadeWindow)
        {
            try { startupWindowFade=new(hwnd); }
            catch(System.ComponentModel.Win32Exception error){Debug.WriteLine(error);}
        }
        ReleaseStartupWindowBackground();
        if(startupWindowFade is not null)startupWindowFadeCompletion=startupWindowFade.RunAsync(DispatcherQueue);
    }

    private async Task RevealStartupOnFailureAsync(CancellationToken token)
    {
        try { await Task.Delay(TimeSpan.FromSeconds(2),token); }
        catch(OperationCanceledException){return;}
        if(IsClosingSettings)return;
        startupRevealFallbackUsed=true;
        // A rendering/driver failure must never strand an inaccessible window.
        // No Activate/Restore here: preserve a user's minimize or focus change.
        ReleaseStartupWindowBackground();
    }

    private async Task PrepareSettingsExitAsync()
    {
        CancelStartupSplash();
        // Explicit commit also covers the focused editor when the native close
        // button does not cause a LostFocus before its window is destroyed.
        Bridge.SendClosing();
        pendingEditorCommit?.Invoke();pendingEditorCommit=null;
        IsSettingsWindowHidden=true;
        AppWindow.Hide();
        OnSettingsWindowHidden();
        await Bridge.FlushAsync();
    }
}
