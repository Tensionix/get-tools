using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AudionGetTools.Core;

/// <summary>
/// What the command line of the terminal remembers: the commands that were run, the ones a person
/// pinned, the command left in the box, the shell. Kept in config\terminal_commands.json - the
/// file and the keys of the window before this one, so what a person ran there is here too. A
/// Debug copy keeps the file beside itself: a check of the window must not change the commands
/// of the window the owner works in.
/// </summary>
public sealed class TerminalMemory
{
    /// <summary>The commands that were run, the newest first.</summary>
    [JsonPropertyName("history")] public List<string> History { get; set; } = new();
    /// <summary>The commands a person pinned, the last pinned first: they stand over the history and do not leave it by age.</summary>
    [JsonPropertyName("pinned")] public List<string> Pinned { get; set; } = new();
    /// <summary>The command left in the box when the window closed.</summary>
    [JsonPropertyName("last")] public string Last { get; set; } = "";
    /// <summary>"pwsh" - PowerShell, "cmd" - the command processor.</summary>
    [JsonPropertyName("shell")] public string Shell { get; set; } = "pwsh";
    /// <summary>The working folder the window before this one kept; this window runs a command from the folder of the program and leaves the key as it found it.</summary>
    [JsonPropertyName("cwd")] public string Cwd { get; set; } = "";

    /// <summary>How many commands the history holds: the oldest goes when a new one comes.</summary>
    public const int Limit = 200;

    static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static string FilePath => Paths.Released ? Path.Combine(Paths.Config, "terminal_commands.json") : Path.Combine(AppContext.BaseDirectory, "terminal_commands.json");

    /// <summary>A file that does not read is an empty memory, not an error.</summary>
    public static TerminalMemory Load()
    {
        TerminalMemory? found = null;
        try
        {
            if (File.Exists(FilePath)) found = JsonSerializer.Deserialize<TerminalMemory>(File.ReadAllText(FilePath, Encoding.UTF8), Json);
        }
        catch { }
        found ??= new TerminalMemory();
        found.History = Clean(found.History);
        found.Pinned = Clean(found.Pinned);
        found.Last ??= "";
        found.Cwd ??= "";
        if (found.Shell != "cmd") found.Shell = "pwsh";
        return found;
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, Json), new UTF8Encoding(false));
        }
        catch { }
    }

    static List<string> Clean(IEnumerable<string>? commands)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        return (commands ?? Enumerable.Empty<string>()).Select(command => (command ?? "").Trim())
            .Where(command => command.Length > 0 && seen.Add(command)).Take(Limit).ToList();
    }

    /// <summary>A command was run: it stands first in the history, once.</summary>
    public void Used(string command)
    {
        command = command.Trim();
        if (command.Length == 0) return;
        History.Remove(command);
        History.Insert(0, command);
        if (History.Count > Limit) History.RemoveRange(Limit, History.Count - Limit);
    }

    /// <summary>A command is pinned - it stands first among the pinned - or the pin is taken off: it stays in the history.</summary>
    public void Pin(string command, bool on)
    {
        command = command.Trim();
        if (command.Length == 0) return;
        Pinned.Remove(command);
        if (!on)
        {
            if (!History.Contains(command)) History.Insert(0, command);
            return;
        }
        Pinned.Insert(0, command);
    }

    /// <summary>A command is forgotten: out of the history and out of the pinned.</summary>
    public void Forget(string command)
    {
        command = command.Trim();
        History.Remove(command);
        Pinned.Remove(command);
    }

    /// <summary>The commands as the list shows them: the pinned first, then the history without them.</summary>
    public List<(string Command, bool Pinned)> Lines() =>
        Pinned.Select(command => (command, true)).Concat(History.Where(command => !Pinned.Contains(command)).Select(command => (command, false))).ToList();

    /// <summary>The commands the arrows of the box walk: the history, the newest first; the pinned that were never run after it.</summary>
    public List<string> Recall() => History.Concat(Pinned.Where(command => !History.Contains(command))).ToList();
}
