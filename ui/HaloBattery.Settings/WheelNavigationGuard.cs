namespace HaloBattery.Settings;

// One fixed deadline per wheel-initiated page transition. Continuing input
// never extends it; pending positioning and the entry animation also block.
internal sealed class WheelNavigationGuard
{
    internal const int CooldownMilliseconds = 450;
    private long until;

    internal void Arm(long now) => until = now + CooldownMilliseconds;
    internal bool IsBlocked(long now, bool transitionActive) => transitionActive || now < until;
    internal bool Consume(long now, bool transitionActive) => IsBlocked(now, transitionActive);
    internal void Reset() => until = 0;
}
