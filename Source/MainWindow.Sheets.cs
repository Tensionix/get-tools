using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using AudionGetTools.Core;

namespace AudionGetTools;

/// <summary>
/// SETTINGS and ABOUT, the way Audion Disk Tools has them: two buttons at the right of the header,
/// each opens its sheet in place of the sections and their pages; the same button, the cross or
/// Esc brings the sections back. A sheet is cards with a name in two columns. SETTINGS holds what
/// a person sets once and forgets - where the downloads go when the window opens, the colours, the
/// fonts and their sizes - and nothing an operation asks for: that stands on its page.
/// </summary>
public partial class MainWindow
{
    string _sheet = "";

    void ToggleSheet(string key)
    {
        _sheet = _sheet == key ? "" : key;
        SheetSettings.IsChecked = _sheet == "settings";
        SheetAbout.IsChecked = _sheet == "about";
        bool open = _sheet.Length > 0;
        SectionRow.IsVisible = Zones.IsVisible = !open;
        SheetHost.IsVisible = open;
        FillSheet();
        if (!open) Shell.Focus();
    }

    /// <summary>The sheet in sight is written again: a setting changed, or the language did.</summary>
    void FillSheet()
    {
        SheetBody.Children.Clear();
        _into = null;
        if (_sheet == "settings") FillSettings();
        else if (_sheet == "about") FillAbout();
        else return;
        if (Lang.Code != "ru") Lang.Walk(SheetBody);
        CentreWords();
    }

    // ------------------------------------------------------------ what a sheet is put together of

    /// <summary>Open starts a card with a name in a column; Add, Note and Pair go into it until the next Open.</summary>
    Panel? _into;

    void Add(Control control) => (_into ?? SheetBody).Children.Add(control);

    void Head(string text) => SheetBody.Children.Add(new TextBlock
    {
        Text = text, FontWeight = FontWeight.SemiBold, FontSize = 16, Margin = new Thickness(0, 0, 0, 8),
    });

    (StackPanel Left, StackPanel Right) Columns()
    {
        var columns = new Grid { ColumnDefinitions = new ColumnDefinitions("*,16,*") };
        var left = new StackPanel { Spacing = 10 };
        var right = new StackPanel { Spacing = 10 };
        Grid.SetColumn(right, 2);
        columns.Children.Add(left);
        columns.Children.Add(right);
        _into = null;
        SheetBody.Children.Add(columns);
        return (left, right);
    }

    StackPanel Below()
    {
        var below = new StackPanel { Margin = new Thickness(0, 10, 0, 0) };
        _into = null;
        SheetBody.Children.Add(below);
        return below;
    }

    void Open(Panel column, string title)
    {
        var body = new StackPanel { Spacing = 2 };
        body.Children.Add(new TextBlock { Text = title, Classes = { "caption" }, Margin = new Thickness(0, 0, 0, 4) });
        column.Children.Add(new Border { Classes = { "subcard" }, Child = body });
        _into = body;
    }

    void Note(string text) => Add(new TextBlock { Text = text, Classes = { "note" } });

    void Pair(string key, string value, int keyWidth = 130)
    {
        var row = new Grid { ColumnDefinitions = new ColumnDefinitions($"{keyWidth},*") };
        row.Children.Add(new TextBlock { Text = key, Classes = { "caption" }, VerticalAlignment = VerticalAlignment.Top, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 10, 0) });
        var shown = new SelectableTextBlock { Text = value, TextWrapping = TextWrapping.Wrap };
        Grid.SetColumn(shown, 1);
        row.Children.Add(shown);
        Add(row);
    }

    static WrapPanel Chips() => new() { Orientation = Orientation.Horizontal, ItemSpacing = 6, LineSpacing = 6, Margin = new Thickness(0, 2, 0, 4) };

    ToggleButton Chip(string label, bool chosen, string tip, Action press)
    {
        var chip = new ToggleButton { Content = label, Classes = { "switch" }, IsChecked = chosen };
        if (tip.Length > 0) ToolTip.SetTip(chip, Lang.T(tip));
        chip.Click += (_, _) => press();
        return chip;
    }

    // ------------------------------------------------------------ SETTINGS

    // Inter and JetBrains Mono ship inside the program; the rest are offered when Windows has them.
    // Only families a list names: a face the program cannot read brought a window down once.
    static readonly string[] WindowFonts = { "JetBrains Mono", "Inter", "Segoe UI", "Segoe UI Variable Text", "Arial", "Tahoma", "Verdana", "Calibri", "Cascadia Mono", "Consolas" };
    static readonly string[] TerminalFonts = { "JetBrains Mono", "Cascadia Mono", "Cascadia Code", "Consolas", "Lucida Console", "Courier New" };

    void FillSettings()
    {
        Head("SETTINGS");
        var (left, right) = Columns();

        Open(left, "ПАПКА ЗАГРУЗОК");
        var places = Chips();
        places.Children.Add(Chip("DOWNLOADS WINDOWS", _settings.Target == "downloads",
            "Папка «Загрузки» этого пользователя Windows.", () => SetDefaultTarget("downloads", "")));
        places.Children.Add(Chip("OUTPUT ПРОГРАММЫ", _settings.Target == "output",
            "Папка output рядом с программой: всё скачанное едет вместе с ней.", () => SetDefaultTarget("output", "")));
        places.Children.Add(Chip("СВОЯ…", _settings.Target == "own",
            "Выбрать любую папку.", async () => await PickDefaultTarget()));
        Add(places);
        Pair("Сейчас", DefaultTarget());
        Note("Сюда кладутся скачанные файлы, образы и распакованные сборки, когда окно открывается. На время работы папку меняет поле ЗАГРУЗКИ над терминалом.");

        Open(left, "ЦВЕТА");
        var themes = Chips();
        foreach (var (look, name) in new[] { ("codedark", "AUDION CODE DARK"), ("vault", "AUDION VAULT DARK"), ("dark", "AUDION DARK"), ("light", "AUDION LIGHT") })
            themes.Children.Add(Chip(name, _settings.Look == look, "", () => SetTheme(look)));
        Add(themes);

        var installed = new HashSet<string>(FontManager.Current.SystemFonts.Select(font => font.Name), StringComparer.OrdinalIgnoreCase) { "Inter", "JetBrains Mono" };
        Open(right, "ШРИФТ ОКНА");
        FontRow(WindowFonts.Where(installed.Contains), _settings.UiFont, family => _settings.UiFont = family,
            13 + _settings.UiShift, 12, 15, size => _settings.UiShift = size - 13);
        Note("Размер — основного текста; названия, подписи и бейджи идут за ним.");

        Open(right, "ШРИФТ ТЕРМИНАЛА");
        FontRow(TerminalFonts.Where(installed.Contains), _settings.LogFont, family => _settings.LogFont = family,
            _settings.LogSize, 9, 14, size => _settings.LogSize = size);

        var reset = new Button { Content = Lang.T("К УМОЛЧАНИЯМ"), Classes = { "small" }, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 10, 0, 0) };
        ToolTip.SetTip(reset, Lang.T("Вернуть папку загрузок, тему, шрифты и размеры к значениям программы."));
        reset.Click += (_, _) =>
        {
            _settings.UiFont = _settings.LogFont = _settings.TargetFolder = "";
            _settings.UiShift = 0;
            _settings.LogSize = Settings.UsualLogSize;
            _settings.Target = "downloads";
            ApplyFonts();
            ApplyDefaultTarget();
            // Saves the settings and writes the sheet again.
            SetTheme("codedark");
        };
        _into = null;
        SheetBody.Children.Add(reset);
    }

    /// <summary>The families as chips, each written in itself, and "− 13 +": half a point a step.</summary>
    void FontRow(IEnumerable<string> families, string chosen, Action<string> setFamily, double size, double min, double max, Action<double> setSize)
    {
        if (chosen.Length == 0) chosen = "JetBrains Mono";
        var row = Chips();
        foreach (var family in families)
        {
            var chip = new ToggleButton
            {
                Content = family, Classes = { "switch" }, FontFamily = App.Family(family),
                IsChecked = string.Equals(family, chosen, StringComparison.OrdinalIgnoreCase),
            };
            chip.Click += (_, _) => { setFamily(family); FontsChanged(); };
            row.Children.Add(chip);
        }
        var minus = new Button { Content = "−", Classes = { "small" }, Padding = new Thickness(10, 0) };
        var value = new TextBlock { Text = size.ToString("0.#", CultureInfo.InvariantCulture), VerticalAlignment = VerticalAlignment.Center, MinWidth = 34, TextAlignment = TextAlignment.Center };
        var plus = new Button { Content = "+", Classes = { "small" }, Padding = new Thickness(10, 0) };
        void Step(double delta)
        {
            var next = Math.Clamp(Math.Round((size + delta) * 2) / 2, min, max);
            if (next == size) return;
            setSize(next);
            FontsChanged();
        }
        minus.Click += (_, _) => Step(-0.5);
        plus.Click += (_, _) => Step(0.5);
        // One piece of the row: a line that wraps takes the three of them along, never the plus alone.
        row.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(12, 0, 0, 0), Children = { minus, value, plus } });
        Add(row);
    }

    void FontsChanged()
    {
        _settings.Save();
        ApplyFonts();
        FillSheet();
    }

    /// <summary>The fonts and the sizes of the settings into the window: at its start and at every change.</summary>
    void ApplyFonts()
    {
        App.SetFonts(_settings.UiFont, _settings.LogFont);
        App.SetSizes(_settings.UiShift, _settings.LogSize);
        // Every page was laid out for the font before: the widths of its columns are counted by its words.
        OutdateAll(lists: false);
        CentreWords();
    }

    // ------------------------------------------------------------ where the downloads go by default

    /// <summary>The folder the downloads go to when the window opens: the Downloads of Windows unless SETTINGS say otherwise.</summary>
    string DefaultTarget() => _settings.Target switch
    {
        "output" when _told is not null => _told.Paths.Output,
        "own" when _settings.TargetFolder.Length > 0 => _settings.TargetFolder,
        _ => Paths.SystemDownloads,
    };

    void ApplyDefaultTarget()
    {
        _target = DefaultTarget();
        TargetBox.Text = Paths.Short(_target);
        ShowDrives();
    }

    void SetDefaultTarget(string kind, string folder)
    {
        _settings.Target = kind;
        _settings.TargetFolder = folder;
        _settings.Save();
        // The choice is in force at once: a default set for the next start alone would look like a button that did nothing.
        ApplyDefaultTarget();
        FillSheet();
    }

    async System.Threading.Tasks.Task PickDefaultTarget()
    {
        var picked = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions { Title = Lang.T("Папка для загрузок"), AllowMultiple = false });
        if (picked.Count > 0 && picked[0].TryGetLocalPath() is { Length: > 0 } folder) SetDefaultTarget("own", folder);
        else FillSheet();
    }

    // ------------------------------------------------------------ ABOUT

    static Avalonia.Media.Imaging.Bitmap Brand(string file) =>
        new(Avalonia.Platform.AssetLoader.Open(new Uri($"avares://AudionGetTools/Assets/brand/{file}")));

    /// <summary>Brand pictures are large and shown small: the default scaler turns them soft, the high-quality one keeps the lines clean.</summary>
    static Image Sharp(Image image)
    {
        RenderOptions.SetBitmapInterpolationMode(image, Avalonia.Media.Imaging.BitmapInterpolationMode.HighQuality);
        return image;
    }

    /// <summary>A domain as a link: opens in the default browser.</summary>
    static Button Link(string domain)
    {
        var link = new Button { Content = domain, Classes = { "crumb", "link" }, Padding = new Thickness(0, 1), Margin = new Thickness(0, 0, 16, 0) };
        link.Click += (_, _) =>
        {
            try { Process.Start(new ProcessStartInfo("https://" + domain) { UseShellExecute = true }); }
            catch { }
        };
        return link;
    }

    void FillAbout()
    {
        // Audion is the family of the programs, Tensionix their author. Both logos come from Audion Design: light
        // letters on the dark theme, dark ones on the light theme.
        SheetBody.Children.Add(Sharp(new Image
        {
            Source = Brand(_settings.Look == "light" ? "Audion_Logo_Light_1600.png" : "Audion_Logo_Dark_1600.png"),
            Height = 56, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 10),
        }));
        Head("Get Tools");
        Note("Всё, что ставится и скачивается на машину с Windows, из одного окна: пакеты WinGet, образ Windows, системные утилиты, утилиты для железа, драйверы, программы для монтажа и графики. Портативная: всё своё хранит рядом с собой.");
        _into = null;

        var (left, right) = Columns();
        Open(left, "ВЕРСИЯ И ПЛАТФОРМА");
        Pair("Версия", Paths.Version ?? "—");
        Pair("Система", RuntimeInformation.OSDescription);
        Pair(".NET", Environment.Version.ToString());
        Pair("Avalonia", typeof(AvaloniaObject).Assembly.GetName().Version?.ToString() ?? "");

        Open(right, "ПАПКИ");
        Pair("Программа", Paths.Root);
        Pair("Настройки окна", Settings.FilePath);
        Pair("Журналы", Paths.ProgramLogs);
        Pair("Загрузки", DefaultTarget());

        Open(Below(), "АВТОР");
        // The logo of Tensionix: its mark and its name in curves, not a typeface standing in for the name.
        Add(Sharp(new Image
        {
            Source = Brand(_settings.Look == "light" ? "Tensionix_Logo_Light_1600.png" : "Tensionix_Logo_Dark_1600.png"),
            Height = 40, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 2, 0, 4),
        }));
        var links = new StackPanel { Orientation = Orientation.Horizontal };
        links.Children.Add(Link("audion.dev"));
        links.Children.Add(Link("tensionix.com"));
        Add(links);
        _into = null;
    }
}
