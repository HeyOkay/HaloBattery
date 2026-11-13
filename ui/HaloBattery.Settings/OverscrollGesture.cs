namespace HaloBattery.Settings;

// Raw wheel units, independent of frame rate, pointer hardware and WinUI.
internal sealed class OverscrollGesture
{
    internal const int Threshold = 600;
    internal const int IdleMilliseconds = 375;
    internal const int LatchMilliseconds = 350;
    internal const int ResetMilliseconds = 90;
    internal int Raw { get; private set; }
    internal int Direction { get; private set; }
    internal double Progress => Math.Min(1, (double)Raw / Threshold);
    internal bool IsCharged => Raw > 0;
    internal bool ConsumeLastInput { get; private set; }
    internal long LatchUntil { get; private set; }
    private long lastInput, resetUntil;

    internal int Feed(int delta, double offset, double height, bool canPrevious,
        bool canNext, long nowMs, double tolerance = 1)
    {
        Tick(nowMs);
        ConsumeLastInput = false;
        if (delta == 0) return 0;
        var direction = delta > 0 ? -1 : 1;
        // Reversal releases native scrolling immediately and lets the old ring
        // fade before another direction may start a new charge.
        if (Direction != 0 && direction != Direction)
        {
            Reset(true);
            resetUntil = nowMs + ResetMilliseconds;
            return 0;
        }
        var boundary = direction < 0 ? offset <= tolerance : offset >= height - tolerance;
        if (!boundary || !(direction < 0 ? canPrevious : canNext))
        {
            Reset(true);
            return 0;
        }
        if (nowMs < resetUntil) return 0;
        ConsumeLastInput = true;
        if (nowMs < LatchUntil) return 0;
        Direction = direction;
        lastInput = nowMs;
        Raw = (int)Math.Min(Threshold, Raw + Math.Abs((long)delta));
        if (Raw < Threshold) return 0;
        Reset(true);
        LatchUntil = nowMs + LatchMilliseconds;
        ConsumeLastInput = true;
        return direction;
    }

    internal void Tick(long nowMs)
    {
        if (IsCharged && nowMs - lastInput >= IdleMilliseconds)
        {
            var fadeFinished = lastInput + IdleMilliseconds + ResetMilliseconds;
            Reset(true);
            resetUntil = fadeFinished;
        }
    }

    internal void Reset(bool preserveLatch = false)
    {
        Raw = 0;
        Direction = 0;
        ConsumeLastInput = false;
        resetUntil = 0;
        if (!preserveLatch) LatchUntil = 0;
    }
}
