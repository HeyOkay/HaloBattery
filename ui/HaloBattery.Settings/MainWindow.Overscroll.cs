using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Windows.UI;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    internal readonly OverscrollGesture OverscrollState = new();
    private readonly Grid overscrollHost = new();
    // Only this wrapper moves. The HUD is a sibling, outside the page's pull.
    private readonly Grid overscrollContent = new();
    private readonly Grid overscrollIndicator = new()
    {
        Width = 16, Height = 16, IsHitTestVisible = false,
        HorizontalAlignment = HorizontalAlignment.Left,
        VerticalAlignment = VerticalAlignment.Top,
        Visibility = Visibility.Collapsed
    };
    private CompositionEllipseGeometry overscrollArc = null!;
    private CompositionColorBrush overscrollArcBrush = null!, overscrollTrackBrush = null!;
    private CompositionPropertySet overscrollPresence = null!;
    private ShapeVisual overscrollRingVisual = null!;
    private readonly Dictionary<string, ExpressionAnimation> overscrollPresenceAnimations = new();
    private SpringVector3NaturalMotionAnimation? overscrollLastPullSpring;
    private DispatcherTimer? overscrollTimer;
    private int overscrollVisualDirection;
    private string? overscrollTarget;
    private bool overscrollNavigation;
    private bool? overscrollDark;
    private long overscrollCompletionUntil, overscrollIdleUntil, overscrollHideUntil;
    private long overscrollGeneration;
    private float overscrollProgressTo, overscrollPresenceTo, overscrollPullTo;
    private readonly OverscrollSpringTrajectory overscrollPullMotion = new();
    private PendingOverscrollNavigation? pendingOverscrollNavigation;
    private List<object>? fixtureScrollEntryTrace;

    private sealed class PendingOverscrollNavigation(string source, string target, int direction, long generation)
    {
        internal readonly string Source = source, Target = target;
        internal readonly int Direction = direction;
        internal readonly long Generation = generation;
        internal CompositionScopedBatch? Batch;
    }

    // Targets are intentionally distinct from displayed pixels. Composition
    // getters return base values, not the compositor's current animated value.
    // QA can pause/seek TrimEnd and Presence via TryGetAnimationController and
    // capture the native child visual, rather than test a CPU animation mirror.
    internal float OverscrollProgressTarget => overscrollProgressTo;
    internal float OverscrollPresenceTarget => overscrollPresenceTo;
    internal float OverscrollPullTarget => overscrollPullTo;
    internal CompositionEllipseGeometry OverscrollProgressGeometry => overscrollArc;
    internal CompositionPropertySet OverscrollPresenceProperties => overscrollPresence;
    internal UIElement OverscrollPageElement => overscrollContent;
    internal ShapeVisual OverscrollRingVisual => overscrollRingVisual;
    internal bool OverscrollIndicatorVisible => overscrollIndicator.Visibility == Visibility.Visible;
    internal int OverscrollIndicatorDirection => overscrollVisualDirection;

    private UIElement BuildOverscrollHost(Frame content)
    {
        overscrollHost.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        overscrollHost.RowDefinitions.Add(new() { Height = GridLength.Auto });
        overscrollContent.Children.Add(content);
        overscrollHost.Children.Add(overscrollContent);
        Grid.SetRowSpan(overscrollIndicator, 2);
        Canvas.SetZIndex(overscrollIndicator, 1);
        overscrollHost.Children.Add(overscrollIndicator);
        var footer = BuildGeneralFooter();
        Grid.SetRow(footer, 1);
        overscrollHost.Children.Add(footer);
        content.SizeChanged += (_, _) => PositionOverscrollIndicator();
        var compositor = CompositionTarget.GetCompositorForCurrentThread();
        overscrollPresence = compositor.CreatePropertySet();
        overscrollPresence.InsertScalar("Presence", 0);
        overscrollPresence.InsertScalar("Edge", 1);
        overscrollRingVisual = compositor.CreateShapeVisual();
        overscrollRingVisual.Size = new Vector2(16);
        overscrollRingVisual.CenterPoint = new Vector3(8, 8, 0);
        overscrollArcBrush = compositor.CreateColorBrush();
        overscrollTrackBrush = compositor.CreateColorBrush();
        CompositionEllipseGeometry Circle()
        {
            var circle = compositor.CreateEllipseGeometry();
            circle.Center = new Vector2(8);
            circle.Radius = new Vector2(7.25f);
            return circle;
        }
        CompositionSpriteShape Stroke(CompositionEllipseGeometry geometry, CompositionColorBrush brush)
        {
            var shape = compositor.CreateSpriteShape(geometry);
            shape.StrokeBrush = brush;
            shape.StrokeThickness = 1.1f;
            shape.StrokeStartCap = CompositionStrokeCap.Round;
            shape.StrokeEndCap = CompositionStrokeCap.Round;
            return shape;
        }
        overscrollRingVisual.Shapes.Add(Stroke(Circle(), overscrollTrackBrush));
        overscrollArc = Circle();
        overscrollArc.TrimOffset = .25f;
        overscrollArc.TrimEnd = 0;
        var progressStroke = Stroke(overscrollArc, overscrollArcBrush);
        // Reflect the fill direction, then orient the trimmed arc to 12 o'clock.
        // Apply this only to the progress stroke, leaving the track and HUD fixed.
        progressStroke.TransformMatrix = Matrix3x2.CreateScale(-1, 1, new Vector2(8)) *
            Matrix3x2.CreateRotation(MathF.PI / 2, new Vector2(8));
        overscrollRingVisual.Shapes.Add(progressStroke);
        void Bind(string property, string expression)
        {
            var animation = compositor.CreateExpressionAnimation(expression);
            animation.SetReferenceParameter("cue", overscrollPresence);
            animation.SetReferenceParameter("arc", overscrollArc);
            overscrollPresenceAnimations[property] = animation;
            overscrollRingVisual.StartAnimation(property, animation);
        }
        // Fade/slide share presence; size follows the animated fill linearly.
        Bind("Opacity", "cue.Presence");
        Bind("Scale", "Vector3(0.5 + arc.TrimEnd, 0.5 + arc.TrimEnd, 1)");
        Bind("Offset.Y", "cue.Edge * 32 * (1 - cue.Presence)");
        ElementCompositionPreview.SetElementChildVisual(overscrollIndicator, overscrollRingVisual);
        overscrollHost.SizeChanged += (_, _) =>
        {
            overscrollHost.Clip = new RectangleGeometry
            {
                Rect = new Rect(0, 0, overscrollHost.ActualWidth, overscrollHost.ActualHeight)
            };
            PositionOverscrollIndicator();
        };
        overscrollHost.Unloaded += (_, _) => { ClearPendingOverscrollNavigation(); StopOverscrollRendering(); };
        Closed += (_, _) => { ClearPendingOverscrollNavigation(); OverscrollState.Reset(); StopOverscrollRendering(); };
        return overscrollHost;
    }

    private void PositionOverscrollIndicator()
    {
        var width = overscrollHost.ActualWidth;
        var x = PageLeft(width) + PageWidth(width) / 2 - 8;
        // The center travels from the host edge to 32 DIPs inside it.
        // Match the presence offset above so the hidden origin stays at the edge.
        var y = overscrollVisualDirection < 0 ? 24 : Math.Max(24, overscrollHost.ActualHeight - 40);
        overscrollIndicator.Margin = new Thickness(Math.Max(0, x), y, 0, 0);
    }

    private void FeedContentOverscroll(int delta, ScrollViewer scroll, PointerRoutedEventArgs args)
    {
        args.Handled = HandleContentOverscroll(delta, scroll.VerticalOffset, scroll.ScrollableHeight) || args.Handled;
    }

    internal bool HandleContentOverscroll(int delta, double offset, double scrollableHeight)
    {
        var pages = State.Pages.ToArray();
        var index = Array.FindIndex(pages, page => page.Id == current);
        if (index < 0 || closing || delta == 0) return false;
        if (DiscardWheelDuringPageEntry()) return true;
        // Once committed, finish the visible fill without queuing wheel input.
        if (pendingOverscrollNavigation is not null) return true;
        var now = Environment.TickCount64;
        var direction = OverscrollState.Feed(delta, offset,
            scrollableHeight, index > 0, index < pages.Length - 1, now, 1 / Scale);
        var consumed = OverscrollState.ConsumeLastInput;
        if (OverscrollState.IsCharged || direction != 0)
        {
            var cueDirection = direction != 0 ? direction : OverscrollState.Direction;
            var target = pages[index + cueDirection];
            if (overscrollTarget != target.Id || overscrollVisualDirection != cueDirection || overscrollDark != dark)
            {
                overscrollTarget = target.Id;
                overscrollVisualDirection = cueDirection;
                overscrollDark = dark;
                AutomationProperties.SetName(overscrollIndicator, target.Title);
                overscrollTrackBrush.Color = Color.FromArgb(dark ? (byte)0x28 : (byte)0x20,
                    dark ? (byte)255 : (byte)0, dark ? (byte)255 : (byte)0, dark ? (byte)255 : (byte)0);
                overscrollArcBrush.Color = Color.FromArgb(dark ? (byte)0xA0 : (byte)0x90,
                    dark ? (byte)255 : (byte)0, dark ? (byte)255 : (byte)0, dark ? (byte)255 : (byte)0);
                overscrollPresence.InsertScalar("Edge", cueDirection);
                PositionOverscrollIndicator();
            }
        }
        overscrollIdleUntil = OverscrollState.IsCharged ? now + OverscrollGesture.IdleMilliseconds : 0;
        if (direction != 0)
        {
            overscrollCompletionUntil = 0;
            pendingOverscrollNavigation = new(current, pages[index + direction].Id, direction, overscrollGeneration);
        }
        else if (!OverscrollState.IsCharged && !consumed) overscrollCompletionUntil = 0;
        RetargetOverscroll(now);
        if (!settings.AnimationsEnabled && pendingOverscrollNavigation is { } immediate)
            CompleteOverscrollNavigation(immediate);
        return consumed;
    }

    private void CompleteOverscrollNavigation(PendingOverscrollNavigation pending)
    {
        if (closing || !ReferenceEquals(pendingOverscrollNavigation, pending) ||
            pending.Generation != overscrollGeneration || current != pending.Source) return;
        var pages = State.Pages.ToArray();
        var sourceIndex = Array.FindIndex(pages, page => page.Id == pending.Source);
        var targetIndex = sourceIndex + pending.Direction;
        if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= pages.Length || pages[targetIndex].Id != pending.Target)
        {
            CancelOverscroll();
            return;
        }
        ClearPendingOverscrollNavigation();
        overscrollCompletionUntil = Environment.TickCount64 + 80;
        var next = pending.Target;
        overscrollNavigation = true;
        try { Navigate(next, true); }
        finally { overscrollNavigation = false; }
        // Upward overscroll enters the end of the previous page. Guard both
        // generation and page identity so subsequent navigation wins the race.
        if (pending.Direction < 0 && frame.Content is Page { Content: ScrollViewer entered })
        {
            var generation = overscrollGeneration;
            fixtureScrollEntryTrace?.Add(new { kind = "queued", generation, current, loaded = entered.IsLoaded,
                height = entered.ScrollableHeight, offset = entered.VerticalOffset, tick = Environment.TickCount64 });
            if (fixtureScrollEntryTrace is not null)
                entered.ViewChanged += (_, args) => fixtureScrollEntryTrace?.Add(new { kind = "viewChanged", current,
                    height = entered.ScrollableHeight, offset = entered.VerticalOffset, args.IsIntermediate, tick = Environment.TickCount64 });
            DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Normal, () =>
            {
                fixtureScrollEntryTrace?.Add(new { kind = "callback", generation, actualGeneration = overscrollGeneration,
                    current, next, loaded = entered.IsLoaded, height = entered.ScrollableHeight, offset = entered.VerticalOffset, tick = Environment.TickCount64 });
                if (!closing && generation == overscrollGeneration && current == next &&
                    frame.Content is Page { Content: ScrollViewer active } && ReferenceEquals(active, entered))
                {
                    root.UpdateLayout();
                    var accepted = entered.ChangeView(null, entered.ScrollableHeight, null, true);
                    fixtureScrollEntryTrace?.Add(new { kind = "changeView", accepted, current, loaded = entered.IsLoaded,
                        height = entered.ScrollableHeight, offset = entered.VerticalOffset, tick = Environment.TickCount64 });
                }
            });
        }
    }

    private void ClearPendingOverscrollNavigation()
    {
        var pending = pendingOverscrollNavigation;
        pendingOverscrollNavigation = null;
        if (pending is null) return;
        var batch = pending.Batch;
        pending.Batch = null;
        batch?.Dispose();
    }

    private void ResetOverscrollForNavigation()
    {
        var now = Environment.TickCount64;
        ClearPendingOverscrollNavigation();
        overscrollGeneration++;
        overscrollIdleUntil = 0;
        if (!overscrollNavigation) overscrollCompletionUntil = 0;
        OverscrollState.Reset(overscrollNavigation);
        // The newly entered page uses its existing entry animation only.
        // A previous page's elastic translation must never carry into it.
        // Assign through XAML while the explicit spring is still registered.
        // This cancels it without later writing its interrupted value back.
        overscrollContent.Translation = Vector3.Zero;
        overscrollPullTo = 0;
        overscrollPullMotion.Snap(0, now);
        overscrollLastPullSpring = null;
        RetargetOverscroll(now);
    }

    private void CancelOverscroll()
    {
        ClearPendingOverscrollNavigation();
        overscrollGeneration++;
        overscrollIdleUntil = overscrollCompletionUntil = 0;
        OverscrollState.Reset(true);
        RetargetOverscroll(Environment.TickCount64);
    }

    private void RetargetOverscroll(long now)
    {
        if (overscrollPresence is null) return;
        var completing = pendingOverscrollNavigation is not null || now < overscrollCompletionUntil;
        var progress = completing ? 1f : (float)OverscrollState.Progress;
        var presence = OverscrollState.IsCharged || completing ? 1f : 0f;
        if (progress != overscrollProgressTo || pendingOverscrollNavigation is { Batch: null })
        {
            overscrollProgressTo = progress;
            AnimateOverscrollScalar(overscrollArc, "TrimEnd", progress,
                progress == 0 ? OverscrollGesture.ResetMilliseconds : 150, pendingOverscrollNavigation);
        }
        if (presence != overscrollPresenceTo)
        {
            overscrollPresenceTo = presence;
            if (presence > 0)
            {
                overscrollHideUntil = 0;
                overscrollIndicator.Visibility = Visibility.Visible;
            }
            else overscrollHideUntil = now + (settings.AnimationsEnabled ? OverscrollGesture.ResetMilliseconds : 0);
            AnimateOverscrollScalar(overscrollPresence, "Presence", presence,
                presence == 0 ? OverscrollGesture.ResetMilliseconds : 160);
        }
        // Resistance saturates at 32 DIPs, independent of wheel packet size.
        var pullProgress = pendingOverscrollNavigation is not null ? 1 : OverscrollState.Progress;
        var pull = OverscrollState.IsCharged || pendingOverscrollNavigation is not null
            ? (float)(-overscrollVisualDirection * 32 * (1 - Math.Exp(-3 * pullProgress)) / (1 - Math.Exp(-3)))
            : 0;
        if (pull != overscrollPullTo)
        {
            overscrollPullTo = pull;
            var damping = pull == 0 ? .8f : 1f;
            var period = pull == 0 ? 50 : 300;
            var velocity = overscrollPullMotion.Retarget(pull, now, damping, period);
            if (settings.AnimationsEnabled)
            {
                var spring = overscrollArc.Compositor.CreateSpringVector3Animation();
                spring.Target = "Translation";
                spring.FinalValue = new Vector3(0, pull, 0);
                spring.InitialVelocity = new Vector3(0, (float)velocity, 0);
                spring.DampingRatio = damping;
                spring.Period = TimeSpan.FromMilliseconds(period);
                overscrollLastPullSpring = spring;
                // No InitialValue: interruptions start at the effective position.
                overscrollContent.StartAnimation(spring);
            }
            else
            {
                overscrollContent.Translation = new Vector3(0, pull, 0);
                overscrollLastPullSpring = null;
                overscrollPullMotion.Snap(pull, now);
            }
        }
        if (overscrollHideUntil != 0 && now >= overscrollHideUntil)
        {
            overscrollIndicator.Visibility = Visibility.Collapsed;
            overscrollHideUntil = 0;
        }
        ScheduleOverscrollDeadline(now);
    }

    private void AnimateOverscrollScalar(CompositionObject target, string property, float value, int duration,
        PendingOverscrollNavigation? pending = null)
    {
        if (!settings.AnimationsEnabled)
        {
            target.StopAnimation(property);
            if (target is CompositionEllipseGeometry geometry) geometry.TrimEnd = value;
            else ((CompositionPropertySet)target).InsertScalar(property, value);
            return;
        }
        var animation = target.Compositor.CreateScalarKeyFrameAnimation();
        animation.Duration = TimeSpan.FromMilliseconds(duration);
        CompositionEasingFunction ease = property == "TrimEnd"
            ? target.Compositor.CreateLinearEasingFunction()
            : target.Compositor.CreateCubicBezierEasingFunction(new Vector2(.2f, .8f), new Vector2(.2f, 1));
        // Omitting a starting keyframe preserves the current presentation value
        // when another wheel packet retargets an animation already in flight.
        animation.InsertKeyFrame(1, value, ease);
        // Scope only the final fill, so page movement and presence animations
        // cannot delay navigation or make a wall-clock timer switch too early.
        if (pending is not null)
        {
            var batch = target.Compositor.CreateScopedBatch(CompositionBatchTypes.Animation);
            pending.Batch = batch;
            batch.Completed += (_, _) => DispatcherQueue.TryEnqueue(() => CompleteOverscrollNavigation(pending));
            target.StartAnimation(property, animation);
            batch.End();
            return;
        }
        target.StartAnimation(property, animation);
    }

    private void ScheduleOverscrollDeadline(long now)
    {
        StopOverscrollRendering();
        if (closing) return;
        var deadlines = new[] { overscrollIdleUntil, overscrollCompletionUntil, overscrollHideUntil };
        var next = deadlines.Where(deadline => deadline > now).DefaultIfEmpty(0).Min();
        if (next == 0) return;
        // Semantic one-shot deadlines only. Composition owns every visual frame.
        overscrollTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(Math.Max(1, next - now)) };
        overscrollTimer.Tick += (_, _) =>
        {
            StopOverscrollRendering();
            var tick = Environment.TickCount64;
            OverscrollState.Tick(tick);
            if (!OverscrollState.IsCharged) overscrollIdleUntil = 0;
            RetargetOverscroll(tick);
        };
        overscrollTimer.Start();
    }

    private void StopOverscrollRendering()
    {
        overscrollTimer?.Stop();
        overscrollTimer = null;
    }
}

// An event-time solution of the same damped oscillator supplies velocity when
// native motion is interrupted. It never renders or schedules a frame.
internal sealed class OverscrollSpringTrajectory
{
    private double initial, velocity, target, damping = 1;
    private long started;
    private double frequency = 2 * Math.PI / .3;

    internal double Retarget(double value, long now, double dampingRatio, double periodMs)
    {
        var (position, speed) = Sample(now);
        initial = position;
        velocity = speed;
        target = value;
        damping = dampingRatio;
        frequency = 2 * Math.PI / (periodMs / 1000);
        started = now;
        return speed;
    }

    internal void Snap(double value, long now)
    {
        initial = target = value;
        velocity = 0;
        started = now;
    }

    private (double Position, double Velocity) Sample(long now)
    {
        var time = Math.Max(0, (now - started) / 1000.0);
        var displacement = initial - target;
        if (damping >= 1)
        {
            var b = velocity + frequency * displacement;
            var envelope = Math.Exp(-frequency * time);
            return (target + (displacement + b * time) * envelope,
                (b - frequency * (displacement + b * time)) * envelope);
        }
        var decay = damping * frequency;
        var oscillation = frequency * Math.Sqrt(1 - damping * damping);
        var sineCoefficient = (velocity + decay * displacement) / oscillation;
        var cosine = Math.Cos(oscillation * time);
        var sine = Math.Sin(oscillation * time);
        var wave = displacement * cosine + sineCoefficient * sine;
        var derivative = oscillation * (-displacement * sine + sineCoefficient * cosine);
        var attenuation = Math.Exp(-decay * time);
        return (target + attenuation * wave, attenuation * (derivative - decay * wave));
    }
}
