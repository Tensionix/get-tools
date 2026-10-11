using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace AudionGetTools.Core;

/// <summary>
/// The small buttons of a version card (`card_actions: vendor` of its field): the installer of the
/// version, its portable build, and "the link alone" - each for that one build, whatever is ticked
/// in the list. The rules are those of the window on NiceGUI (vendor_card_files,
/// vendor_card_actions): a list that names the two files of its versions (`installer_id`,
/// `portable_id` - TechPowerUp, the programs by file) gives every card the same three places, an
/// empty one where a version has no such file; a list that does not gives one arrow, the crimson
/// archive when the card itself is a portable build, the green one otherwise.
/// </summary>
public static class VersionCard
{
    /// <summary>The operations a version card runs: the download of its page, and the one that only tells the link.</summary>
    public const string Download = "vendor_download", Link = "vendor_link";

    static readonly Regex PortableWord = new(@"\b(portable|standalone|zip)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>The list names the files of its versions: a card of it has a place for an installer and a place for a portable build.</summary>
    public static bool NamesFiles(IEnumerable<Option> options) => options.Any(option => !option.Header && option.PortableId.Length > 0);

    /// <summary>The ids of the files a card offers; "" where it has no such file.</summary>
    public static (string Installer, string Portable) Files(Option option, bool namesFiles)
    {
        if (option.PortableId.Length > 0) return (option.InstallerId, option.PortableId);
        if (namesFiles) return (option.Key, "");
        return PortableWord.IsMatch(option.Label) ? ("", option.Key) : (option.Key, "");
    }
}
