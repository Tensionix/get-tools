using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Documents;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using AudionGetTools.Core;
using AudionGetTools.Views;
using Page = AudionGetTools.Core.Page;

namespace AudionGetTools;

/// <summary>
/// The window of Audion Get Tools. It installs and downloads nothing itself: every button runs an
/// operation of the manifest through the console door (get_cli.cmd) and shows what the operation
/// says. Which sections there are, what stands on their pages and which fields an operation has,
/// the door tells - the window knows none of them by name.
/// </summary>
public partial class MainWindow : Window
{
    readonly Settings _settings;
    readonly Runner _runner = new();
    /// <summary>The values of the fields: a store for every page, one of the window for the fields the layout names shared.</summary>
    ValueStore _store = new();
    Described? _told;
    Section? _section;
    Page? _page;
    /// <summary>The page each section was left at.</summary>
    readonly Dictionary<string, string> _lastPage = new();
    string _target = "";
    /// <summary>Where the pages that read a folder read from (`source` of the layout).</summary>
    string _source = "";

    public MainWindow()
    {
        // The theme is the application's: set before the markup is read, so the window comes up in it.
        _settings = Settings.Load();
        Lang.Set(_settings.LanguageInUse);
        if (Application.Current is { } app) app.RequestedThemeVariant = App.Variant(_settings.Look);
        InitializeComponent();
        if (_settings.Width >= MinWidth && _settings.Height >= MinHeight)
        {
            Width = _settings.Width;
            Height = _settings.Height;
        }
        Classes.Set("vault", _settings.Look == "vault");
        App.SetFonts(_settings.UiFont, _settings.LogFont);
        App.SetSizes(_settings.UiShift, _settings.LogSize);
        // Words and signs on whole pixels of this screen, whatever its scale.
        PixelGrid.Apply(RenderScaling);
        ScalingChanged += (_, _) => PixelGrid.Apply(RenderScaling);
        PixelGrid.Field(TargetBox);
        PixelGrid.Field(SourceBox);
        PixelGrid.Field(PageSearchBox);

        var version = Paths.Version;
        Title = version is null ? "Audion Get Tools" : "Audion Get Tools v" + version;
        VersionText.Text = (version is null ? "version unknown" : "v" + version) + " • " + DateTime.Now.Year;

        _runner.Output += OnOutput;
        _runner.Finished += OnFinished;
        Guard.Told = message =>
        {
            AppendLines(new[] { "[GUI ERROR] " + message });
            SetStatus("Ошибка окна записана в logs.");
        };

        Wire();
        WireTerminal();
#if DEBUG
        // A pass of a check (-Shot) is not for a person: the window stands off every screen, takes no
        // focus and has no button on the taskbar (the owner: "I cannot make out by your millisecond
        // switches what belongs to what"). Its pictures are drawn from the window itself, not from a
        // screen. AUDION_SHOT_ON_SCREEN=1 puts it in sight.
        if (Program.ShotFolder.Length > 0 && Environment.GetEnvironmentVariable("AUDION_SHOT_ON_SCREEN") != "1")
        {
            ShowInTaskbar = false;
            ShowActivated = false;
            WindowStartupLocation = WindowStartupLocation.Manual;
            Position = new PixelPoint(-32000, -32000);
        }
#endif
        MarkTheme();
        MarkLanguage();
        ShowMachine();
        // What the markup holds is written in Russian: walked once, into the language in use.
        Lang.Walk(this);
        Closing += OnClosing;
        Opened += async (_, _) =>
        {
            await StartUp();
#if DEBUG
            // AUDION_SHOT_HEIGHT: a pass may ask for a taller window - a long page in one picture. Off the screen it may be any height.
            if (Program.ShotFolder.Length > 0 && double.TryParse(Environment.GetEnvironmentVariable("AUDION_SHOT_HEIGHT"), out var tall) && tall > 400) Height = tall;
            if (Program.ShotFolder.Length > 0) await TakeShots(Program.ShotFolder);
#endif
        };
    }

    // ------------------------------------------------------------ the look

    void Wire()
    {
        LangRu.Click += (_, _) => SetLanguage("ru");
        LangEn.Click += (_, _) => SetLanguage("en");
        ThemeVault.Click += (_, _) => SetTheme("vault");
        ThemeDark.Click += (_, _) => SetTheme("dark");
        ThemeCode.Click += (_, _) => SetTheme("codedark");
        ThemeLight.Click += (_, _) => SetTheme("light");
        StopButton.Click += (_, _) => StopRun();
        CopyLogButton.Click += (_, _) => CopyLog();
        ClearLogButton.Click += (_, _) => ClearLog();
        WideLogButton.Click += (_, _) => ToggleWide();
        ActionReset.Click += (_, _) => { if (_view is { } view) ResetFields(view, view.ResetFields); };
        LogsButton.Click += (_, _) => OpenFolder(Paths.ProgramLogs);
        OpenTargetButton.Click += (_, _) => OpenFolder(_target);
        PickTargetButton.Click += async (_, _) => await PickTarget();
        TargetBox.LostFocus += (_, _) => TakeTarget();
        TargetBox.KeyDown += (_, e) => { if (e.Key == Avalonia.Input.Key.Enter) { TakeTarget(); Shell.Focus(); } };
        // The search of a page hides cards of the page in sight; each page keeps its own.
        PageSearchBox.TextChanged += (_, _) => { if (_view is { Page.Filter: true } view) view.Form.PageQuery = PageSearchBox.Text ?? ""; };
        OpenSourceButton.Click += (_, _) => OpenSource();
        PickSourceButton.Click += async (_, _) => await PickSource();
        SourceBox.LostFocus += (_, _) => TakeSource();
        SourceBox.KeyDown += (_, e) => { if (e.Key == Avalonia.Input.Key.Enter) { TakeSource(); Shell.Focus(); } };
        _clock.Tick += (_, _) => ShowClock();
        SheetSettings.Click += (_, _) => ToggleSheet("settings");
        SheetAbout.Click += (_, _) => ToggleSheet("about");
        SheetClose.Click += (_, _) => ToggleSheet(_sheet);
        KeyDown += (_, e) =>
        {
            if (e.Key != Avalonia.Input.Key.Escape || _sheet.Length == 0) return;
            ToggleSheet(_sheet);
            e.Handled = true;
        };
    }

    void MarkLanguage()
    {
        LangRu.IsChecked = Lang.Code == "ru";
        LangEn.IsChecked = Lang.Code != "ru";
    }

    void SetLanguage(string code)
    {
        if (Lang.Code != code)
        {
            Lang.Set(code);
            _settings.Lang = code;
            _settings.Save();
            // What code wrote is written again in the new language; what the markup holds is walked.
            ShowMachine();
            ForgetPages();
            BuildService();
            BuildSections();
            Lang.Walk(this);
            FillSheet();
        }
        MarkLanguage();
    }

    void SetTheme(string look)
    {
        _settings.Look = look;
        _settings.Save();
        if (Application.Current is { } app) app.RequestedThemeVariant = App.Variant(look);
        Classes.Set("vault", look == "vault");
        MarkTheme();
        // The lines of the terminal took their brushes from the old theme; so did the cards of ticks.
        Dispatcher.UIThread.Post(Recolour, DispatcherPriority.Background);
        OutdateAll(lists: false);
        // SETTINGS marks the theme chosen; ABOUT shows the mark made for it.
        FillSheet();
    }

    void MarkTheme()
    {
        ThemeVault.IsChecked = _settings.Look == "vault";
        ThemeDark.IsChecked = _settings.Look == "dark";
        ThemeCode.IsChecked = _settings.Look == "codedark";
        ThemeLight.IsChecked = _settings.Look == "light";
        ThemeButton.IsChecked = false;
    }

    /// <summary>
    /// The machine in the header, badge by badge: Windows, the computer, the processor, the video
    /// cards with their drivers, the memory, the drives, the rights of the window. A badge holds a
    /// few words; the whole of it is in its tooltip.
    /// </summary>
    void ShowMachine()
    {
        MachineRow.Children.Clear();
        bool ru = Lang.Code == "ru";
        void Add(Badge? badge)
        {
            if (badge is not null) MachineRow.Children.Add(Spec(badge));
        }
        try
        {
            Add(SystemInfo.Windows(ru));
            Add(SystemInfo.Machine(ru));
            Add(SystemInfo.Processor(ru));
            foreach (var card in SystemInfo.VideoCards(ru)) Add(card);
            Add(SystemInfo.Memory(ru));
        }
        catch { }
        MachineRow.Children.Add(_systemDrive = Spec(null));
        MachineRow.Children.Add(_targetDrive = Spec(null));
        try
        {
            if (SystemInfo.Rights(ru) is { } rights)
            {
                var badge = Spec(rights);
                if (!SystemInfo.Elevated)
                {
                    // The one badge that does something: a window without the rights starts itself again with them.
                    badge.Cursor = new Avalonia.Input.Cursor(Avalonia.Input.StandardCursorType.Hand);
                    badge.PointerPressed += (_, _) => RestartElevated();
                }
                MachineRow.Children.Add(badge);
            }
        }
        catch { }
        ShowDrives();
    }

    Border _systemDrive = new(), _targetDrive = new();

    /// <summary>A badge of the header; an empty one waits, out of sight, for what it will say.</summary>
    static Border Spec(Badge? badge)
    {
        var spec = new Border { Classes = { "spec" }, Child = new TextBlock(), IsVisible = false };
        Tell(spec, badge);
        return spec;
    }

    static void Tell(Border spec, Badge? badge)
    {
        spec.IsVisible = badge is not null;
        if (badge is null) return;
        ((TextBlock)spec.Child!).Text = badge.Text;
        spec.Classes.Set("warn", badge.Warn);
        ToolTip.SetTip(spec, badge.Tip);
    }

    /// <summary>
    /// The drives by how much of them is free: the one of Windows and, when it is another, the one
    /// the downloads go to. Asked again after every run and every change of the folder.
    /// </summary>
    void ShowDrives()
    {
        bool ru = Lang.Code == "ru";
        var system = SystemInfo.SystemDrive;
        Tell(_systemDrive, SystemInfo.Drive(system, ru, downloads: false));
        string target = "";
        try { target = _target.Length > 0 ? Path.GetPathRoot(_target) ?? "" : ""; } catch { }
        // A network folder (\\server\share) is no drive to measure.
        bool other = target.Length > 0 && !target.StartsWith(@"\\") && !string.Equals(target, system, StringComparison.OrdinalIgnoreCase);
        Tell(_targetDrive, other ? SystemInfo.Drive(target, ru, downloads: true) : null);
        CentreWords();
    }

    static string Upper(string text) => text.ToUpper(CultureInfo.CurrentCulture);

    /// <summary>The status line: written in Russian by whoever speaks, shown in the language of the window.</summary>
    void SetStatus(string text) => StatusText.Text = Lang.T(text);

    // ------------------------------------------------------------ the start

    async Task StartUp()
    {
        if (!Door.Ready)
        {
            ShowNote(Door.Trouble);
            SetStatus("Движок программы недоступен.");
            return;
        }
        SetStatus("Читаю состав программы…");
        var (told, trouble) = await Door.Describe();
        if (told is null)
        {
            ShowNote(Lang.F("Дверь программы не ответила: {0}", trouble));
            SetStatus("Состав программы не прочитан.");
            return;
        }
        _told = told;
        _store = new ValueStore(told.Layout.Shared);
        ApplyDefaultTarget();
        _source = _settings.SourceFolder.Length > 0 ? _settings.SourceFolder : told.Paths.Input;
        SourceBox.Text = Paths.Short(_source);
        BuildService();
        BuildSections();
        SetStatus("Готово.");
    }

    void ShowNote(string text)
    {
        CardsNote.Text = text;
        CardsNote.IsVisible = true;
    }

    // ------------------------------------------------------------ the header: the service of the program

    void BuildService()
    {
        ServiceRow.Children.Clear();
        if (_told is null) return;
        foreach (var node in _told.Layout.Service)
        {
            var face = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            if (Icons.Glyph(node.Icon) is not null) face.Children.Add(Icons.Sign(node.Icon));
            face.Children.Add(new TextBlock { Text = Upper(node.Name) });
            var button = new Button { Classes = { "small" }, Content = face, Margin = new Thickness(0, 0, 4, 0) };
            button.Classes.Set("burn", node.Tone == "burn");
            Avalonia.Automation.AutomationProperties.SetAutomationId(button, "run_" + node.Id);
            ToolTip.SetTip(button, TipOf(node));
            button.Click += (_, _) => Press(node);
            ServiceRow.Children.Add(button);
        }
        // The operations of the working folders of the program: signs beside the row of the downloads.
        FolderTools.Children.Clear();
        foreach (var node in _told.Layout.Folders)
        {
            var button = new Button { Classes = { "tool" }, Content = Icons.Sign(Icons.Glyph(node.Icon) is not null ? node.Icon : "trash-2"), Margin = new Thickness(4, 0, 0, 0) };
            button.Classes.Set("burn", node.Tone == "burn");
            Avalonia.Automation.AutomationProperties.SetAutomationId(button, "run_" + node.Id);
            ToolTip.SetTip(button, TipOf(node) + "\n" + FolderLines());
            button.Click += (_, _) => Press(node);
            FolderTools.Children.Add(button);
        }
        ApplyBusy();
    }

    /// <summary>The two working folders of the program by their full paths: what an operation of the folders acts on.</summary>
    string FolderLines() => _told is null ? "" : "input: " + _told.Paths.Input + "\noutput: " + _told.Paths.Output;

    /// <summary>The name of an operation and what it does: the tooltip of its button.</summary>
    static string TipOf(Node node) =>
        node.Tip.Length > 0 ? node.Name + "\n" + node.Tip : node.About.Length > 0 ? node.Name + "\n" + node.About : node.Name;

    // ------------------------------------------------------------ the sections and their pages

    void BuildSections()
    {
        SectionRow.Children.Clear();
        SectionRow.ColumnDefinitions.Clear();
        if (_told is null) return;
        foreach (var section in _told.Layout.Sections)
        {
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, HorizontalAlignment = HorizontalAlignment.Center };
            if (Icons.Glyph(section.Icon) is not null) head.Children.Add(Icons.Sign(section.Icon));
            head.Children.Add(new TextBlock { Classes = { "sectionname" }, Text = Upper(section.Name) });
            var face = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Children = { head } };
            // A line that does not fit - a larger font of SETTINGS, a narrow window - ends in an ellipsis, not at the rim.
            if (section.Line.Length > 0) face.Children.Add(new TextBlock { Classes = { "hint" }, Text = section.Line, TextTrimming = TextTrimming.CharacterEllipsis });
            var tab = new ToggleButton { Classes = { "switch", "section" }, Tag = section.Id, Content = face, HorizontalAlignment = HorizontalAlignment.Stretch, MinHeight = 46 };
            Avalonia.Automation.AutomationProperties.SetAutomationId(tab, "section_" + section.Id);
            ToolTip.SetTip(tab, section.Name + "\n" + section.Line);
            tab.Click += (_, _) => SelectSection(section);
            // The sections share the width of the window equally, with a gap between them.
            if (SectionRow.ColumnDefinitions.Count > 0) SectionRow.ColumnDefinitions.Add(new ColumnDefinition(new GridLength(6)));
            SectionRow.ColumnDefinitions.Add(new ColumnDefinition(GridLength.Star));
            Grid.SetColumn(tab, SectionRow.ColumnDefinitions.Count - 1);
            SectionRow.Children.Add(tab);
        }
        var open = _told.Layout.Sections.FirstOrDefault(section => section.Id == _section?.Id) ?? _told.Layout.Sections.FirstOrDefault();
        if (open is not null) SelectSection(open);
    }

    void SelectSection(Section section)
    {
        _section = section;
        foreach (var child in SectionRow.Children)
            if (child is ToggleButton tab) tab.IsChecked = (string?)tab.Tag == section.Id;
        // The pages of the section: a band of switches as large as the sections over it, sharing
        // the width of the left side equally - a sign and a name, no line under them. A name that
        // does not fit an equal share takes its own width (Views\EvenRow.cs).
        PageRow.Children.Clear();
        foreach (var page in section.Pages)
        {
            var face = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, HorizontalAlignment = HorizontalAlignment.Center };
            if (Icons.Glyph(page.Icon) is not null) face.Children.Add(Icons.Sign(page.Icon));
            face.Children.Add(new TextBlock { Classes = { "sectionname" }, Text = Upper(page.Name), TextTrimming = TextTrimming.CharacterEllipsis });
            var tab = new ToggleButton { Classes = { "switch", "section" }, Tag = page.Id, Content = face, HorizontalAlignment = HorizontalAlignment.Stretch };
            Avalonia.Automation.AutomationProperties.SetAutomationId(tab, $"page_{section.Id}_{page.Id}");
            // A page that says what it is for says it here; the name alone would repeat the button.
            if (page.Line.Length > 0) ToolTip.SetTip(tab, page.Name + "\n" + page.Line);
            tab.Click += (_, _) => SelectPage(page);
            PageRow.Children.Add(tab);
        }
        // A section of one page has nothing to switch.
        PageRow.IsVisible = section.Pages.Count > 1;
        var open = section.Pages.FirstOrDefault(page => _lastPage.TryGetValue(section.Id, out var last) && last == page.Id) ?? section.Pages.FirstOrDefault();
        if (open is not null) SelectPage(open);
    }

    // ------------------------------------------------------------ where the downloads go

    void TakeTarget()
    {
        var typed = (TargetBox.Text ?? "").Trim().Trim('"');
        if (typed.Length == 0) typed = DefaultTarget();
        try { _target = Path.GetFullPath(Path.IsPathRooted(typed) ? typed : Path.Combine(Paths.Root, typed)); }
        catch { SetStatus("Путь не принят: в нём недопустимый знак."); }
        TargetBox.Text = Paths.Short(_target);
        ShowDrives();
    }

    async Task PickTarget()
    {
        var picked = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions { Title = Lang.T("Папка для загрузок"), AllowMultiple = false });
        if (picked.Count == 0 || picked[0].TryGetLocalPath() is not { Length: > 0 } folder) return;
        _target = folder;
        TargetBox.Text = Paths.Short(_target);
        ShowDrives();
    }

    // ------------------------------------------------------------ where the operations of a page read from

    /// <summary>The folder typed into the row SOURCE; an empty row is `input` of the program again.</summary>
    void TakeSource()
    {
        var typed = (SourceBox.Text ?? "").Trim().Trim('"');
        if (typed.Length == 0) typed = _told?.Paths.Input ?? "";
        try { SetSource(Path.GetFullPath(Path.IsPathRooted(typed) ? typed : Path.Combine(Paths.Root, typed))); }
        catch
        {
            SetStatus("Путь не принят: в нём недопустимый знак.");
            SourceBox.Text = Paths.Short(_source);
        }
    }

    async Task PickSource()
    {
        var picked = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions { Title = Lang.T("Папка-источник"), AllowMultiple = false });
        if (picked.Count > 0 && picked[0].TryGetLocalPath() is { Length: > 0 } folder) SetSource(folder);
    }

    /// <summary>
    /// Another source: kept for the next start, and every page that reads a folder is out of date -
    /// a list that shows what lies in the source is asked anew.
    /// </summary>
    void SetSource(string folder)
    {
        SourceBox.Text = Paths.Short(folder);
        if (string.Equals(folder, _source, StringComparison.OrdinalIgnoreCase)) return;
        _source = folder;
        _settings.SourceFolder = string.Equals(folder, _told?.Paths.Input ?? "", StringComparison.OrdinalIgnoreCase) ? "" : folder;
        _settings.Save();
        foreach (var view in _views.Values)
            if (view.Page.Source) view.Stale = true;
        if (_view is { Page.Source: true }) RebuildSoon();
        CheckSource();
    }

    /// <summary>
    /// Whether the source is there, asked off the window's thread: a folder on a drive that went
    /// to sleep, or on a share that is gone, answers in seconds, and the window must not wait.
    /// </summary>
    async void CheckSource()
    {
        var folder = _source;
        if (folder.Length == 0) return;
        bool there = await Task.Run(() => { try { return Directory.Exists(folder); } catch { return false; } });
        if (folder == _source && !there) SetStatus(Lang.F("Папки-источника нет: {0}", folder));
    }

    /// <summary>The source in Explorer. It is not made when it is not there: a source is what lies somewhere already.</summary>
    async void OpenSource()
    {
        var folder = _source;
        bool there = await Task.Run(() => { try { return Directory.Exists(folder); } catch { return false; } });
        if (!there) { SetStatus(Lang.F("Папки-источника нет: {0}", folder)); return; }
        try { Process.Start(new ProcessStartInfo("explorer.exe") { ArgumentList = { folder }, UseShellExecute = false }); }
        catch (Exception ex) { SetStatus(Lang.F("Папка не открылась: {0}", ex.Message)); }
    }

    void OpenFolder(string folder)
    {
        try
        {
            Directory.CreateDirectory(folder);
            Process.Start(new ProcessStartInfo("explorer.exe") { ArgumentList = { folder }, UseShellExecute = false });
        }
        catch (Exception ex) { SetStatus(Lang.F("Папка не открылась: {0}", ex.Message)); }
    }

    // ------------------------------------------------------------ the terminal

    /// <summary>The text of the terminal since it was last cleared: the copy button gives all of it, up to a limit.</summary>
    readonly StringBuilder _logAll = new();
    const int LogAllLimit = 16_000_000;

    /// <summary>
    /// The panel keeps the last lines of a run and not all of them. It is pages of text one under
    /// another: a text is laid out whole at every line added to it, so new lines go into the last
    /// page while it has room, a full page is never touched again, and the oldest pages leave whole.
    /// </summary>
    const int LogLimit = 5000;
    const int PageLines = 100;
    readonly List<int> _pageLines = new();
    readonly List<int> _pageRuns = new();
    readonly List<(Run Run, Hue Hue)> _logRuns = new();
    int _logShown;
    bool _scrollPosted;
    /// <summary>The colours of the operation that runs: what a line opened is in force on the next.</summary>
    readonly Ansi _ansi = new();

    /// <summary>
    /// Lines into the terminal. A line an operation told is written in the colours its writer gave
    /// it and in no other (Core\Ansi.cs); a line of the window itself - what was run, how it
    /// ended, what the window could not do - in the tone of what it says (Core\LogTones.cs).
    /// </summary>
    void AppendLines(IReadOnlyList<string> lines, bool told = false)
    {
        if (lines.Count == 0) return;
        // The panel follows the new lines while its reader is at the end, and stays where it is
        // when he has gone up to read: asked before the lines are added.
        bool follow = _scrollPosted || LogScroll.Offset.Y + LogScroll.Viewport.Height >= LogScroll.Extent.Height - 40;

        var read = new List<(string Text, Hue Hue)>[lines.Count];
        for (int i = 0; i < lines.Count; i++)
        {
            string plain = lines[i];
            if (told) read[i] = _ansi.Read(lines[i], out plain);
            else
            {
                read[i] = new List<(string, Hue)>();
                if (plain.Length > 0) read[i].Add((plain, new Hue(LogTones.Key(LogTones.Of(plain)), null, false, false)));
            }
            _logAll.Append(plain).Append("\r\n");
        }
        if (_logAll.Length > LogAllLimit)
        {
            int cut = LogAllLimit / 4;
            while (cut < _logAll.Length && _logAll[cut - 1] != '\n') cut++;
            _logAll.Remove(0, cut);
        }

        int at = Math.Max(0, lines.Count - LogLimit);
        _logShown += lines.Count - at;
        var pages = LogPages.Children;
        while (at < lines.Count)
        {
            if (_pageLines.Count == 0 || _pageLines[^1] >= PageLines)
            {
                pages.Add(new Controls.LogPage { TextWrapping = TextWrapping.Wrap });
                _pageLines.Add(0);
                _pageRuns.Add(0);
            }
            int held = _pageLines[^1], take = Math.Min(PageLines - held, lines.Count - at);
            // Pieces that stand together and are written the same way are one run.
            var added = new List<Inline>();
            var piece = new StringBuilder();
            var kind = new Hue("LogPlain", null, false, false);
            void Close()
            {
                if (piece.Length == 0) return;
                var run = new Run(piece.ToString()) { Foreground = Ansi.Brush(kind) };
                if (kind.Bold) run.FontWeight = FontWeight.Bold;
                if (kind.Underline) run.TextDecorations = TextDecorations.Underline;
                added.Add(run);
                _logRuns.Add((run, kind));
                piece.Clear();
            }
            for (int i = 0; i < take; i++)
            {
                if (held + i > 0) piece.Append('\n');
                foreach (var (text, hue) in read[at + i])
                {
                    if (hue != kind) Close();
                    kind = hue;
                    piece.Append(text);
                }
            }
            Close();
            var page = (Controls.LogPage)pages[^1];
            (page.Inlines ??= new InlineCollection()).AddRange(added);
            _pageLines[^1] = held + take;
            _pageRuns[^1] += added.Count;
            at += take;
        }

        int gone = 0, lost = 0;
        while (gone < _pageLines.Count - 1 && _logShown - _pageLines[gone] >= LogLimit)
        {
            _logShown -= _pageLines[gone];
            lost += _pageRuns[gone++];
        }
        if (gone > 0)
        {
            pages.RemoveRange(0, gone);
            _pageLines.RemoveRange(0, gone);
            _pageRuns.RemoveRange(0, gone);
            _logRuns.RemoveRange(0, lost);
        }

        if (!follow || _scrollPosted) return;
        _scrollPosted = true;
        Dispatcher.UIThread.Post(() =>
        {
            _scrollPosted = false;
            LogScroll.ScrollToEnd();
        }, DispatcherPriority.Background);
    }

    void ClearLog()
    {
        LogPages.Children.Clear();
        _pageLines.Clear();
        _pageRuns.Clear();
        _logRuns.Clear();
        _logAll.Clear();
        _logShown = 0;
        if (!_runner.Running) _ansi.Reset();
    }

    /// <summary>After a change of the theme the lines already written keep their meaning and take the new colours.</summary>
    void Recolour()
    {
        // A colour a line was given by its writer is the same in every theme.
        foreach (var (run, hue) in _logRuns)
            if (hue.Own is null) run.Foreground = App.Ink(hue.Key);
        if (ActivityRow.IsVisible) ShowActivity(_activity, true);
    }

    async void CopyLog()
    {
        if (_logAll.Length == 0 || Clipboard is not { } clipboard) return;
        try
        {
            await Avalonia.Input.Platform.ClipboardExtensions.SetTextAsync(clipboard, _logAll.ToString());
            Toast.Show(this, Lang.T("Терминал скопирован."));
        }
        catch (Exception ex) { SetStatus(Lang.F("Буфер обмена не принял текст: {0}", ex.Message)); }
    }

    bool _wide;

    /// <summary>The terminal over the whole window - the cards step aside - and back.</summary>
    void ToggleWide()
    {
        _wide = !_wide;
        if (LeftZone.Parent is Grid zones)
        {
            LeftZone.IsVisible = !_wide;
            zones.ColumnDefinitions[0].Width = _wide ? new GridLength(0) : GridLength.Star;
            zones.ColumnDefinitions[2].Width = _wide ? GridLength.Star : new GridLength(560);
        }
        if (WideLogButton.Content is Avalonia.Controls.Shapes.Path sign) sign.Data = Icons.Glyph(_wide ? "minimize-2" : "maximize-2");
    }

    // ------------------------------------------------------------ the end of the window

    void SaveWindow()
    {
        if (WindowState != WindowState.Normal) return;
        _settings.Width = Width;
        _settings.Height = Height;
        _settings.Save();
    }
}
