using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;

namespace AudionGetTools.Core;

/// <summary>
/// What --self-test checks once the window has been put together: the parts of the window that
/// need no screen - what the door says of the program, the values of the fields, the line a run
/// is written with, the colours of the terminal. Each check answers with a line; one that failed
/// is named on the error stream, and the program ends with 2.
/// </summary>
public static class SelfTest
{
    /// <summary>
    /// Lines in the colours their writer gave them: a colour closed on its own line, the kinds of
    /// a colour and of a look, a colour left open and closed on the next line, a line nobody
    /// coloured. A picture of the terminal is taken with the same lines (MainWindow.Run.cs).
    /// </summary>
    public static readonly string[] ColouredLines =
    {
        "\u001b[33m[WARN] yellow by its writer\u001b[0m and plain after it",
        "\u001b[96mVivaldi\u001b[0m [\u001b[36mVivaldi.Vivaldi\u001b[0m] \u001b[1mbold\u001b[0m \u001b[2mdim\u001b[0m \u001b[4munderlined\u001b[0m \u001b[38;5;208mthe 208th\u001b[0m \u001b[38;2;120;200;80m24-bit\u001b[0m",
        "\u001b[31mred \u001b[32mgreen \u001b[34mblue \u001b[35mmagenta \u001b[90mgrey \u001b[97mwhite",
        "still white: the colour was left open\u001b[0m",
        "[OK] a line nobody coloured",
    };

    sealed class Report
    {
        public readonly List<string> Passed = new();
        public readonly List<string> Failed = new();

        public void Check(string name, bool good, string found = "")
        {
            if (good) Passed.Add(name);
            else Failed.Add(found.Length > 0 ? name + ": " + found : name);
        }
    }

    /// <summary>A file the report is written to as well, "ok" and "FAIL" lines alike; "" - the two streams alone.</summary>
    public static string ReportFile { get; set; } = "";

    /// <summary>True when the build of App\ asks: the window was built a moment ago, and its number is checked too.</summary>
    public static bool JustBuilt { get; set; }

    /// <summary>Runs every check. What passed is written to the output, what failed to the error stream; true when nothing failed.</summary>
    public static bool Run()
    {
        var report = new Report();
        foreach (var check in new (string Name, Action<Report> Body)[]
        {
            ("version", Number),
            ("rights", Rights),
            ("standing", Standing),
            ("describe", Describe),
            ("dropdown", Dropdown),
            ("makers", Makers),
            ("terminal", Terminal),
            ("values", ValuesOfPages),
            ("portable", CarriedTicks),
            ("values of the program", ValuesOfTheProgram),
            ("lists an operation needs", ListsAnOperationNeeds),
            ("lists kept", ListsKept),
            ("search", SearchOfALists),
            ("version card", VersionCards),
            ("value asked for", ValueAskedFor),
            ("run line", RunLine),
            ("source", SourceOfARun),
            ("colours", Colours),
            ("error stream", ErrorStream),
        })
        {
            try { check.Body(report); }
            catch (Exception error) { report.Failed.Add($"{check.Name}: {error.GetType().Name}: {error.Message}"); }
        }
        foreach (var line in report.Passed) try { Console.Out.WriteLine("ok   " + line); } catch { }
        foreach (var line in report.Failed) try { Console.Error.WriteLine("FAIL " + line); } catch { }
        if (ReportFile.Length > 0)
            try
            {
                System.IO.File.WriteAllLines(ReportFile, report.Passed.Select(line => "ok   " + line).Concat(report.Failed.Select(line => "FAIL " + line)), new System.Text.UTF8Encoding(false));
            }
            catch { }
        return report.Failed.Count == 0;
    }

    // ------------------------------------------------------------ the number of the program

    /// <summary>
    /// The executable carries the number of config\version.json: it is written in when the window
    /// is built, so a build that took another number shows here. Asked only of a window that was
    /// just built (--just-built): a release moves the number on without rebuilding App\, and the
    /// number the window shows it reads from the file.
    /// </summary>
    /// <summary>A list of models is a field that opens its list under itself: the default first, the pinned next, a word of trouble no line at all.</summary>
    static void Dropdown(Report report)
    {
        // Read the way the answer of the door is read: "pinned" is a boolean there.
        static Option Of(string value, bool pinned = false)
        {
            using var doc = JsonDocument.Parse("[{\"value\": \"" + value + "\", \"label\": \"" + value + "\", \"pinned\": " + (pinned ? "true" : "false") + "}]");
            return Described.ParseOptions(doc.RootElement)[0];
        }
        var order = string.Join(",", Views.Form.DropdownOrder(new[] { Of("c"), Of("b", true), Of(""), Of("__request_failed__"), Of("a", true), Of("d") }).Select(option => option.Key.Length == 0 ? "-" : option.Key));
        report.Check("dropdown: the default first, the pinned next in the order of the list, a word of trouble is no line", order == "-,b,a,c,d", order);
        // The plan of the AI page asks first, then instructs, then chooses the model: the layout names the order of its blocks.
        var plan = _told?.Operations().FirstOrDefault(node => node.Id == "ai_plan_packages");
        var blocks = plan is null ? "" : string.Join(",", Views.Form.InOrder(plan.Fields.GroupBy(field => field.Group), plan.Groups).Select(block => block.Key));
        report.Check("blocks: the form of the AI plan stands as the layout names - the request, the instruction, the models, the rest",
            blocks.StartsWith("request,prompt,provider,generation", StringComparison.Ordinal), blocks);
        // The forms of the program as the door described them: a form is told once, whichever operations draw it.
        var models = (_told?.Forms.Values.SelectMany(form => form) ?? Enumerable.Empty<Field>()).Where(field => field.Id is "openai_model" or "gemini_model").ToList();
        report.Check("dropdown: the two lists of models are drop-downs with pins that follow their keys",
            models.Count >= 2 && models.All(field => field.View == "dropdown" && field.Pins.Length > 0 && field.DependsOn.Count == 1 && field.Dynamic),
            string.Join("; ", models.Select(field => $"{field.Id}: view={field.View} pins={field.Pins.Length > 0} depends={string.Join(",", field.DependsOn)}")));
    }

    /// <summary>The command line of the terminal: what it remembers, and the operation it runs.</summary>
    static void Terminal(Report report)
    {
        var memory = new TerminalMemory();
        foreach (var command in new[] { "one", "two", "three", "two" }) memory.Used(command);
        report.Check("terminal: a command that was run stands first in the history, once", string.Join(",", memory.History) == "two,three,one", string.Join(",", memory.History));
        memory.Pin("one", true);
        memory.Pin("never run", true);
        var shown = string.Join(",", memory.Lines().Select(line => (line.Pinned ? "*" : "") + line.Command));
        report.Check("terminal: the pinned stand first, the last pinned at the head, then the history without them", shown == "*never run,*one,two,three", shown);
        memory.Pin("one", false);
        memory.Pin("never run", false);
        shown = string.Join(",", memory.Lines().Select(line => (line.Pinned ? "*" : "") + line.Command));
        report.Check("terminal: a pin taken off leaves the command in the history", shown == "never run,two,three,one", shown);
        memory.Forget("three");
        memory.Pin("two", true);
        memory.Forget("two");
        shown = string.Join(",", memory.Lines().Select(line => (line.Pinned ? "*" : "") + line.Command));
        report.Check("terminal: a command forgotten leaves the history and the pinned", shown == "never run,one", shown);
        for (int i = 0; i < TerminalMemory.Limit + 20; i++) memory.Used("command " + i);
        report.Check("terminal: the history holds no more than its limit, the newest kept", memory.History.Count == TerminalMemory.Limit && memory.History[0] == "command " + (TerminalMemory.Limit + 19), memory.History.Count + " | " + memory.History[0]);
        var operation = _told?.CardOperations.FirstOrDefault(node => node.Id == "terminal_command");
        report.Check("terminal: the program describes the operation a command runs by - a shell, a command, a folder",
            operation is not null && operation.Runs && string.Join(",", operation.Fields.Select(field => field.Id)) == "shell,command,cwd",
            operation is null ? "no such operation" : string.Join(",", operation.Fields.Select(field => field.Id)));
    }

    /// <summary>The pages of DRIVERS stand by the kind of device; a choice of a page names what the page fixes while it is on.</summary>
    static void Makers(Report report)
    {
        var drivers = _told?.Layout.Sections.FirstOrDefault(section => section.Id == "drivers");
        var ids = string.Join(",", drivers?.Pages.Select(page => page.Id) ?? Enumerable.Empty<string>());
        report.Check("makers: DRIVERS stand by the kind of device", ids == "gpu,chipset,wifi,bluetooth,ethernet,audio,this_machine", ids);
        if (drivers?.Pages.FirstOrDefault(page => page.Id == "wifi") is not { } wifi) return;
        string Fixes() => string.Join(",", wifi.Fixed.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Key + "=" + FieldKinds.Text(pair.Value)));
        report.Check("makers: a page opens on one of its makers, and fixes what that one names", wifi.Choices.Any(choice => choice.Id == wifi.Chosen) && wifi.Fixed.ContainsKey("vendor"), wifi.Chosen + " | " + Fixes());
        var first = wifi.Chosen;
        wifi.Choose("realtek");
        var realtek = Fixes();
        wifi.Choose("intel");
        var intel = Fixes();
        report.Check("makers: another maker - another set of values, nothing of the one before left", realtek == "msc_kind=wifi,msc_wifi=Realtek Wi-Fi,vendor=mscatalog" && intel == "tpu_driver=Intel Wi-Fi,tpu_kind=drivers,vendor=techpowerup", realtek + " | " + intel);
        report.Check("makers: a maker the page does not have changes nothing", !wifi.Choose("no such") && wifi.Chosen == "intel", wifi.Chosen);
        // The values of the page follow: the lists of the page are asked with the maker that is on.
        var store = new ValueStore(_told!.Layout.Shared);
        wifi.Choose("realtek");
        var held = store.Open(wifi);
        var seen = held.Get("vendor") + "/" + held.Get("msc_wifi");
        wifi.Choose("intel");
        held = store.Open(wifi);
        report.Check("makers: the values of the page are those of the maker that is on", seen == "mscatalog/Realtek Wi-Fi" && held.Get("vendor") == "techpowerup" && held.Get("tpu_driver") == "Intel Wi-Fi", seen + " | " + held.Get("vendor") + "/" + held.Get("tpu_driver"));
        wifi.Choose(first);
    }

    /// <summary>The badge of versions over a list of drivers: the installed one orange when the PC is behind, green when it is not.</summary>
    static void Standing(Report report)
    {
        static string Told(Option option) => string.Join(" ", Views.Form.StandingWords(option).Select(part => $"{part.Text}/{part.Kind}/{part.Tone}"));
        var behind = Told(new Option { Running = "6.0.9764.1", Newest = "6.0.10029.1", Behind = true });
        report.Check("standing: a PC behind the newest release - the installed version in the tone of a preview, the newest one faint",
            behind == "УСТАНОВЛЕНА/word/beta 6.0.9764.1/number/beta ·/dot/ ПОСЛЕДНЯЯ/word/ 6.0.10029.1/number/", behind);
        var same = Told(new Option { Running = "6.0.10029.1", Newest = "6.0.10029.1" });
        report.Check("standing: a PC on the newest release - one green word and the version", same == "УСТАНОВЛЕНА ПОСЛЕДНЯЯ/word/latest 6.0.10029.1/number/latest", same);
        report.Check("standing: a list that says nothing of this PC's driver has no badge", Told(new Option { Newest = "1.0", Behind = true }).Length == 0);
    }

    static void Rights(Report report)
    {
        // A click on "Rights: standard" starts this very program again, by the verb Windows asks its question for.
        var start = SystemInfo.ElevatedStart();
        report.Check("rights: the window starts itself again with the rights - the same program, from the folder of the project",
            start is not null && start.Verb == "runas" && start.UseShellExecute
            && string.Equals(start.FileName, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase)
            && string.Equals(start.WorkingDirectory, Paths.Root, StringComparison.OrdinalIgnoreCase),
            start is null ? "no start" : $"{start.FileName} | {start.Verb} | {start.WorkingDirectory}");
        foreach (bool ru in new[] { true, false })
        {
            var badge = SystemInfo.Rights(ru);
            report.Check($"rights: the badge of a window without the rights says what a click does [{(ru ? "ru" : "en")}]",
                badge is not null && (SystemInfo.Elevated || badge.Tip.Contains(ru ? "Щелчок - перезапустить" : "A click restarts")), badge?.Tip ?? "no badge");
        }
    }

    static void Number(Report report)
    {
        if (!JustBuilt) return;
        var built = typeof(SelfTest).Assembly.GetName().Version;
        var wanted = Paths.Version;
        var carried = built is null ? "" : $"{built.Major}.{built.Minor}.{built.Build}";
        report.Check("version: the executable carries the number of config\\version.json", wanted is not null && carried == wanted.Split('-', '+')[0], $"the executable {carried}, the file {wanted ?? "(unreadable)"}");
    }

    // ------------------------------------------------------------ what the door says of the program

    static void Describe(Report report)
    {
        // Off the window's thread: nothing turns its queue here, and an answer awaited on it would never come.
        var (told, trouble) = Task.Run(Door.Describe).GetAwaiter().GetResult();
        report.Check("describe: the door answered and the answer reads", told is not null, trouble);
        if (told is null) return;
        _told = told;
        var places = told.Operations().ToList();
        var nameless = places.Where(node => node.Id.Length == 0 || node.Title.Length == 0).Select(node => node.Id.Length > 0 ? node.Id : "(no id)").ToList();
        report.Check($"describe: each of the {places.Count} places of operations has a node and a name", places.Count > 0 && nameless.Count == 0, string.Join(", ", nameless));
        var homeless = told.Layout.Sections.SelectMany(section => section.Pages).SelectMany(page => page.Operations)
            .Where(node => node.Page?.Section is null).Select(node => node.Id).ToList();
        report.Check("describe: every operation of a page knows its page and its section", homeless.Count == 0, string.Join(", ", homeless));
        var pages = told.Layout.Sections.SelectMany(section => section.Pages).Where(page => page.Id.Length == 0 || page.Title.Length == 0).Select(page => page.Id).ToList();
        report.Check("describe: every page has an id and a name", pages.Count == 0, string.Join(", ", pages));
        // The forms are told once and put together here: an operation that names a form has its fields,
        // without those its page sets by itself; one that runs at a press has none.
        var formless = places.Where(node => node.Form.Length > 0 && node.Fields.Count == 0 && node.Dropped.Count < told.Forms[node.Form].Count).Select(node => node.Id).ToList();
        var drawn = places.Where(node => node.Page is { } page && node.Fields.Any(field => page.Fixed.ContainsKey(field.Id))).Select(node => node.Id).Distinct().ToList();
        var direct = places.Where(node => node.Direct != (node.Runs && node.Fields.Count == 0)).Select(node => node.Id).ToList();
        report.Check($"describe: the {told.Forms.Count} forms told once are put together - {places.Count(node => node.Fields.Count > 0)} places have their fields",
            told.Forms.Count > 0 && formless.Count == 0 && drawn.Count == 0 && direct.Count == 0, $"without fields: {string.Join(", ", formless)}; draw a fixed field: {string.Join(", ", drawn)}; direct: {string.Join(", ", direct)}");
    }

    /// <summary>What the door said, for the checks that read the program itself.</summary>
    static Described? _told;

    // ------------------------------------------------------------ the values of the fields

    /// <summary>
    /// A program of two pages that draw one form, as the door would describe it: a flag, a flag the
    /// window shares, a list that depends on a field each page fixes, a list of the manifest, and
    /// three sets - one turns the flag over, one names values, one picks by a rule.
    /// </summary>
    static Described TwoPages()
    {
        const string form = """
            { "id": "download", "title": "Download", "runs": true, "fields": [
                { "id": "portable", "kind": "checkbox", "default": false },
                { "id": "keep", "kind": "checkbox", "default": true },
                { "id": "versions", "kind": "checkboxes", "options_source": "module:list", "depends_on": ["vendor", "portable"] },
                { "id": "apps", "kind": "checkboxes", "options": [ {"value": "a", "label": "a"}, {"value": "b", "label": "b"}, {"value": "c", "label": "c"} ] },
                { "id": "sets", "kind": "profile_buttons", "presets": [
                    { "id": "portable_only", "label": "Portable", "toggle": {"portable": true} },
                    { "id": "work", "label": "Work", "values": {"apps": ["b", "a"]} },
                    { "id": "latest", "label": "Latest", "pick": {"versions": "latest"} } ] } ] }
            """;
        return Described.Parse("""
            { "version": "0", "paths": {}, "card_operations": [], "layout": { "service": [], "shared": ["keep"], "sections": [ { "id": "s", "title": "S", "pages": [
                { "id": "a", "title": "A", "fixed": {"vendor": "one"}, "operations": [ FORM ] },
                { "id": "b", "title": "B", "fixed": {"vendor": "two"}, "operations": [ FORM ] } ] } ] } }
            """.Replace("FORM", form));
    }

    /// <summary>The value an operation would send to the door for a field, as its JSON says it.</summary>
    static string Sent(Values values, Node node, string field)
    {
        using var doc = JsonDocument.Parse(values.ForDoor(node));
        return doc.RootElement.TryGetProperty(field, out var value) ? value.GetRawText() : "(not sent)";
    }

    static void ValuesOfPages(Report report)
    {
        var told = TwoPages();
        var (pageA, pageB) = (told.Layout.Sections[0].Pages[0], told.Layout.Sections[0].Pages[1]);
        var store = new ValueStore(told.Layout.Shared);
        var (a, b) = (store.Open(pageA), store.Open(pageB));
        var (nodeA, nodeB) = (pageA.Operations[0], pageB.Operations[0]);
        Field Of(Node node, string id) => node.Fields.First(field => field.Id == id);
        var sets = Of(nodeA, "sets").Presets.ToDictionary(preset => preset.Id);

        // ---- one field on two pages
        a.Set(Of(nodeA, "portable"), "true");
        report.Check("values: a field set on one page is not seen on another", a.Get("portable") == "true" && b.Get("portable") == "false", $"a={a.Get("portable")} b={b.Get("portable")}");
        report.Check("values: each page sends the door its own value", Sent(a, nodeA, "portable") == "true" && Sent(b, nodeB, "portable") == "false", $"a sends {Sent(a, nodeA, "portable")}, b sends {Sent(b, nodeB, "portable")}");
        report.Check("values: what a page fixes goes to the door from that page alone", Sent(a, nodeA, "vendor") == "\"one\"" && Sent(b, nodeB, "vendor") == "\"two\"", $"{Sent(a, nodeA, "vendor")} {Sent(b, nodeB, "vendor")}");
        a.Reset(new[] { Of(nodeA, "portable") });

        // ---- a field the layout names shared
        int heard = 0;
        b.Changed += id => { if (id == "keep") heard++; };
        a.Set(Of(nodeA, "keep"), "false");
        report.Check("values: a shared field is one value for both pages", a.Get("keep") == "false" && b.Get("keep") == "false" && Sent(b, nodeB, "keep") == "false", $"a={a.Get("keep")} b={b.Get("keep")}");
        report.Check("values: the other page hears the change of a shared field", heard == 1, heard + " times");

        // ---- what a page fixes resets nothing of another page
        a.Set(Of(nodeA, "versions"), "1\n2");
        store.Open(pageB);
        store.Open(pageA);
        report.Check("values: a page coming into sight does not reset the list of another, nor its own", a.Get("versions") == "1\n2", a.Get("versions").Replace('\n', ','));
        a.Fix("vendor", "three");
        report.Check("values: a list goes when a field it depends on changes on its own page", a.Get("versions") == "", a.Get("versions").Replace('\n', ','));
        a.Fix("vendor", "one");

        // ---- a set that turns a flag over
        var portable = sets["portable_only"];
        bool before = Presets.IsActive(portable, a);
        Presets.Press(portable, a, _ => Array.Empty<Option>());
        bool once = Presets.IsActive(portable, a), there = Presets.IsActive(portable, b);
        Presets.Press(portable, a, _ => Array.Empty<Option>());
        bool twice = Presets.IsActive(portable, a);
        report.Check("sets: a set with a flag is on after a press and off after the second", !before && once && !twice, $"before {before}, once {once}, twice {twice}");
        report.Check("sets: a set turned on on one page is off on another", !there);

        // ---- a set that names values
        var work = sets["work"];
        Presets.Press(work, a, _ => Array.Empty<Option>());
        bool lit = Presets.IsActive(work, a);
        a.Set(Of(nodeA, "apps"), "a");
        report.Check("sets: a set with values is on while its fields hold them, whatever the order of the ticks", lit && a.Get("apps") == "a" && !Presets.IsActive(work, a), $"lit {lit}, then {Presets.IsActive(work, a)}");
        report.Check("sets: a set that picks by a rule has no state to show", Presets.HasState(portable) && Presets.HasState(work) && !Presets.HasState(sets["latest"]));
        var options = new[] { new Option { Value = JsonDocument.Parse("\"9\"").RootElement, Label = "9" }, new Option { Value = JsonDocument.Parse("\"8\"").RootElement, Label = "8" } };
        Presets.Press(sets["latest"], a, _ => options);
        report.Check("sets: a rule picks among the options of the list as it is now", a.Get("versions") == "9", a.Get("versions"));
        // "Golden": every card with a star, wherever its list writes the star.
        var starred = new[] { Card("w1"), Card("n1"), Card("plain") };
        (starred[0].Label, starred[1].Label, starred[2].Label) = ("★ 26300.9550 - latest", "616.92 - 9 Sep 2026 - ★ golden", "616.64 - 3 Sep 2026");
        var golden = new Preset { Id = "golden", Pick = new Dictionary<string, string> { ["versions"] = "golden" } };
        Presets.Press(golden, a, _ => starred);
        report.Check("sets: \"golden\" ticks every card with a star, at the start of its name or at its end", a.Get("versions") == "w1\nn1", a.Get("versions").Replace('\n', ','));
        a.Reset(nodeA.Fields);

        // ---- what a list ticks by itself
        var versions = Of(nodeA, "versions");
        a.Offer(versions, "9");
        bool offered = a.Get(versions) == "9" && !a.IsTouched(versions);
        a.Set(Of(nodeA, "portable"), "true");
        bool gone = a.Get(versions) == "";
        a.Offer(versions, "9");
        report.Check("lists: a tick of the list is set when the list is shown from the memory of the window", offered && gone && a.Get(versions) == "9", $"offered {offered}, gone {gone}, then <{a.Get(versions)}>");
        a.Set(versions, "");
        a.Offer(versions, "9");
        report.Check("lists: a tick the person took off does not come back", a.Get(versions) == "" && a.IsTouched(versions), $"<{a.Get(versions)}>");
        a.Reset(new[] { versions });
        a.Offer(versions, "9");
        report.Check("lists: \"back to the defaults\" lets the list tick its own again", a.Get(versions) == "9" && !a.IsTouched(versions), $"<{a.Get(versions)}>");
    }

    /// <summary>A card of a list of versions: its value, and the two files of its version when the list names them.</summary>
    static Option Card(string value, string installer = "", string portable = "") =>
        new() { Value = JsonDocument.Parse(JsonSerializer.Serialize(value)).RootElement, Label = value, InstallerId = installer, PortableId = portable };

    /// <summary>
    /// "Portable" turned over: the ticks follow their versions to the other file, as in 2.17.2.
    /// Version 1 and 2 have both files, version 3 an installer alone, version 4 a portable build alone.
    /// </summary>
    static void CarriedTicks(Report report)
    {
        var told = TwoPages();
        var page = told.Layout.Sections[0].Pages[0];
        var a = new ValueStore(told.Layout.Shared).Open(page);
        var node = page.Operations[0];
        var versions = node.Fields.First(field => field.Id == "versions");
        var portable = node.Fields.First(field => field.Id == "sets").Presets.First(preset => preset.Id == "portable_only");
        var installers = new[] { Card("i1", "i1", "p1"), Card("i2", "i2", "p2"), Card("i3"), Card("p4", "", "p4") };
        var portables = new[] { Card("p1", "i1", "p1"), Card("p2", "i2", "p2"), Card("p4", "", "p4") };

        a.Set(versions, "i1\ni3\np4");
        var carried = Presets.Carried(portable, a, node.Fields, _ => installers);
        Presets.Press(portable, a, _ => installers);
        bool reset = a.Get(versions) == "";
        string moved = carried.TryGetValue("versions", out var ticks) ? string.Join(",", ticks) : "(none)";
        if (ticks is not null) Presets.Land(a, versions, ticks, portables);
        report.Check("portable: turned on, the ticks move to the portable build of the same version; a version without one drops out",
            reset && moved == "p1,p4" && a.Get(versions) == "p1\np4", $"reset {reset}, carried {moved}, now <{a.Get(versions).Replace('\n', ',')}>");
        a.Offer(versions, "p2");
        report.Check("portable: what the new list ticks by itself does not replace the ticks carried over", a.Get(versions) == "p1\np4", a.Get(versions).Replace('\n', ','));

        carried = Presets.Carried(portable, a, node.Fields, _ => portables);
        Presets.Press(portable, a, _ => portables);
        moved = carried.TryGetValue("versions", out ticks) ? string.Join(",", ticks) : "(none)";
        if (ticks is not null) Presets.Land(a, versions, ticks, installers);
        report.Check("portable: turned off, the ticks move back to the installer; a portable build alone stays",
            moved == "i1,p4" && a.Get(versions) == "i1\np4", $"carried {moved}, now <{a.Get(versions).Replace('\n', ',')}>");

        // Nothing ticked: nothing is carried, and the list that comes may tick its own.
        a.Reset(node.Fields);
        carried = Presets.Carried(portable, a, node.Fields, _ => installers);
        Presets.Press(portable, a, _ => installers);
        a.Offer(versions, "p1");
        report.Check("portable: with nothing ticked nothing is carried, and the new list ticks its own", carried.Count == 0 && a.Get(versions) == "p1", $"carried {carried.Count}, now <{a.Get(versions)}>");

        // A version whose only ticks have no portable build: turned on, the list is left with none - by choice.
        a.Reset(node.Fields);
        a.Set(versions, "i3");
        carried = Presets.Carried(portable, a, node.Fields, _ => installers);
        Presets.Press(portable, a, _ => installers);
        if (carried.TryGetValue("versions", out ticks)) Presets.Land(a, versions, ticks, portables);
        a.Offer(versions, "p1");
        report.Check("portable: ticks that have no portable build are gone, and the list does not tick its own in their place", a.Get(versions) == "" && a.IsTouched(versions), $"<{a.Get(versions)}>");
    }

    /// <summary>
    /// The program itself: "Portable" pressed on a page of TechPowerUp must not reach the page of
    /// NVIDIA, where no button would take it back and no driver has a portable build (the audit
    /// of 10.10.2026, finding 2).
    /// </summary>
    static void ValuesOfTheProgram(Report report)
    {
        if (_told is not { } told) { report.Check("values of the program: the door described it", false, "no description"); return; }
        static string Vendor(Page page) => page.Fixed.TryGetValue("vendor", out var vendor) ? FieldKinds.Text(vendor) : "";
        static Node? Download(Page page) => page.Operations.FirstOrDefault(node => node.Fields.Any(field => field.Id == "vendor_portable_only"));
        static Preset? Portable(Node node) => node.Fields.SelectMany(field => field.Presets).FirstOrDefault(preset => preset.Toggle.ContainsKey("vendor_portable_only"));
        var pages = told.Layout.Sections.SelectMany(section => section.Pages).Where(page => Download(page) is not null).ToList();
        // A page that is TechPowerUp's for good, and the page of the video drivers with NVIDIA chosen on it.
        var from = pages.FirstOrDefault(page => page.Choices.Count == 0 && Vendor(page) == "techpowerup");
        var to = pages.FirstOrDefault(page => page.Choices.Any(choice => choice.Id == "nvidia"));
        to?.Choose("nvidia");
        report.Check("values of the program: a page of TechPowerUp and the page of NVIDIA draw the form with the portable flag", from is not null && to is not null);
        if (from is null || to is null) return;
        var store = new ValueStore(told.Layout.Shared);
        var (here, there) = (store.Open(from), store.Open(to));
        var (pressed, other) = (Download(from)!, Download(to)!);
        var set = Portable(pressed);
        report.Check("values of the program: the set \"Portable\" turns the flag over", set is not null && Presets.HasState(set));
        if (set is null) return;
        Presets.Press(set, here, _ => Array.Empty<Option>());
        report.Check($"values of the program: \"Portable\" on {from.Section?.Id}/{from.Id} sends the flag from that page",
            Sent(here, pressed, "vendor_portable_only") == "true" && Presets.IsActive(set, here), Sent(here, pressed, "vendor_portable_only"));
        report.Check($"values of the program: {to.Section?.Id}/{to.Id} still sends vendor_portable_only: false",
            Sent(there, other, "vendor_portable_only") == "false", Sent(there, other, "vendor_portable_only"));
        var shared = told.Layout.Shared.Where(id => pressed.Fields.Any(field => field.Id == id && field.Kind == "checkbox")).ToList();
        report.Check("values of the program: the layout names shared fields, and the form has them", shared.Count > 0, string.Join(", ", told.Layout.Shared));
        foreach (var id in shared)
        {
            var field = pressed.Fields.First(one => one.Id == id);
            var was = here.Get(field);
            here.Set(field, was == "true" ? "false" : "true");
            report.Check($"values of the program: {id} set on one page is the value of the other", there.Get(id) == here.Get(id) && there.Get(id) != was, $"here {here.Get(id)}, there {there.Get(id)}");
        }
    }

    /// <summary>
    /// "Install ADK" and "Export drivers" stand in the card of the downloads and read none of its
    /// lists: with no version ticked they must run, and the download itself must not (the audit
    /// of 10.10.2026, finding 9).
    /// </summary>
    static void ListsAnOperationNeeds(Report report)
    {
        if (_told is not { } told) { report.Check("lists an operation needs: the door described the program", false, "no description"); return; }
        var store = new ValueStore(told.Layout.Shared);
        foreach (var (id, held) in new[] { ("vendor_uup_adk", false), ("export_selected_drivers", false), ("vendor_download", true) })
        {
            var node = told.Layout.Sections.SelectMany(section => section.Pages).SelectMany(page => page.Operations)
                .FirstOrDefault(one => one.Id == id && one.Fields.Any(field => Values.IsList(field) && field.MinSelected > 0));
            if (node?.Page is not { } page)
            {
                report.Check($"lists an operation needs: {id} stands in a form with a list that asks for ticks", false, "no such place");
                continue;
            }
            var few = store.Open(page).TooFew(node, _ => true);
            report.Check($"lists an operation needs: with nothing ticked {id} on {page.Section?.Id}/{page.Id} is {(held ? "held back" : "not held back")}",
                held == (few is not null) && node.ValidatesFields == held, $"validates {node.ValidatesFields}, short list {few?.Id ?? "-"}");
        }
    }

    /// <summary>
    /// The lists of a form with their terms: a run that ended well leaves the catalogue of a vendor
    /// (an hour) in the memory of the window and drops what WinGet said of the machine (20 s); a
    /// list is good for its term and no longer (the audit of 10.10.2026, finding 13).
    /// </summary>
    static void ListsKept(Report report)
    {
        var clock = new DateTime(2026, 10, 10, 12, 0, 0, DateTimeKind.Utc);
        var lists = new ListMemory { Now = () => clock };
        void Fill()
        {
            lists.Clear();
            lists.Put("versions of a vendor", new List<Option>(), "", 3600);
            lists.Put("drivers of the machine", new List<Option>(), "", 300);
            lists.Put("installed packages", new List<Option>(), "", 20);
            lists.Put("a list without a term", new List<Option>(), "", 0);
        }
        string Kept() => string.Join(", ", new[] { "versions of a vendor", "drivers of the machine", "installed packages", "a list without a term" }.Where(key => lists.TryGet(key, out _)));

        Fill();
        bool dropped = lists.DropAfterRun();
        report.Check("lists kept: after a run that ended well the list of an hour stays, the lists of 20 s and 300 s and the one without a term go",
            dropped && Kept() == "versions of a vendor", Kept());
        report.Check("lists kept: a second run finds nothing more to drop", !lists.DropAfterRun() && lists.Count == 1);

        Fill();
        bool early = lists.DropExpired();
        clock = clock.AddSeconds(21);
        bool first = lists.DropExpired();
        string afterTwenty = Kept();
        clock = clock.AddSeconds(300);
        lists.DropExpired();
        string afterFive = Kept();
        clock = clock.AddSeconds(3600);
        lists.DropExpired();
        report.Check("lists kept: a list is good for its term and no longer; one without a term does not age",
            !early && first && afterTwenty == "versions of a vendor, drivers of the machine, a list without a term"
            && afterFive == "versions of a vendor, a list without a term" && Kept() == "a list without a term", $"{afterTwenty} | {afterFive} | {Kept()}");

        if (_told is not { } told) return;
        var terms = told.Operations().SelectMany(node => node.Fields).Where(field => field.Dynamic).Select(field => field.CacheSeconds).Distinct().OrderBy(term => term).ToList();
        report.Check("lists kept: the terms of the manifest are read - some under an hour, some an hour",
            terms.Any(term => term > 0 && term < ListMemory.LongLived) && terms.Any(term => term >= ListMemory.LongLived), string.Join(", ", terms));
    }

    /// <summary>
    /// The search of a list with headings: a card is in sight when its name and its value hold
    /// every word typed; a heading while a card under it is; a line of explanation always.
    /// </summary>
    static void SearchOfALists(Report report)
    {
        static Option Heading(string name, bool note = false) => new() { Label = name, Header = true, Note = note };
        var list = new List<Option>
        {
            Heading("What the list is, in a line", note: true),
            Heading("Game Ready"),
            Card("https://vendor.example/617.42-desktop-whql.exe"),
            Card("https://vendor.example/617.14-desktop-beta.exe"),
            Heading("Studio"),
            Card("https://vendor.example/617.42-studio-whql.exe"),
            Heading("Notebook"),
            Card("https://vendor.example/600.10-notebook-beta.exe"),
            Heading("Packages"),
            Card("7zip.7zip"),
        };
        list[9].Label = "The archiver";
        list[2].Label = "617.42 - 22 Sep 2026 - Game Ready - latest";
        list[3].Label = "617.14 - 8 Sep 2026 - Game Ready - beta";
        list[3].LabelRu = "617.14 - 8 сентября 2026 - Game Ready - бета";
        list[5].Label = "617.42 - 22 Sep 2026 - Studio";
        list[7].Label = "600.10 - 1 Feb 2026 - Notebook";
        string Seen(string query) => string.Concat(Search.Shown(list, query).Select(shown => shown ? '1' : '0'));

        report.Check("search: with nothing typed every card and every heading is in sight", Seen("") == "1111111111" && Seen("   ") == "1111111111", Seen(""));
        report.Check("search: a word of the name, whatever its case; a heading with no card left is hidden", Seen("STUDIO") == "1000110000", Seen("STUDIO"));
        report.Check("search: the id of a package is searched, though the card does not show it", Seen("7zip") == "1000000011", Seen("7zip"));
        report.Check("search: the address of a file is not - \"whql\" and \"exe\" stand in every one of them", Seen("whql") == "1000000000" && Seen("exe") == "1000000000", $"{Seen("whql")} {Seen("exe")}");
        report.Check("search: every word typed must be there, in any order", Seen("ready 617.42") == "1110000000" && Seen("617.42 ready") == "1110000000" && Seen("617.42") == "1110110000", $"{Seen("ready 617.42")} {Seen("617.42")}");
        report.Check("search: the name in the other language is searched too", Seen("бета") == "1101000000", Seen("бета"));
        report.Check("search: a word nothing holds leaves the line of explanation alone", Seen("no-such-word") == "1000000000", Seen("no-such-word"));
        var fields = _told?.Operations().SelectMany(node => node.Fields).Where(field => field.Filter).Select(field => field.Id).Distinct().OrderBy(id => id).ToList();
        if (fields is not null)
            report.Check("search: the manifest names the lists that have a search of their own", fields.Count > 0 && fields.All(id => _told!.Operations().SelectMany(node => node.Fields).Any(field => field.Id == id && Values.IsList(field))), string.Join(", ", fields));
        var pages = _told?.Layout.Sections.SelectMany(section => section.Pages).Where(page => page.Filter).Select(ValueStore.KeyOf).ToList();
        if (pages is not null) report.Check("search: the layout names the pages that have one search for all their lists", pages.Count > 0, string.Join(", ", pages));
    }

    /// <summary>
    /// The small buttons of a version card: which files a card offers, and what a press sends the
    /// door - the one id in the list of versions, the ticks of the form untouched.
    /// </summary>
    static void VersionCards(Report report)
    {
        var naming = new[] { Card("i1", "i1", "p1"), Card("i3"), Card("p4", "", "p4") };
        var plain = new[] { Card("d1"), Card("z1") };
        (plain[0].Label, plain[1].Label) = ("617.42 - Game Ready", "2.71 - standard version - portable 11.9 MB");
        string Files(Option option, bool names) => VersionCard.Files(option, names) is var (installer, portable) ? $"{installer}|{portable}" : "";
        report.Check("version card: a list that names the files of its versions gives a card its installer and its portable build",
            VersionCard.NamesFiles(naming) && Files(naming[0], true) == "i1|p1" && Files(naming[1], true) == "i3|" && Files(naming[2], true) == "|p4",
            string.Join(" ", naming.Select(option => Files(option, true))));
        report.Check("version card: a list that does not gives one arrow - the portable one when the card says it is a portable build",
            !VersionCard.NamesFiles(plain) && Files(plain[0], false) == "d1|" && Files(plain[1], false) == "|z1", string.Join(" ", plain.Select(option => Files(option, false))));

        var told = TwoPages();
        var page = told.Layout.Sections[0].Pages[0];
        var a = new ValueStore(told.Layout.Shared).Open(page);
        var node = page.Operations[0];
        var versions = node.Fields.First(field => field.Id == "versions");
        a.Set(versions, "i1\ni3");
        report.Check("version card: a press sends the door the one id, with the other values of the form",
            Sent(a, node, "versions") != "[\"p1\"]" && JsonDocument.Parse(a.ForDoor(node, "versions", "p1")).RootElement.GetProperty("versions").GetRawText().Replace(" ", "").Replace("\n", "").Replace("\r", "") == "[\"p1\"]"
            && JsonDocument.Parse(a.ForDoor(node, "versions", "p1")).RootElement.GetProperty("vendor").GetString() == "one",
            a.ForDoor(node, "versions", "p1"));
        report.Check("version card: the line a person reads names the one id", a.ChangedOf(node, "versions", "p1").GetValueOrDefault("versions") == "p1" && a.ChangedOf(node).GetValueOrDefault("versions") == "i1,i3");
        report.Check("version card: what is ticked in the list stays ticked", a.Get(versions) == "i1\ni3", a.Get(versions).Replace('\n', ','));

        if (_told is not { } program) return;
        // The program itself: wherever a list of versions is in sight as a page opens, the page has both operations to run.
        // (The form of the downloads stands on DRIVERS / THIS MACHINE too, for "Export drivers" - with its versions hidden.)
        var store = new ValueStore(program.Layout.Shared);
        var carrying = new List<Page>();
        foreach (var one in program.Layout.Sections.SelectMany(section => section.Pages))
        {
            var values = store.Open(one);
            bool InSight(Field field) => !field.Hidden && !FieldKinds.Matches(field.HiddenWhen, values.Get)
                && (field.VisibleWhen is not { Count: > 0 } || FieldKinds.Matches(field.VisibleWhen, values.Get));
            if (one.Operations.Any(place => place.Fields.Any(field => field.CardActions == "vendor" && InSight(field)))) carrying.Add(one);
        }
        var missing = carrying.Where(one => !one.Operations.Any(place => place.Id == VersionCard.Download && place.Runs) || !one.Operations.Any(place => place.Id == VersionCard.Link && place.Runs))
            .Select(ValueStore.KeyOf).ToList();
        report.Check($"version card: each of the {carrying.Count} pages that show a list of versions has the download and the link to run", carrying.Count > 0 && missing.Count == 0, string.Join(", ", missing));
    }

    /// <summary>
    /// The value an operation asks for before it runs - the GitHub token: it is named in the call
    /// and in the line a person reads, and written in neither; the command gets it as one line of
    /// its standard input (the audit of 10.10.2026, finding 6).
    /// </summary>
    static void ValueAskedFor(Report report)
    {
        const string mark = "SELF-TEST-VALUE-5e1c";
        var launch = Door.Run("github_token", "", "", "github_token", mark);
        var line = Door.RunLine("github_token", "", "", new Dictionary<string, string>(), "github_token");
        bool named = Enumerable.Range(0, launch.Args.Count - 1).Any(i => launch.Args[i] == "--secret-stdin" && launch.Args[i + 1] == "github_token");
        report.Check("value asked for: the call names the value and gives it by the standard input alone",
            named && launch.Input == mark && !launch.Args.Any(arg => arg.Contains(mark)) && !launch.CommandLine.Contains(mark), string.Join(" ", launch.Args.Skip(5)));
        report.Check("value asked for: the line a person reads names it and does not hold it", line.EndsWith("--secret-stdin github_token") && !line.Contains(mark), line);
        report.Check("value asked for: an empty value is a value, and a run that asks for none is given nothing to read",
            Door.Run("github_token", "", "", "github_token", "").Input == "" && Door.Run("health_doctor", "", "").Input is null);

        if (_told is not { } told) return;
        var asking = told.Operations().Where(node => node.Prompt.Length > 0).ToList();
        var wordless = asking.Where(node => node.Asks is not { Title.Length: > 0, Label.Length: > 0, Ok.Length: > 0 }).Select(node => node.Id).ToList();
        report.Check($"value asked for: each of the {asking.Count} operations that ask has the words of its dialog, and a secret is marked",
            asking.Count > 0 && wordless.Count == 0 && asking.Any(node => node.Asks.Secret), string.Join(", ", wordless));
        var folders = told.Layout.Folders;
        report.Check("value asked for: nothing waits for a place - the token stands in the header, the clearing beside the downloads",
            told.Layout.Service.Any(node => node.Prompt.Length > 0) && folders.Count > 0 && folders.All(node => node.Dangerous && node.Runs), string.Join(", ", folders.Select(node => node.Id)));
    }

    // ------------------------------------------------------------ the line a run is written with

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CommandLineToArgvW(string line, out int count);

    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr memory);

    /// <summary>A line as Windows hands it to a program, word by word.</summary>
    static List<string> Words(string line)
    {
        var words = new List<string>();
        var raw = CommandLineToArgvW(line, out int count);
        if (raw == IntPtr.Zero) return words;
        try
        {
            for (int i = 0; i < count; i++) words.Add(Marshal.PtrToStringUni(Marshal.ReadIntPtr(raw, i * IntPtr.Size)) ?? "");
        }
        finally { LocalFree(raw); }
        return words;
    }

    static void RunLine(Report report)
    {
        // What a value may hold that a line has rules for: a space, a quote, a backslash at its end.
        var values = new Dictionary<string, string>
        {
            ["plain"] = "word",
            ["space"] = "two words",
            ["quote"] = "say \"yes\" twice",
            ["slash_end"] = @"C:\Some Folder\",
            ["slash_quote"] = "a\\\"b",
            ["slashes_end"] = @"name\\",
            ["empty"] = "",
            ["signs"] = "a&b|c<d>e^f",
        };
        var target = @"D:\Down Loads\";
        var source = @"E:\Some Source\";
        var line = Door.RunLine("vendor_download", source, target, values);
        var expected = new List<string> { Paths.DoorName, "run", "--operation", "vendor_download", "--source", Paths.Short(source), "--target", Paths.Short(target) };
        foreach (var (name, value) in values) { expected.Add("--set"); expected.Add(name + "=" + value); }
        var read = Words(line);
        var wrong = Enumerable.Range(0, Math.Max(expected.Count, read.Count))
            .Where(i => i >= expected.Count || i >= read.Count || expected[i] != read[i])
            .Select(i => $"word {i}: wanted <{(i < expected.Count ? expected[i] : "")}> read <{(i < read.Count ? read[i] : "")}>").ToList();
        report.Check("run line: read back word for word - a space, a quote, a backslash at the end of a value", wrong.Count == 0, string.Join("; ", wrong) + " | " + line);
    }

    /// <summary>
    /// A page that reads a folder (`source` of the layout) names it to the door at every run; a
    /// page that reads none names none, whatever folder the window holds (the audit of
    /// 10.10.2026, finding 3: the window never passed --source).
    /// </summary>
    static void SourceOfARun(Report report)
    {
        if (_told is not { } told) { report.Check("source: the door described the program", false, "no description"); return; }
        var pages = told.Layout.Sections.SelectMany(section => section.Pages).Where(page => page.Operations.Any(node => node.Runs)).ToList();
        var reading = pages.Where(page => page.Source).ToList();
        report.Check("source: the layout names pages that read a folder, and pages that read none", reading.Count > 0 && reading.Count < pages.Count,
            string.Join(", ", reading.Select(ValueStore.KeyOf)));
        const string folder = @"D:\Chosen Source", target = @"E:\Downloads";
        static bool Names(IReadOnlyList<string> words, string key, string value) =>
            Enumerable.Range(0, words.Count - 1).Any(i => words[i] == key && words[i + 1] == value);
        foreach (var page in reading)
        {
            var node = page.Operations.First(one => one.Runs);
            var source = Door.SourceOf(page, folder);
            var launch = Door.Run(node.Id, source, target);
            var line = Words(Door.RunLine(node.Id, source, target, new Dictionary<string, string>()));
            report.Check($"source: a run of {ValueStore.KeyOf(page)} names the source to the door, and in the line a person reads",
                Names(launch.Args, "--source", folder) && Names(launch.Args, "--target", target) && Names(line, "--source", folder),
                string.Join(" ", launch.Args.Skip(5).Take(8)));
        }
        if (pages.FirstOrDefault(page => !page.Source) is { } plain)
        {
            var launch = Door.Run(plain.Operations.First(one => one.Runs).Id, Door.SourceOf(plain, folder), target);
            report.Check($"source: a run of {ValueStore.KeyOf(plain)}, a page that reads no folder, names none", !launch.Args.Contains("--source") && Names(launch.Args, "--target", target),
                string.Join(" ", launch.Args.Skip(5).Take(8)));
        }
    }

    // ------------------------------------------------------------ the lines of the error stream

    static void ErrorStream(Report report)
    {
        var read = new[] { "a plain word", "[ERR] named an error by its writer", "", "[ERROR] another word of the writer" }.Select(Runner.ErrorLine).ToArray();
        var expected = new[] { "[ERR] a plain word", "[ERR] named an error by its writer", "[ERR] ", "[ERR] [ERROR] another word of the writer" };
        report.Check("error stream: a line is marked [ERR] once", read.SequenceEqual(expected), string.Join(" ~ ", read));
    }

    // ------------------------------------------------------------ the colours of the terminal

    static void Colours(Report report)
    {
        static string Piece((string Text, Hue Hue) part) =>
            part.Text + "|" + (part.Hue.Own is { } own ? $"#{own.R:x2}{own.G:x2}{own.B:x2}" + (own.A < 255 ? "/" + own.A : "") : part.Hue.Key)
            + (part.Hue.Bold ? "|bold" : "") + (part.Hue.Underline ? "|underline" : "");

        var expected = new[]
        {
            new[] { "[WARN] yellow by its writer|#eab308", " and plain after it|LogPlain" },
            new[]
            {
                "Vivaldi|#22d3ee", " [|LogPlain", "Vivaldi.Vivaldi|#06b6d4", "] |LogPlain", "bold|LogPlain|bold", " |LogPlain", "dim|LogMuted", " |LogPlain",
                "underlined|LogPlain|underline", " |LogPlain", "the 208th|#ff8700", " |LogPlain", "24-bit|#87d75f",
            },
            new[] { "red |#ef4444", "green |#22c55e", "blue |#3b82f6", "magenta |#d946ef", "grey |#6b7280", "white|#ffffff" },
            new[] { "still white: the colour was left open|#ffffff" },
            new[] { "[OK] a line nobody coloured|LogPlain" },
        };
        var plain = new[]
        {
            "[WARN] yellow by its writer and plain after it",
            "Vivaldi [Vivaldi.Vivaldi] bold dim underlined the 208th 24-bit",
            "red green blue magenta grey white",
            "still white: the colour was left open",
            "[OK] a line nobody coloured",
        };
        var ansi = new Ansi();
        for (int i = 0; i < ColouredLines.Length; i++)
        {
            var parts = ansi.Read(ColouredLines[i], out var text).Select(Piece).ToArray();
            report.Check($"colours: line {i + 1} is read into its pieces", parts.SequenceEqual(expected[i]), string.Join(" ~ ", parts));
            report.Check($"colours: line {i + 1} as text alone", text == plain[i], text);
        }
        // A colour left open is over with the run: the next run starts plain.
        ansi.Read("\u001b[31mopen", out _);
        ansi.Reset();
        var after = ansi.Read("next run", out _).Select(Piece).ToArray();
        report.Check("colours: a colour left open does not outlive Reset", after.SequenceEqual(new[] { "next run|LogPlain" }), string.Join(" ~ ", after));
        // A dimmed colour is the same colour, fainter.
        var dim = new Ansi().Read("\u001b[2;31mfaint\u001b[0m", out _).Select(Piece).ToArray();
        report.Check("colours: a dimmed colour keeps its tone", dim.SequenceEqual(new[] { "faint|#ef4444/184" }), string.Join(" ~ ", dim));
    }
}
