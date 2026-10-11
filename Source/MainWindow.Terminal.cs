using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using AudionGetTools.Core;
using AudionGetTools.Views;

namespace AudionGetTools;

/// <summary>
/// The command line of the terminal. A command typed under the terminal runs in the shell chosen,
/// from the folder of the program, and what it says is read in the terminal like the words of any
/// operation; STOP stops it the same way. What was run is kept (Core\TerminalMemory.cs): the list
/// beside the box has the pinned commands first, then the history, a pin and a cross on every
/// line. While an operation goes a command waits its turn and runs after it.
///
/// The owner, 11.10.2026: "In the other applications this is a working thing: commands are sent
/// at the bottom, there is a cache of commands and pins, removal from the history and from the
/// pins. In Get Tools ... there is no terminal until something runs, and while it runs the
/// terminal cannot be used."
/// </summary>
public partial class MainWindow
{
    TerminalMemory _commands = new();
    /// <summary>The commands sent while an operation went: each runs when its turn comes.</summary>
    readonly Queue<(string Shell, string Command)> _queued = new();
    /// <summary>Where the arrows of the box stand in the commands that were run; -1 - at what is being typed.</summary>
    int _recall = -1;
    string _typed = "";

    const string TerminalOperation = "terminal_command";

    void WireTerminal()
    {
        _commands = TerminalMemory.Load();
        CommandBox.Text = _commands.Last;
        PixelGrid.Field(CommandBox);
        foreach (var (control, id) in new (Control, string)[] { (CommandBox, "command_box"), (CommandRunButton, "command_run"),
                     (CommandHistoryButton, "command_history"), (ShellPwsh, "shell_pwsh"), (ShellCmd, "shell_cmd") })
            Avalonia.Automation.AutomationProperties.SetAutomationId(control, id);
        MarkShell();
        ShellPwsh.Click += (_, _) => SetShell("pwsh");
        ShellCmd.Click += (_, _) => SetShell("cmd");
        CommandRunButton.Click += (_, _) => SendCommand();
        CommandBox.KeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter) { SendCommand(); e.Handled = true; }
            else if (e.Key == Key.Up) { Recall(1); e.Handled = true; }
            else if (e.Key == Key.Down) { Recall(-1); e.Handled = true; }
        };

        var search = new TextBox { Classes = { "masks" }, PlaceholderText = Lang.T("поиск по списку"), MinHeight = 24, Height = 24, Padding = new Thickness(8, 0), VerticalContentAlignment = VerticalAlignment.Center };
        PixelGrid.Field(search);
        Avalonia.Automation.AutomationProperties.SetAutomationId(search, "find_commands");
        var lines = new StackPanel();
        var panel = new StackPanel
        {
            Children =
            {
                search,
                new ScrollViewer { Content = lines, MaxHeight = 380, Margin = new Thickness(0, 6, 0, 0), HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled },
            },
        };
        var flyout = new Flyout { Content = panel, Placement = PlacementMode.TopEdgeAlignedRight };
        flyout.FlyoutPresenterClasses.Add("dropdown");
        CommandHistoryButton.Flyout = flyout;

        void Fill()
        {
            lines.Children.Clear();
            var words = Search.Words(search.Text);
            foreach (var (command, pinned) in _commands.Lines())
            {
                if (words.Any(word => !command.Contains(word, StringComparison.OrdinalIgnoreCase))) continue;
                var line = new Grid { Classes = { "dropline" }, ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto") };
                var sign = Icons.Sign("pin");
                var pin = new Button { Classes = { "cardicon" }, Content = sign };
                if (pinned)
                {
                    sign.Stroke = Tones.Of("gold").Ink;
                    pin.Opacity = 1;
                }
                else pin.Classes.Add("ghost");
                ToolTip.SetTip(pin, Lang.T(pinned ? "Открепить: строка вернётся в общий список." : "Закрепить: строка встанет в начало списка."));
                Avalonia.Automation.AutomationProperties.SetAutomationId(pin, "pin_command_" + command);
                pin.Click += (_, _) =>
                {
                    _commands.Pin(command, !pinned);
                    _commands.Save();
                    Fill();
                };
                line.Children.Add(pin);

                var pick = new Button { Classes = { "dropdownrow" }, Content = new TextBlock { Text = command, TextTrimming = Avalonia.Media.TextTrimming.CharacterEllipsis } };
                ToolTip.SetTip(pick, command);
                Avalonia.Automation.AutomationProperties.SetAutomationId(pick, "command_" + command);
                pick.Click += (_, _) =>
                {
                    flyout.Hide();
                    Put(command);
                };
                Grid.SetColumn(pick, 1);
                line.Children.Add(pick);

                var gone = new Button { Classes = { "cardicon", "ghost" }, Content = Icons.Sign("x") };
                ToolTip.SetTip(gone, Lang.T("Убрать команду из списка."));
                Avalonia.Automation.AutomationProperties.SetAutomationId(gone, "forget_command_" + command);
                gone.Click += (_, _) =>
                {
                    _commands.Forget(command);
                    _commands.Save();
                    Fill();
                };
                Grid.SetColumn(gone, 2);
                line.Children.Add(gone);
                lines.Children.Add(line);
            }
            if (lines.Children.Count == 0)
                lines.Children.Add(new TextBlock { Classes = { "note" }, Text = Lang.T(_commands.Lines().Count == 0 ? "команд ещё не было" : "ничего не найдено"), Margin = new Thickness(4, 2) });
        }

        search.TextChanged += (_, _) => Fill();
        flyout.Opening += (_, _) =>
        {
            panel.Width = Math.Max(CommandRow.Bounds.Width - 18, 420);
            Fill();
        };
        flyout.Opened += (_, _) => search.Focus();
    }

    void SetShell(string shell)
    {
        _commands.Shell = shell;
        _commands.Save();
        MarkShell();
    }

    void MarkShell()
    {
        ShellPwsh.IsChecked = _commands.Shell != "cmd";
        ShellCmd.IsChecked = _commands.Shell == "cmd";
    }

    /// <summary>A command goes into the box, the caret after it.</summary>
    void Put(string command)
    {
        CommandBox.Text = command;
        CommandBox.CaretIndex = command.Length;
        CommandBox.Focus();
    }

    /// <summary>The arrows of the box: up - the command before, down - the one after, and back to what was being typed.</summary>
    void Recall(int step)
    {
        var known = _commands.Recall();
        if (known.Count == 0) return;
        if (_recall < 0) _typed = CommandBox.Text ?? "";
        _recall = Math.Clamp(_recall + step, -1, known.Count - 1);
        Put(_recall < 0 ? _typed : known[_recall]);
    }

    /// <summary>Enter, or the button: the command is remembered and runs - at once, or after the operation that goes now.</summary>
    void SendCommand()
    {
        var command = (CommandBox.Text ?? "").Trim();
        if (command.Length == 0) return;
        if (_told is null || !Door.Ready) { SetStatus("Консольная команда программы ещё не готова."); return; }
        _commands.Used(command);
        _commands.Last = "";
        _commands.Save();
        _recall = -1;
        CommandBox.Text = "";
        if (_runner.Running || _asking)
        {
            _queued.Enqueue((_commands.Shell, command));
            SetStatus(Lang.F("Команда в очереди ({0}): пойдёт, когда закончится операция.", _queued.Count));
            return;
        }
        StartCommand(_commands.Shell, command);
    }

    void StartCommand(string shell, string command)
    {
        if (_told?.CardOperations.FirstOrDefault(node => node.Id == TerminalOperation) is not { } operation)
        {
            SetStatus("В описании программы нет операции терминала.");
            return;
        }
        var values = new JsonObject { ["shell"] = shell, ["command"] = command }.ToJsonString();
        Begin(operation, "", values, new Dictionary<string, string> { ["shell"] = shell, ["command"] = command }, (shell == "cmd" ? "CMD" : "PS") + " › " + command, command: true);
    }

    /// <summary>An operation has ended: the next command of the queue runs.</summary>
    void NextCommand()
    {
        if (_queued.Count == 0 || _runner.Running) return;
        var (shell, command) = _queued.Dequeue();
        StartCommand(shell, command);
    }

    /// <summary>The window closes: the command left in the box is kept for the next start; what waited its turn does not run.</summary>
    void KeepCommand()
    {
        _queued.Clear();
        _commands.Last = (CommandBox.Text ?? "").Trim();
        _commands.Save();
    }
}
