using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;

namespace HaloBattery.Settings;

public sealed partial class MainWindow
{
    // Native ComboBox/ComboBoxItem retain selection, keyboard and automation.
    // Explicit item colors avoid cached platform ThemeResource accent tints.
    internal void ConfigureComboSelection(ComboBox box)
    {
        var fill = dark ? "#303030" : "#ECECEC";
        var hover = dark ? "#3A3A3A" : "#E0E0E0";
        var text = dark ? "#FFFFFF" : "#1B1B1B";
        var selectedText = dark ? "#1B1B1B" : "#FFFFFF";
        var disabledText = dark ? "#5DFFFFFF" : "#5C000000";
        var template = (ControlTemplate)XamlReader.Load($$"""
            <ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="ComboBoxItem">
                <Grid x:Name="LayoutRoot" Background="{TemplateBinding Background}"
                      BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}"
                      CornerRadius="{TemplateBinding CornerRadius}" Control.IsTemplateFocusTarget="True">
                    <VisualStateManager.VisualStateGroups>
                        <VisualStateGroup x:Name="CommonStates">
                            <VisualState x:Name="Normal">
                                <Storyboard><PointerUpThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="PointerOver">
                                <VisualState.Setters><Setter Target="LayoutRoot.Background" Value="{{hover}}" /></VisualState.Setters>
                                <Storyboard><PointerUpThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="Pressed">
                                <VisualState.Setters><Setter Target="LayoutRoot.Background" Value="{{hover}}" /></VisualState.Setters>
                                <Storyboard><PointerDownThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="Disabled">
                                <VisualState.Setters><Setter Target="ContentPresenter.Foreground" Value="{{disabledText}}" /></VisualState.Setters>
                            </VisualState>
                            <VisualState x:Name="Selected">
                                <VisualState.Setters>
                                    <Setter Target="LayoutRoot.Background" Value="{{AccentHex}}" />
                                    <Setter Target="ContentPresenter.Foreground" Value="{{selectedText}}" />
                                </VisualState.Setters>
                                <Storyboard><PointerUpThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="SelectedUnfocused">
                                <VisualState.Setters>
                                    <Setter Target="LayoutRoot.Background" Value="{{AccentHex}}" />
                                    <Setter Target="ContentPresenter.Foreground" Value="{{selectedText}}" />
                                </VisualState.Setters>
                            </VisualState>
                            <VisualState x:Name="SelectedPointerOver">
                                <VisualState.Setters>
                                    <Setter Target="LayoutRoot.Background" Value="{{AccentHex}}" />
                                    <Setter Target="ContentPresenter.Foreground" Value="{{selectedText}}" />
                                </VisualState.Setters>
                                <Storyboard><PointerUpThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="SelectedPressed">
                                <VisualState.Setters>
                                    <Setter Target="LayoutRoot.Background" Value="{{AccentHex}}" />
                                    <Setter Target="ContentPresenter.Foreground" Value="{{selectedText}}" />
                                </VisualState.Setters>
                                <Storyboard><PointerDownThemeAnimation Storyboard.TargetName="LayoutRoot" /></Storyboard>
                            </VisualState>
                            <VisualState x:Name="SelectedDisabled">
                                <VisualState.Setters>
                                    <Setter Target="LayoutRoot.Background" Value="{{fill}}" />
                                    <Setter Target="ContentPresenter.Foreground" Value="{{disabledText}}" />
                                </VisualState.Setters>
                            </VisualState>
                        </VisualStateGroup>
                    </VisualStateManager.VisualStateGroups>
                    <ContentPresenter x:Name="ContentPresenter" Content="{TemplateBinding Content}"
                        ContentTemplate="{TemplateBinding ContentTemplate}" ContentTemplateSelector="{TemplateBinding ContentTemplateSelector}"
                        ContentTransitions="{TemplateBinding ContentTransitions}" Foreground="{TemplateBinding Foreground}"
                        FontFamily="{TemplateBinding FontFamily}" FontSize="{TemplateBinding FontSize}"
                        FontWeight="{TemplateBinding FontWeight}" FontStyle="{TemplateBinding FontStyle}"
                        HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}"
                        VerticalAlignment="{TemplateBinding VerticalContentAlignment}" Margin="{TemplateBinding Padding}" />
                </Grid>
            </ControlTemplate>
            """);
        var style = box.ItemContainerStyle ?? new Style(typeof(ComboBoxItem));
        style.Setters.Add(new Setter(ComboBoxItem.BackgroundProperty, Brush("#00000000")));
        style.Setters.Add(new Setter(ComboBoxItem.ForegroundProperty, Brush(text)));
        style.Setters.Add(new Setter(ComboBoxItem.VerticalContentAlignmentProperty, VerticalAlignment.Center));
        style.Setters.Add(new Setter(ComboBoxItem.UseSystemFocusVisualsProperty, true));
        style.Setters.Add(new Setter(ComboBoxItem.TemplateProperty, template));
        box.ItemContainerStyle = style;
    }
}
