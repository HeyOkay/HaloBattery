namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private static void VerifyWheelBurstState(List<object> checks)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var guard = new WheelNavigationGuard();
        Check("fresh wheel is accepted without a transition", !guard.Consume(0, false));
        guard.Arm(1000);
        Check("wheel navigation has a fixed 450 ms cooldown", guard.IsBlocked(1449, false) && !guard.IsBlocked(1450, false));
        var blocked = true;
        for (var now = 1000; now < 1450; now++) blocked &= guard.Consume(now, false);
        Check("continuous packets cannot extend the fixed deadline", blocked && !guard.Consume(1450, false) && !guard.Consume(1451, false));
        Check("continued spinning remains accepted after expiry without a quiet gap", !guard.Consume(1452, false) && !guard.Consume(2000, false));
        guard.Arm(0);
        Check("paused page entry stays blocked after the deadline", guard.Consume(20000, true));
        Check("finishing a long entry releases immediately without waiting for idle", !guard.Consume(20000, false));
        Check("manual page entry adds no post-animation deadline", guard.Consume(30000, true) && !guard.Consume(30000, false));
        guard.Arm(30000);
        guard.Reset();
        Check("intentional manual navigation clears the previous cooldown", !guard.Consume(30100, false));
    }

    private async Task VerifyWheelBurstAfterEntry(List<object> checks, string phase)
    {
        void Check(string name, bool passed)
        {
            checks.Add(new { name = phase + ": " + name, passed });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + phase + ": " + name);
        }
        Check("entry has finished before testing the fixed native cooldown", !PageEntryBlocksWheel);
        var page = current;
        var scroll = pageHosts.Single().scroll;
        var offset = scroll.VerticalOffset;
        var dropped = discardedEntryWheelMessages;
        var originalVertical = scroll.VerticalScrollMode;
        var originalHorizontal = scroll.HorizontalScrollMode;
        // Exercise a complete fixed window against the native message filter,
        // independently of how much time the preceding paused entry consumed.
        var began = Environment.TickCount64;
        ArmWheelNavigation(began);
        Check("page intercepts wheel before native scrolling while clicks and layout stay enabled", scroll.Content is Microsoft.UI.Xaml.Controls.Grid { Background: not null, IsHitTestVisible: true } &&
            scroll.IsEnabled && scroll.IsHitTestVisible && scroll.VerticalScrollBarVisibility == Microsoft.UI.Xaml.Controls.ScrollBarVisibility.Auto);
        var posted = true;
        int packets = 0;
        do
        {
            posted &= PostFixtureWheel(hwnd, 0x020A, packets % 2 == 0 ? 120u << 16 : unchecked((nuint)(uint)(-120 << 16)), 0);
            posted &= PostFixtureWheel(hwnd, 0x020E, 120u << 16, 0);
            packets++;
            await Task.Delay(25);
            if (packets == 4)
                Check("native wheel is discarded inside the fixed window", posted && discardedEntryWheelMessages >= dropped + 8 &&
                    current == page && navigationWheelRemainder == 0 && OverscrollState.Raw == 0 && Math.Abs(scroll.VerticalOffset - offset) <= 1 / Scale);
        } while (Environment.TickCount64 - began < WheelNavigationGuard.CooldownMilliseconds + 150);
        Check("continuous native packets cannot prolong the cooldown", posted && !wheelNavigationGuard.IsBlocked(Environment.TickCount64, false) &&
            discardedEntryWheelMessages > dropped && discardedEntryWheelMessages < dropped + packets * 2);
        Check("wheel guard leaves original native scroll modes unchanged",
            scroll.VerticalScrollMode == originalVertical && scroll.HorizontalScrollMode == originalHorizontal);
        int remainder = 0;
        Check("a partial notch works immediately after expiry without waiting for idle", !AdvanceWheelPage(-60, ref remainder) &&
            remainder == -60 && current == page && pendingOverscrollNavigation is null);
    }
}
