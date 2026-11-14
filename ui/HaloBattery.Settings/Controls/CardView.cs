// Native WinUI card extracted from Chrome Mica Patcher Controls/CardView.cs.
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
namespace HaloBattery.Settings.Controls;

// Карточка макета v3: одна панель со скруглением 20 и обводкой, заголовок (иконка, название, описание,
// действие, стрелка) и раскрываемое тело. Низ скруглён всегда (скругляется внешняя рамка), раскрытие
// и сворачивание идут столько же, сколько переход страниц (300 мс).
internal sealed class CardView : UserControl
{
    private readonly Border frame = new();
    // Подсветка рамки под курсором (как Reveal в Windows 10): отдельный слой поверх рамки, ничего не меняет в раскладке
    // и не мешает эффекту наведения - это прозрачная рамка той же формы с радиальным градиентом вокруг курсора.
    private readonly RadialGradientBrush spot = new() { MappingMode = BrushMappingMode.Absolute, RadiusX = 93, RadiusY = 93, Center = new Windows.Foundation.Point(-1000, -1000), GradientOrigin = new Windows.Foundation.Point(-1000, -1000) };
    private readonly Border glow = new() { CornerRadius = new CornerRadius(20), BorderThickness = new Thickness(1), IsHitTestVisible = false };
    internal const double ChevronSpace = 40; // стрелка 32 + отступ 8 - столько резервируют карточки без раскрытия
    private const double Duration = 300;
    private readonly MainWindow ui;
    private readonly Grid head = new() { Padding = new Thickness(24, 16, 24, 16), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
    private readonly ContentControl iconHost = new() { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 16, 0), IsTabStop = false };
    private readonly StackPanel texts = new() { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
    private readonly ContentControl titleHost = new() { IsTabStop = false };
    private readonly ContentControl descriptionHost = new() { IsTabStop = false };
    private readonly ContentControl actionHost = new() { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0), IsTabStop = false };
    private readonly Button? chevron;
    private readonly RotateTransform chevronTurn = new();
    // Тело карточки всегда в разметке и раскладывается один раз: оно лежит на Canvas (тот не ограничивает высоту),
    // а раскрытие меняет только высоту и обрезку окна bodyHost. Содержимое при раскрытии/сворачивании не перестраивается.
    private readonly Grid bodyHost = new() { Height = 0, IsHitTestVisible = false };
    private readonly Canvas bodyCanvas = new();
    private readonly RectangleGeometry clip = new() { Rect = new Windows.Foundation.Rect(0, 0, 0, 0) };
    private readonly StackPanel body = new();
    private readonly Border divider = new();
    private bool expanded;
    private EventHandler<object>? tick;

    public UIElement? TitleElement => titleHost.Content as UIElement;
    public UIElement? DescriptionElement => descriptionHost.Content as UIElement;
    public UIElement? ActionElement => actionHost.Content as UIElement;
    public bool HasIcon => iconHost.Content is not null;
    public bool Expandable => chevron is not null;
    public IEnumerable<UIElement> BodyElements => body.Children;
    // Entry fade can target content without changing the panel fill/border.
    internal IEnumerable<UIElement> EntryFadeTargets => head.Children.Cast<UIElement>().Append(divider);

    public CardView(MainWindow ui, UIElement? icon, UIElement title, UIElement? description, UIElement? action, bool expandable)
    {
        this.ui = ui;
        frame.CornerRadius = new CornerRadius(20);
        frame.BorderThickness = new Thickness(1);
        frame.BorderBrush = ui.LineBrush;
        frame.Background = ui.CardBrush;
        HorizontalAlignment = HorizontalAlignment.Stretch;
        IsTabStop = false;

        foreach (var width in new[] { GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto, GridLength.Auto })
            head.ColumnDefinitions.Add(new ColumnDefinition { Width = width });
        iconHost.Content = icon;
        if (icon is null) iconHost.Margin = new Thickness(0);
        head.Children.Add(iconHost);
        titleHost.Content = title;
        descriptionHost.Content = description;
        texts.Children.Add(titleHost);
        texts.Children.Add(descriptionHost);
        Grid.SetColumn(texts, 1);
        head.Children.Add(texts);
        actionHost.Content = action;
        if (action is null) actionHost.Margin = new Thickness(0);
        // Keep a useful label column when the window is narrow, including
        // ComboBoxes whose preferred width follows a very long device name.
        head.SizeChanged += (_, _) =>
        {
            if (action is FrameworkElement control)
            {
                var available = head.ActualWidth - head.Padding.Left - head.Padding.Right
                    - (icon is null ? 0 : 36) - (expandable ? ChevronSpace : 0) - 12 - 120;
                control.MaxWidth = Math.Max(0, available);
            }
        };
        Grid.SetColumn(actionHost, 2);
        head.Children.Add(actionHost);

        if (expandable)
        {
            var glyph = ui.LucideIcon("chevron-down", 12);
            glyph.RenderTransformOrigin = new Windows.Foundation.Point(0.5, 0.5); glyph.RenderTransform = chevronTurn;
            chevron = new Button { Content = glyph, Width = 32, Height = 32, Padding = new Thickness(0), CornerRadius = new CornerRadius(16), BorderThickness = new Thickness(0), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), Margin = new Thickness(8, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(chevron, ui.V("expand"));
            chevron.ClickMode = ClickMode.Press;
            chevron.Click += (_, _) => Toggle();
            Grid.SetColumn(chevron, 3);
            head.Children.Add(chevron);
            // Переключение по нажатию кнопки мыши (не по отпусканию): без задержки, каждое нажатие - новое переключение.
            head.PointerPressed += (_, e) => { if (e.GetCurrentPoint(head).Properties.IsLeftButtonPressed && !FromControl(e.OriginalSource)) Toggle(); };
        }
        // Подсветка при наведении - у всех карточек (у раскрытой - только заголовок).
        head.CornerRadius = new CornerRadius(19);
        // Как у SettingsCard из WinUI: при наведении светлее фон и «приподнятая» обводка (низ темнее - эффект тени),
        // при нажатии фон на тон глуше.
        head.PointerEntered += (_, _) => SetState(hover: true, pressed: false);
        head.PointerExited += (_, _) => SetState(hover: false, pressed: false);
        head.PointerCanceled += (_, _) => SetState(hover: false, pressed: false);
        head.PointerCaptureLost += (_, _) => SetState(hover: false, pressed: false);
        head.PointerPressed += (_, _) => SetState(hover: true, pressed: true);
        head.PointerReleased += (_, _) => SetState(hover: true, pressed: false);

        divider.BorderThickness = new Thickness(0, 1, 0, 0);
        divider.BorderBrush = ui.LineBrush;
        divider.Child = body;
        bodyCanvas.Children.Add(divider);
        bodyHost.Children.Add(bodyCanvas);
        bodyHost.Clip = clip;
        SizeChanged += (_, e) => divider.Width = Math.Max(0, e.NewSize.Width - 2);
        divider.SizeChanged += (_, _) => { if (expanded && tick is null) SetBodyHeight(FullHeight); };
        var stack = new StackPanel();
        stack.Children.Add(head);
        stack.Children.Add(bodyHost);
        frame.Child = stack;
        spot.GradientStops.Add(new GradientStop { Color = ui.Dark ? Windows.UI.Color.FromArgb(0x4B, 255, 255, 255) : Windows.UI.Color.FromArgb(0x30, 0, 0, 0), Offset = 0 });
        spot.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(0, ui.Dark ? (byte)255 : (byte)0, ui.Dark ? (byte)255 : (byte)0, ui.Dark ? (byte)255 : (byte)0), Offset = 1 });
        glow.BorderBrush = spot;
        var layers = new Grid();
        layers.Children.Add(frame);
        layers.Children.Add(glow);
        Content = layers;
        Unloaded += (_, _) => StopAnimation();
    }

    private void SetState(bool hover, bool pressed)
    {
        head.Background = pressed ? ui.CardPressedBrush : hover ? ui.CardHoverBrush : new SolidColorBrush(Microsoft.UI.Colors.Transparent);
        frame.BorderBrush = hover ? ui.ElevationBorderBrush() : ui.LineBrush;
    }

    // Позиция курсора относительно карточки; null - курсор ушёл из окна.
    public void SetSpotlight(Windows.Foundation.Point? at)
    {
        var p = at ?? new Windows.Foundation.Point(-1000, -1000);
        spot.Center = p;
        spot.GradientOrigin = p;
    }

    // Строка под заголовком на всю ширину текста и действия (например, полоса занятого места до правого края «N ГБ из 40»).
    public void SetFooter(FrameworkElement footer)
    {
        if (head.RowDefinitions.Count == 0) { head.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); head.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); }
        Grid.SetRowSpan(iconHost, 2);
        if (chevron is not null) Grid.SetRowSpan(chevron, 2);
        footer.Margin = new Thickness(0, 6, 0, 0);
        Grid.SetRow(footer, 1); Grid.SetColumn(footer, 1); Grid.SetColumnSpan(footer, 2);
        head.Children.Add(footer);
    }

    public void SetTitle(UIElement title) => titleHost.Content = title;
    public void SetIcon(UIElement? icon)
    {
        iconHost.Content = icon;
        iconHost.Margin = icon is null ? new Thickness(0) : new Thickness(0, 0, 16, 0);
    }
    public void SetDescription(UIElement? description) => descriptionHost.Content = description;
    public void AddBody(UIElement element) => body.Children.Add(element);

    public bool IsExpanded
    {
        get => expanded;
        set { StopAnimation(); expanded = value; SetBodyHeight(value ? FullHeight : 0); bodyHost.IsHitTestVisible = value; chevronTurn.Angle = value ? 180 : 0; head.CornerRadius = value ? new CornerRadius(19, 19, 0, 0) : new CornerRadius(19); UpdateChevronName(); }
    }

    private double FullHeight => divider.ActualHeight > 0 ? divider.ActualHeight : divider.DesiredSize.Height;

    private void SetBodyHeight(double h)
    {
        bodyHost.Height = h;
        clip.Rect = new Windows.Foundation.Rect(0, 0, 100000, h);
    }

    private bool FromControl(object source)
    {
        for (var node = source as DependencyObject; node is not null && node != head; node = VisualTreeHelper.GetParent(node))
            if (node == actionHost || node == chevron) return true;
        return false;
    }


    private void UpdateChevronName()
    {
        if (chevron is not null) Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(chevron, ui.V("expand"));
    }

    private void Toggle()
    {
        var opening = !expanded;
        expanded = opening;
        UpdateChevronName();
        var from = bodyHost.Height;
        var to = opening ? FullHeight : 0;
        var angleFrom = chevronTurn.Angle;
        var angleTo = opening ? 180.0 : 0.0;
        if (opening) head.CornerRadius = new CornerRadius(19, 19, 0, 0);
        bodyHost.IsHitTestVisible = opening;
        StopAnimation();
        if (!ui.AnimationsEnabled) { IsExpanded = opening; return; }
        // Прогресс копится по кадрам, шаг не больше 1/30 с: если кадр задержался (сборка мусора, загрузка),
        // анимация на миг замедляется, а не перескакивает в конец.
        var clock = System.Diagnostics.Stopwatch.StartNew();
        double last = 0, progress = 0;
        tick = (_, _) =>
        {
            var now = clock.Elapsed.TotalMilliseconds;
            progress += Math.Min(now - last, 1000.0 / 30);
            last = now;
            var t = Math.Min(1, progress / Duration);
            var eased = 1 - Math.Pow(1 - t, 3);
            SetBodyHeight(from + (to - from) * eased);
            chevronTurn.Angle = angleFrom + (angleTo - angleFrom) * eased;
            if (t >= 1) { StopAnimation(); IsExpanded = opening; }
        };
        CompositionTarget.Rendering += tick;
    }

    private void StopAnimation()
    {
        if (tick is null) return;
        CompositionTarget.Rendering -= tick;
        tick = null;
    }
}
