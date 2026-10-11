"""Builds Source/Icons.cs from the owner's icon set - no icon of the window is drawn by hand.

    E:\\TOOLS\\Lucide-Icons    signs of actions (ISC): strokes on a 24 grid

Run:  py -3.12 Source\\Tools\\build_icons.py [-Sets E:\\TOOLS]
Add a name to LUCIDE below and run it again; Icons.cs is never edited by hand.
The tool of Audion Build Publisher (Source\\Tools\\build_icons.py there), with this window's list.
"""
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

SETS = pathlib.Path(sys.argv[sys.argv.index('-Sets') + 1]) if '-Sets' in sys.argv else pathlib.Path('E:/TOOLS')
OUT = pathlib.Path(__file__).resolve().parents[1] / 'Icons.cs'

# What the window shows. The key is the name in the set: a sign is asked for by it everywhere.
LUCIDE = [
    # the sections: packages, Windows, the system, hardware, drivers, media
    'package', 'app-window', 'wrench', 'cpu', 'circuit-board', 'clapperboard',
    # the pages of the sections
    'layout-grid', 'refresh-cw', 'hard-drive', 'search', 'sparkles', 'arrow-left-right', 'disc', 'key-round',
    'layers', 'terminal', 'activity', 'gauge', 'monitor', 'laptop', 'palette',
    'usb', 'flask-conical', 'gamepad-2',
    # what an operation does: check, install, update, list, everything, remove, pin, a text, out, in, a page, run
    'scan-search', 'package-plus', 'list', 'check-check', 'trash-2', 'pin', 'file-text', 'upload', 'download',
    'external-link', 'play',
    # the service of the header
    'shield-check', 'eraser',
    # a card: its field of ticks - all, none; back to the defaults; a set of values
    'list-x', 'undo-2', 'wand-sparkles', 'star',
    # the downloads folder, the terminal, the look, a run and its stop
    'folder', 'folder-open', 'copy', 'maximize-2', 'minimize-2', 'sun', 'square', 'info',
    # a sheet over the sections (SETTINGS, ABOUT): its way out
    'x',
    # a package card: its installer, its archive or portable build, its page
    'file-down', 'archive',
    # a field that opens its list under itself
    'chevron-down',
    # the pages of DRIVERS by the kind of device
    'gpu', 'microchip', 'wifi', 'bluetooth', 'ethernet-port', 'audio-lines',
]
SVG = '{http://www.w3.org/2000/svg}'


def number(value):
    text = ('%.4f' % float(value)).rstrip('0').rstrip('.')
    return text if text not in ('-0', '') else '0'


NUMBER = r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'


def absolute_start(d):
    """The first "m" of a path is absolute whatever its case - but only inside its own path.

    Lucide starts paths with a small "m", and several paths are joined into one geometry here:
    the second "m" would then be counted from the end of the first drawing. It becomes "M", and
    the pairs that followed it - implicit relative lines - get their own "l".
    """
    hit = re.match(r'\s*m\s*(%s)[\s,]*(%s)\s*,?\s*' % (NUMBER, NUMBER), d)
    if not hit:
        return d
    rest = d[hit.end():]
    lead = 'l' if rest and (rest[0].isdigit() or rest[0] in '-.') else ''
    return 'M%s %s%s%s' % (hit.group(1), hit.group(2), lead, rest)


def shape_to_path(element):
    """One SVG primitive as path data, so a sign is a single geometry."""
    tag = element.tag.replace(SVG, '')
    get = lambda name, default=0.0: float(element.get(name, default))
    if tag == 'path':
        return absolute_start(element.get('d', '').strip())
    if tag == 'line':
        return 'M%s %sL%s %s' % tuple(number(get(n)) for n in ('x1', 'y1', 'x2', 'y2'))
    if tag in ('polyline', 'polygon'):
        points = re.findall(r'-?\d*\.?\d+', element.get('points', ''))
        pairs = ['%s %s' % (number(points[i]), number(points[i + 1])) for i in range(0, len(points) - 1, 2)]
        return 'M' + 'L'.join(pairs) + ('Z' if tag == 'polygon' else '')
    if tag in ('circle', 'ellipse'):
        cx, cy = get('cx'), get('cy')
        rx = get('r') if tag == 'circle' else get('rx')
        ry = get('r') if tag == 'circle' else get('ry')
        return 'M%s %sa%s %s 0 1 0 %s 0a%s %s 0 1 0 %s 0Z' % (
            number(cx - rx), number(cy), number(rx), number(ry), number(2 * rx), number(rx), number(ry), number(-2 * rx))
    if tag == 'rect':
        x, y, w, h = get('x'), get('y'), get('width'), get('height')
        rx = float(element.get('rx', element.get('ry', 0)))
        ry = float(element.get('ry', element.get('rx', 0)))
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx == 0 and ry == 0:
            return 'M%s %sh%sv%sh%sZ' % (number(x), number(y), number(w), number(h), number(-w))
        return ('M%s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sZ' % (
            number(x + rx), number(y), number(w - 2 * rx), number(rx), number(ry), number(rx), number(ry),
            number(h - 2 * ry), number(rx), number(ry), number(-rx), number(ry),
            number(-(w - 2 * rx)), number(rx), number(ry), number(-rx), number(-ry),
            number(-(h - 2 * ry)), number(rx), number(ry), number(rx), number(-ry)))
    return ''


def paths_of(file):
    root = ET.parse(file).getroot()
    parts = [shape_to_path(e) for e in root.iter() if e.tag.replace(SVG, '') in ('path', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'rect')]
    return ' '.join(p for p in parts if p)


def quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def main():
    lines = [
        '// Generated by Source/Tools/build_icons.py from Lucide (ISC) - do not edit by hand.',
        'using System.Collections.Generic;',
        '',
        'namespace AudionGetTools;',
        '',
        '/// <summary>The signs of the window, by their names in the set they come from. Nothing here is drawn by hand.</summary>',
        'public static partial class Icons',
        '{',
        '    /// <summary>Lucide: strokes on a 24 grid.</summary>',
        '    public static readonly IReadOnlyDictionary<string, string> Lucide = new Dictionary<string, string>',
        '    {',
    ]
    for name in sorted(set(LUCIDE)):
        data = paths_of(SETS / 'Lucide-Icons' / 'package' / 'icons' / (name + '.svg'))
        lines.append('        [%s] = %s,' % (quote(name), quote(data)))
    lines += ['    };', '}', '']
    # The sources of this window end their lines with LF (Tests\Test-Window.ps1).
    OUT.write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    print('Icons.cs: Lucide %d' % len(set(LUCIDE)))


if __name__ == '__main__':
    main()
