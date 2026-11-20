using Microsoft.UI.Xaml.Hosting;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private Task VerifyStartupSplash(List<object> checks)
    {
        void Check(string name,bool passed)
        {
            checks.Add(new {name,passed});
            if(!passed)throw new InvalidOperationException("Fixture check failed: "+name);
        }
        Check("splash shown on restored native window",startupSplashShown && startupStartedAt>0);
        Check("native first background paint matches theme",startupBackgroundPaintSample==(dark?0x00202020u:0x00F3F3F3u));
        Check("native startup paint hook released after first splash frame",startupWindowBackground is null);
        Check("theme frame prepared before native reveal",startupFramePreparedWhileCloaked && !startupRevealFallbackUsed);
        Check("native window uncloaked after startup",!StartupWindowBackground.IsAppCloaked(hwnd) && startupRevealDeadline is null);
        Check("splash app icon uses larger 96 DIP size",startupLogo.Width==96 && startupLogo.Height==96);
        Check("full SVG contour shadow created and disposed after fade",startupContourShadowCreated && startupShadowVisual is null && startupShadow is null);
        Check("contour shadow stays broad faint and centred",startupShadowIsSoft);
        Check("whole native window fades through intermediate opacity",!AnimationsEnabled || startupWindowFade is {FinishedNormally:true,HadIntermediateAlpha:true,Frames:>1});
        Check("temporary native opacity mode restored after entrance",startupWindowFade is null || startupWindowFade.NativeStyleRestored);
        Check("splash and settings each rendered before reveal",splashFrameRendered && contentFrameRendered);
        Check("content ready follows initial splash",contentReadyAt>=startupStartedAt);
        Check("startup completed with real localized model",startupCompleted && !pagesInitializing && State.Labels.Count>0);
        Check("splash removed and settings interactive",!root.Children.Contains(startupSplash) && body.IsHitTestVisible);
        Check("content fade reached fully visible",Math.Abs(ElementCompositionPreview.GetElementVisual(body).Opacity-1)<0.001);
        Check("startup render and image subscriptions removed",startupSubscriptions.Count==0 && startupLogoSource is null);
        Check("no idle startup animation callbacks",startupFadeVersions.Count<=2);
        return Task.CompletedTask;
    }
}
