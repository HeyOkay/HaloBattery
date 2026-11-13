using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Composition;
using System.Numerics;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyOverscrollNavigation(string destination, List<object> checks)
    {
        fixtureScrollEntryTrace = new();
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var gesture = new OverscrollGesture();
        Check("overscroll accumulates raw small deltas", gesture.Feed(-17, 100, 100, true, true, 1000) == 0 &&
            gesture.Feed(-23, 100, 100, true, true, 1020) == 0 && gesture.Raw == 40);
        Check("overscroll reversal clears resistance", gesture.Feed(7, 100, 100, true, true, 1040) == 0 && gesture.Raw == 0 && !gesture.ConsumeLastInput);
        gesture = new OverscrollGesture();
        gesture.Feed(-100, 100, 100, true, true, 1000);
        gesture.Tick(1000 + OverscrollGesture.IdleMilliseconds - 1);
        Check("overscroll retains charge until the idle deadline", gesture.Raw == 100);
        gesture.Tick(1000 + OverscrollGesture.IdleMilliseconds);
        Check("overscroll pause clears accumulated resistance", gesture.Raw == 0);
        gesture = new OverscrollGesture();
        Check("overscroll preserves a steady wheel cadence", gesture.Feed(-120, 100, 100, true, true, 1000) == 0 &&
            gesture.Feed(-120, 100, 100, true, true, 1300) == 0 &&
            gesture.Feed(-120, 100, 100, true, true, 1600) == 0 &&
            gesture.Feed(-120, 100, 100, true, true, 1900) == 0 &&
            gesture.Feed(-120, 100, 100, true, true, 2200) == 1);
        gesture = new OverscrollGesture();
        Check("overscroll waits below threshold", gesture.Feed(-(OverscrollGesture.Threshold - 1), 100, 100, true, true, 1000) == 0 && gesture.IsCharged);
        Check("overscroll switches exactly at threshold", gesture.Feed(-1, 100, 100, true, true, 1010) == 1 && gesture.ConsumeLastInput);
        Check("overscroll discards excess during latch", gesture.Feed(-5000, 0, 0, true, true, 1020) == 0 && gesture.Raw == 0);
        gesture = new OverscrollGesture();
        Check("overscroll huge packet advances one adjacent page", gesture.Feed(int.MinValue, 0, 0, true, true, 1000) == 1);
        gesture = new OverscrollGesture();
        Check("overscroll ignores interior scrolling", gesture.Feed(-300, 50, 100, true, true, 1000) == 0 && gesture.Raw == 0 && !gesture.ConsumeLastInput);
        Check("overscroll stays within first and last pages", gesture.Feed(300, 0, 100, false, true, 1010) == 0 && gesture.Raw == 0 &&
            gesture.Feed(-300, 100, 100, true, false, 1020) == 0 && gesture.Raw == 0);

        Navigate("general", false);
        await Task.Delay(100);
        HandleContentOverscroll(-90, 0, 0);
        await Task.Delay(25);
        Check("composition ring uses a compact base without glyphs", overscrollIndicator.Width == 16 && overscrollIndicator.Height == 16 &&
            overscrollIndicator.Children.Count == 0 && OverscrollRingVisual.Shapes.Count == 2);
        Check("overscroll target accumulates partial raw progress", current == "general" && OverscrollProgressTarget == .15f && OverscrollIndicatorVisible);
        root.UpdateLayout();
        var indicatorTop = overscrollIndicator.TransformToVisual(overscrollHost).TransformPoint(new Windows.Foundation.Point()).Y;
        Check("General indicator stops 32 DIP inside the full host edge below the fixed footer", generalFooter?.ActualHeight > 0 &&
            Grid.GetRowSpan(overscrollIndicator) == 2 && Canvas.GetZIndex(overscrollIndicator) > Canvas.GetZIndex(generalFooter) &&
            Math.Abs(indicatorTop - (overscrollHost.ActualHeight - 40)) <= 1 / Scale,
            new { indicatorTop, hostHeight = overscrollHost.ActualHeight, frameHeight = frame.ActualHeight, footerHeight = generalFooter?.ActualHeight });
        var progressClock = OverscrollProgressGeometry.TryGetAnimationController("TrimEnd");
        var presenceClock = OverscrollPresenceProperties.TryGetAnimationController("Presence");
        if (AnimationsEnabled)
            Check("progress and presence run on native animation clocks", progressClock is not null && presenceClock is not null);
        Check("fade and slide share presence", overscrollPresenceAnimations.Count == 3 &&
            overscrollPresenceAnimations["Opacity"].Expression == "cue.Presence" &&
            overscrollPresenceAnimations["Offset.Y"].Expression == "cue.Edge * 32 * (1 - cue.Presence)");
        Check("indicator grows linearly with animated fill", overscrollPresenceAnimations["Scale"].Expression ==
            "Vector3(0.5 + arc.TrimEnd, 0.5 + arc.TrimEnd, 1)");
        var stroke = OverscrollRingVisual.Shapes[1] as CompositionSpriteShape;
        var transform = stroke?.TransformMatrix ?? Matrix3x2.Identity;
        Check("arc maps its native trim origin to twelve and grows counterclockwise", stroke is not null &&
            ReferenceEquals(stroke.Geometry, overscrollArc) && overscrollArc.TrimOffset == .25f &&
            Vector2.Distance(Vector2.Transform(new Vector2(15.25f, 8), transform), new Vector2(8, .75f)) < .0001f &&
            Vector2.Distance(Vector2.Transform(new Vector2(8, 15.25f), transform), new Vector2(.75f, 8)) < .0001f);
        Check("overscroll follows neutral theme", overscrollArcBrush.Color.A == (dark ? 160 : 144) &&
            overscrollArcBrush.Color.R == (dark ? 255 : 0) && overscrollArcBrush.Color.G == (dark ? 255 : 0) && overscrollArcBrush.Color.B == (dark ? 255 : 0),
            new { color = overscrollArcBrush.Color.ToString() });
        Check("bottom overscroll pulls content within 32 DIP", OverscrollPullTarget < 0 && OverscrollPullTarget >= -32,
            new { target = OverscrollPullTarget });
        var layoutEvents = 0;
        Microsoft.UI.Xaml.SizeChangedEventHandler sized = (_, _) => layoutEvents++;
        frame.SizeChanged += sized;
        await Task.Delay(85);
        frame.SizeChanged -= sized;
        Check("elastic transforms do not resize page layout", layoutEvents == 0, new { sizeChanges = layoutEvents });
        checks.Add(new { name = "composition playback telemetry only", diagnostic = true,
            progress = progressClock?.Progress, presence = presenceClock?.Progress,
            note = "Native animation playback position is not presented-frame or pixel readback." });
        HandleContentOverscroll(20, 0, 0);
        Check("reverse resets raw charge and compositor targets", OverscrollState.Raw == 0 && OverscrollProgressTarget == 0 && OverscrollPresenceTarget == 0 && OverscrollPullTarget == 0);
        if (AnimationsEnabled)
        {
            Check("reverse keeps progress reset animated", OverscrollProgressGeometry.TryGetAnimationController("TrimEnd") is not null);
            Check("content returns with a stronger fast native spring", overscrollLastPullSpring is not null &&
                overscrollLastPullSpring.FinalValue == Vector3.Zero && overscrollLastPullSpring.Target == "Translation" &&
                overscrollLastPullSpring.DampingRatio == .8f && overscrollLastPullSpring.Period.TotalMilliseconds == 50);
        }
        await Task.Delay(OverscrollGesture.ResetMilliseconds + 40);
        Check("overscroll reset finishes hidden", !OverscrollIndicatorVisible && OverscrollPresenceTarget == 0);
        Navigate("devices", false);
        await Task.Delay(100);
        HandleContentOverscroll(90, 0, 1000);
        Check("top overscroll stops 32 DIP inside with opposite content pull", OverscrollIndicatorDirection == -1 && overscrollIndicator.Margin.Top == 24 && OverscrollPullTarget > 0 && OverscrollPullTarget <= 32);
        await Task.Delay(OverscrollGesture.IdleMilliseconds + OverscrollGesture.ResetMilliseconds + 80);
        Check("idle clears charge and all animation targets", OverscrollState.Raw == 0 && !OverscrollIndicatorVisible && OverscrollProgressTarget == 0 && OverscrollPullTarget == 0);
        Navigate("general", false);
        await Task.Delay(100);
        HandleContentOverscroll(-(OverscrollGesture.Threshold - 1), 0, 0);
        Check("overscroll UI waits below threshold", current == "general");
        HandleContentOverscroll(-1, 0, 0);
        if (AnimationsEnabled)
        {
            var finalFill = OverscrollProgressGeometry.TryGetAnimationController("TrimEnd");
            Check("threshold starts a native final fill before navigation", current == "general" &&
                pendingOverscrollNavigation is not null && finalFill is not null && OverscrollProgressTarget == 1);
            finalFill!.Pause();
            finalFill.Progress = .5f;
            await Task.Delay(220);
            Check("paused incomplete fill prevents navigation beyond animation duration", current == "general" &&
                pendingOverscrollNavigation is not null && OverscrollIndicatorVisible && OverscrollPresenceTarget == 1);
            Check("pending fill consumes excess and opposite wheel packets", HandleContentOverscroll(int.MinValue, 0, 0) &&
                HandleContentOverscroll(int.MaxValue, 0, 0) && current == "general" && OverscrollState.Raw == 0);
            finalFill.Resume();
        }
        else Check("disabled animation fills immediately before navigation", current == "devices" &&
            OverscrollProgressGeometry.TrimEnd == 1 && pendingOverscrollNavigation is null);
        for (var i = 0; current != "devices" && i < 50; i++) await Task.Delay(20);
        Check("completed fill opens exactly the adjacent page", current == "devices" && pendingOverscrollNavigation is null && OverscrollProgressTarget == 1);
        var translation = OverscrollPageElement.Translation;
        Check("page transition clears elastic translation and spring",
            translation == Vector3.Zero && overscrollLastPullSpring is null && OverscrollPullTarget == 0);
        await Task.Delay(240);
        Check("completion releases indicator and elastic target", !OverscrollIndicatorVisible && OverscrollPresenceTarget == 0 && OverscrollPullTarget == 0);
        Check("completion does not restart page spring", overscrollLastPullSpring is null);
        if (AnimationsEnabled)
        {
            Navigate("general", false);
            HandleContentOverscroll(int.MinValue, 0, 0);
            OverscrollProgressGeometry.TryGetAnimationController("TrimEnd")!.Pause();
            Navigate("icons", false);
            await Task.Delay(220);
            Check("manual navigation cancels a pending fill without a stale transition", current == "icons" && pendingOverscrollNavigation is null);
            Navigate("general", false);
            HandleContentOverscroll(-OverscrollGesture.Threshold, 0, 0);
            OverscrollProgressGeometry.TryGetAnimationController("TrimEnd")!.Pause();
            CancelOverscroll();
            await Task.Delay(220);
            Check("cancelled pending fill cannot navigate later", current == "general" && pendingOverscrollNavigation is null && OverscrollProgressTarget == 0);
        }
        Navigate("icons", false);
        await Task.Delay(150);
        HandleContentOverscroll(OverscrollGesture.Threshold, 0, 1000);
        for (var i = 0; current != "devices" && i < 50; i++) await Task.Delay(20);
        await Task.Delay(50);
        root.UpdateLayout();
        var entered = frame.Content is Page { Content: ScrollViewer active } ? active : null;
        Check("overscroll backward enters previous page at end", current == "devices" && entered is not null &&
            Math.Abs(entered.VerticalOffset - entered.ScrollableHeight) <= 1 / Scale,
            new { current, offset = entered?.VerticalOffset, height = entered?.ScrollableHeight, trace = fixtureScrollEntryTrace });
        ResetOverscrollForNavigation();
        await Task.Delay(260);
    }
}
