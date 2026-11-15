using System.Diagnostics;
using HaloBattery.Settings.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    private async Task VerifyScrollBarFallback(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var appResources = Application.Current.Resources;
        var installed = appResources[typeof(ScrollBar)];
        appResources.Remove(typeof(ScrollBar));
        try
        {
            foreach (var fault in new[] { "missing", "non-style", "wrong-target" })
            {
                var resources = new ResourceDictionary();
                if (fault == "non-style") resources["DefaultScrollBarStyle"] = "not a style";
                if (fault == "wrong-target") resources["DefaultScrollBarStyle"] = new Style(typeof(Button));
                Check(fault + ": cosmetic installation safely declines the resource", !ScrollBarAppearance.Install(resources) &&
                    !resources.ContainsKey(typeof(ScrollBar)));
                // Exercise real native template creation after the optional
                // install failed, with no application cosmetic style present.
                var scroll = new ScrollViewer
                {
                    VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                    Content = new Border { Height = 1200 },
                    RequestedTheme = dark ? ElementTheme.Dark : ElementTheme.Light
                };
                var fallback = new Window { Content = scroll };
                try
                {
                    fallback.AppWindow.Resize(new Windows.Graphics.SizeInt32(360, 300));
                    fallback.Activate();
                    for (var i = 0; !scroll.IsLoaded && i < 40; i++) await Task.Delay(25);
                    scroll.UpdateLayout();
                    var bar = PageScrollBar(scroll);
                    Check(fault + ": fallback window loads the native scrollbar template", scroll.IsLoaded && bar is not null &&
                        !ScrollBarAppearance.GetEnabled(bar) && TemplateRoot(bar) is not null && scroll.ScrollableHeight > 0);
                    scroll.ChangeView(null, scroll.ScrollableHeight, null, true);
                    for (var i = 0; scroll.VerticalOffset <= 0 && i < 20; i++) await Task.Delay(25);
                    Check(fault + ": fallback window remains scrollable", scroll.VerticalOffset > 0,
                        new { scroll.VerticalOffset, scroll.ScrollableHeight });
                }
                finally { fallback.Close(); }
            }
        }
        finally { appResources[typeof(ScrollBar)] = installed; Activate(); }
    }

    private async Task VerifyPageEntryRecovery(List<object> checks)
    {
        void Check(string name, bool passed, object? actual = null)
        {
            checks.Add(new { name, passed, actual });
            if (!passed) throw new InvalidOperationException("Fixture check failed: " + name);
        }
        var ignoredRequests = 0;
        async Task<(Page page, ScrollViewer scroll)> BlockEntry(bool animate)
        {
            Navigate("about", false);
            ignoredRequests = 0;
            fixtureEntryChangeView = _ => ignoredRequests++;
            Navigate("devices", animate, enterAtEnd: true);
            for (var i = 0; pendingEntryScroll is null && i < 30; i++) await Task.Delay(10);
            await Task.Delay(60); // Let the queued landing check make its retry.
            var page = (Page)frame.Content;
            var scroll = (ScrollViewer)page.Content;
            Check("fault injection ignores real entry positioning and retry", ignoredRequests >= 2 && scroll.ScrollableHeight > 0 &&
                scroll.VerticalOffset == 0 && ReferenceEquals(pendingEntryPage, page) && page.Opacity == 0 && DiscardNavigationWheel(),
                new { ignoredRequests, scroll.ScrollableHeight, scroll.VerticalOffset, page.Opacity });
            return (page, scroll);
        }
        async Task AwaitRecovery(string fault, Page page)
        {
            var watch = Stopwatch.StartNew();
            for (var i = 0; PageEntryBlocksWheel && i < 70; i++) await Task.Delay(20);
            Check(fault + ": page becomes visible and wheel input resumes within a bounded interval", !PageEntryBlocksWheel &&
                page.Opacity == 1 && !DiscardNavigationWheel() && pendingEntryScroll is null && pendingEntryViewChanged is null &&
                pendingEntryTimer is null && watch.ElapsedMilliseconds < 1400,
                new { page.Opacity, wheelBlocked = PageEntryBlocksWheel, elapsedMs = watch.ElapsedMilliseconds });
        }
        try
        {
            var ignored = await BlockEntry(true);
            await AwaitRecovery("ignored ChangeView", ignored.page);
            Check("ignored ChangeView leaves the best available view usable", ignored.scroll.VerticalOffset == 0 && ignoredRequests >= 3,
                new { ignored.scroll.VerticalOffset, ignoredRequests });

            var missingEvent = await BlockEntry(true);
            // Move the real native viewport while suppressing the final event
            // consumer. The initial dispatcher retry already ran while at top.
            missingEvent.scroll.ViewChanged -= pendingEntryViewChanged;
            missingEvent.scroll.ChangeView(null, missingEvent.scroll.ScrollableHeight, null, true);
            for (var i = 0; missingEvent.scroll.VerticalOffset == 0 && i < 10; i++) await Task.Delay(20);
            Check("missing final event leaves a landed native viewport hidden before recovery", PageEntryBlocksWheel &&
                missingEvent.page.Opacity == 0 && Math.Abs(missingEvent.scroll.VerticalOffset - missingEvent.scroll.ScrollableHeight) <= 1 / Scale);
            await AwaitRecovery("missing final ViewChanged", missingEvent.page);
            Check("missing final event recovery retains the bottom landing",
                Math.Abs(missingEvent.scroll.VerticalOffset - missingEvent.scroll.ScrollableHeight) <= 1 / Scale);

            var superseded = await BlockEntry(false);
            var supersededTimer = pendingEntryTimer;
            Navigate("about", true);
            var replacement = frame.Content;
            Check("superseding navigation cancels the old positioning deadline", supersededTimer is { IsRunning: false } &&
                pendingEntryTimer is null && superseded.page.Opacity == 1);
            for (var i = 0; PageEntryBlocksWheel && i < 40; i++) await Task.Delay(20);
            await Task.Delay(500);
            Check("expired old deadline cannot change the replacement page", ReferenceEquals(frame.Content, replacement) &&
                current == "about" && !PageEntryBlocksWheel && ((Page)replacement).Opacity == 1 && pageHosts.Single().scroll.VerticalOffset == 0);

            var hidden = await BlockEntry(false);
            var hiddenTimer = pendingEntryTimer;
            await HideSettingsWindowAsync();
            Check("hiding cancels the positioning deadline and restores the retained page", IsSettingsWindowHidden &&
                hiddenTimer is { IsRunning: false } && pendingEntryTimer is null && hidden.page.Opacity == 1 && !PageEntryBlocksWheel && pageEntryWheelHook == 0 && pageEntryWheelTimer is null);
            await Task.Delay(500);
            ShowSettingsWindow(null, null);
            Check("reopening after a canceled deadline retains visible usable content", ReferenceEquals(frame.Content, hidden.page) &&
                !IsSettingsWindowHidden && hidden.page.Opacity == 1 && !DiscardNavigationWheel());
        }
        finally { fixtureEntryChangeView = null; ResetPageEntry(); }
    }
}
