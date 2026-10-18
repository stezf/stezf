#!/usr/bin/env python3
"""Builds assets/projects.svg from the repo data GitHub publishes.

Why a script instead of a browser-side fetch: the README is served as sanitized
HTML (<script> tags get escaped and style= is stripped, verified with the API's
POST /markdown), and an SVG loaded via <img> renders in restricted mode, with no
scripts and no external resources. Nothing can query the API from the profile, so
the data is fetched here and baked into the SVG.

Usage:
    python3 scripts/gen_projects.py            # query the API and write the SVG
    python3 scripts/gen_projects.py --dry-run  # query and report only

To change which repos are listed, edit the REPOS constant below.

Environment variables:
    GITHUB_TOKEN  optional token, raises the API rate limit (60 requests/hour
                  unauthenticated). The workflow passes its own.
    REPO_OWNER    repo owner, defaults to stezf.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# --- which repos are listed, and in what order ------------------------------
# Only three fields are painted: name, description and language.
REPOS = ["GlaceShell", "RobloxVerify", "BingR-Bot"]

# --- paths ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE_PATH = os.path.join(HERE, "projects.template.svg")
METRICS_PATH = os.path.join(HERE, "text-metrics.json")
DESTINATION = os.path.join(ROOT, "assets", "projects.svg")

# --- card design (viewBox units, see the template) --------------------------
MARGIN = 40
GUTTER = 40
CARD_HEIGHT = 236
ROW_GAP = 34
PADDING = 36              # inner padding
NAME_SIZE = 46
DESCRIPTION_SIZE = 29
LANGUAGE_SIZE = 27
DESCRIPTION_LINE = 40
NAME_BASELINE = 62
DESCRIPTION_BASELINE = 112
LANGUAGE_BASELINE = 202
CORNER_RADIUS = 16
BOTTOM_PADDING = 20   # aire low la ultima row
DOT_RADIUS = 9
DESCRIPTION_LINES = 2
# With an odd number of repos the last row has a gap: True makes the last card
# span both columns and cover it.
LAST_CARD_SPANS_BOTH = False

# Language colors from github-linguist (lib/linguist/languages.yml). Add new
# languages here; unknown ones fall back to the brand blue.
LANGUAGE_COLORS = {
    "QML": "#44a51c",
    "Shell": "#89e051",
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "Go": "#00ADD8",
    "Lua": "#000080",
    "Rust": "#dea584",
    "C": "#555555",
    "C++": "#f34b7d",
}
BRAND_COLOR = "#568eff"

# Liberation Sans (Arial metrics) is narrower than Segoe UI but wider than other
# substitutes; this margin covers the difference so text never overflows.
SAFETY_MARGIN = 1.06


class Metrics:
    """Measures strings with the width table in scripts/text-metrics.json."""

    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as fh:
            data = json.load(fh)
        self.regular = data["regular"]
        self.negrita = data["negrita"]
        self.target = 0.0

    def width(self, text, body, negrita=False):
        table = self.negrita if negrita else self.regular
        total = 0.0
        for ch in text:
            total += table.get(ch, table.get("n", 500))
        return total / 1000.0 * body * SAFETY_MARGIN

    def ajusta(self, text, body, negrita=False):
        """Truncates with an ellipsis until it fits the target width."""
        target = int(self.target)
        if self.width(text, body, negrita) <= target:
            return text
        points = self.width("...", body, negrita)
        # Invariant: 'low' is a prefix that DOES fit with the dots, 'high' one
        # that does not. The +1 here is deliberate: without it the previous
        # version overshot by one glyph (measured 620.2 vs a 618 target).
        low, high = 0, len(text)
        while low + 1 < high:
            mid = (low + high) // 2
            if self.width(text[:mid], body, negrita) + points <= target:
                low = mid
            else:
                high = mid
        return text[:low].rstrip(" ,;:.-") + "..."

    def _cabe(self, palabras, limite):
        """Greedily wraps words into lines of width `limite`, without balancing."""
        lines = []
        actual = ""
        for palabra in palabras:
            tentativa = palabra if not actual else actual + " " + palabra
            if not actual or self.width(tentativa, self.body) <= limite:
                actual = tentativa
            else:
                lines.append(actual)
                actual = palabra
        if actual:
            lines.append(actual)
        return lines

    def envuelve(self, text, body, lines):
        """Wraps text into `lines` lines, widest first.

        The wrap is BALANCED, and that matters: a greedy wrap fills the first
        line and dumps the rest in the last one, so a description that fits in
        two lines ends up ellipsized. So we look for the MINIMUM width that
        still fits the text in `lines` lines.

        The search is over INTEGERS and without the +1, both tested: in floats
        it stalls (once low and high meet, (low+high)/2 rounds down to low, the
        step stops shrinking the interval and the loop never ends), and with the
        +1 a failed test jumps past the answer and truncates text that fits.
        """
        self.body = body
        palabras = text.split()
        if not palabras:
            return [""] * lines

        target = int(self.target)
        full_width = self.width(text, body)

        if full_width <= target:
            return [text] + [""] * (lines - 1)

        # A single word wider than the card cannot be wrapped; truncate it.
        if max(self.width(p, body) for p in palabras) > target:
            return [self.ajusta(palabras[0], body)] + [""] * (lines - 1)

        # Invariant: 'high' is always a width the text DOES fit in 'lines'
        # lines, and the search is BOUNDED by the card width (an unbounded
        # minimum returned 874 for a usable width of 618).
        if len(self._cabe(palabras, target)) > lines:
            # Does not fit even at full card width: greedy-wrap it and ellipsize
            # the last line, which keeps as much text as possible.
            output = self._cabe(palabras, target)
            last_lines = output[: lines - 1]
            last_lines.append(self.ajusta(" ".join(output[lines - 1:]), body))
            return last_lines + [""] * (lines - len(last_lines))

        # Otherwise take the MINIMUM width that fits: the most even wrap.
        low, high = 0, target
        while low + 1 < high:
            mid = (low + high) // 2
            if len(self._cabe(palabras, mid)) <= lines:
                high = mid
            else:
                low = mid

        output = self._cabe(palabras, high)
        return output + [""] * (lines - len(output))


def escape(text):
    """Escapes text so it can be embedded in XML."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def fetch_repos(owner):
    """Fetches one object per listed repo from the GitHub API."""
    cabeceras = {
        "User-Agent": "stezf-profile-readme",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        cabeceras["Authorization"] = "Bearer " + token

    output = []
    for name in REPOS:
        url = "https://api.github.com/repos/%s/%s" % (owner, name)
        peticion = urllib.request.Request(url, headers=cabeceras)
        try:
            with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                crudo = json.load(respuesta)
        except urllib.error.HTTPError as err:
            if err.code == 401:
                motivo = ("401 = the GITHUB_TOKEN is not valid (expired, or it "
                          "belongs to another account)")
            elif err.code in (403, 429):
                motivo = ("%d = rate limit reached. Without a token it is 60 per "
                          "hour per IP; wait a while or export a "
                          "GITHUB_TOKEN" % err.code)
            elif err.code == 404:
                motivo = ("404 = the repo does not exist, it is private, or the "
                          "name in REPOS is misspelled")
            else:
                motivo = "%d = the API replied with an error" % err.code
            raise SystemExit(
                "error: the API returned %s for %s/%s\n"
                "       %s\n"
                "       assets/projects.svg is left untouched, so the section does "
                "not end up empty." % (err.code, owner, name, motivo))
        except urllib.error.URLError as err:
            raise SystemExit(
                "error: could not reach the GitHub API (%s).\n"
                "       assets/projects.svg is left untouched, so the section does\n"
                "       not end up empty." % err.reason)
        output.append({
            "name": crudo.get("name") or name,
            "description": (crudo.get("description") or "").strip(),
            "language": crudo.get("language") or "",
        })
        print("  %-14s %-10s %s" % (
            output[-1]["name"], output[-1]["language"] or "-",
            (output[-1]["description"][:54] or "(sin description)")))
    return output


def card(dato, x, y, width, metrics):
    """One card: name, two-line description and language with its dot."""
    metrics.target = width - 2 * PADDING

    lines = metrics.envuelve(
        dato["description"] or "Sin description.", DESCRIPTION_SIZE,
        DESCRIPTION_LINES)
    name = metrics.ajusta(dato["name"], NAME_SIZE, negrita=True)
    language = dato["language"]
    color = LANGUAGE_COLORS.get(language, BRAND_COLOR)
    x_text = x + PADDING

    parts = ["    <g>"]
    parts.append(
        '      <rect class="card" x="%d" y="%d" width="%d" height="%d" rx="%d"'
        ' filter="url(#card-shadow)"/>'
        % (x, y, width, CARD_HEIGHT, CORNER_RADIUS))
    parts.append('      <text class="name" x="%d" y="%d">%s</text>'
                  % (x_text, y + NAME_BASELINE, escape(name)))
    for index, line in enumerate(lines):
        if not line:
            continue
        parts.append('      <text class="desc" x="%d" y="%d">%s</text>'
                      % (x_text, y + DESCRIPTION_BASELINE + index * DESCRIPTION_LINE,
                         escape(line)))
    if language:
        parts.append('      <circle cx="%d" cy="%d" r="%d" fill="%s"/>'
                      % (x_text + DOT_RADIUS, y + LANGUAGE_BASELINE - 9,
                         DOT_RADIUS, color))
        parts.append('      <text class="lang" x="%d" y="%d">%s</text>'
                      % (x_text + 2 * DOT_RADIUS + 14, y + LANGUAGE_BASELINE,
                         escape(metrics.ajusta(language, LANGUAGE_SIZE))))
    parts.append("    </g>")
    return "\n".join(parts)


def render(data, metrics, generado):
    total = len(data)
    column_width = (1500 - 2 * MARGIN - GUTTER) // 2
    full_width = 1500 - 2 * MARGIN
    odd = total % 2 == 1 and LAST_CARD_SPANS_BOTH
    pitch = CARD_HEIGHT + ROW_GAP

    cards = []
    boxes = []
    row = 0
    for index, dato in enumerate(data):
        if odd and index == total - 1:
            # Odd repo count: the last card spans both columns instead of leaving a gap.
            # It always starts a new row, so the row is counted first.
            if index > 0:
                row += 1
            boxes.append((MARGIN, row * pitch, full_width))
            cards.append(card(dato, MARGIN, row * pitch, full_width, metrics))
            continue
        if index % 2 == 0 and index > 0:
            row += 1
        x = MARGIN + (index % 2) * (column_width + GUTTER)
        boxes.append((x, row * pitch, column_width))
        cards.append(card(dato, x, row * pitch, column_width, metrics))

    # Overlap check: a card in the wrong row draws on top of another one and the SVG
    # is still valid XML, so the bug would only show up when rendering.
    for i, (x1, y1, w1) in enumerate(boxes):
        for x2, y2, w2 in boxes[i + 1:]:
            solapan = (x1 < x2 + w2 and x2 < x1 + w1
                       and y1 < y2 + CARD_HEIGHT and y2 < y1 + CARD_HEIGHT)
            if solapan:
                raise SystemExit(
                    "error: cards %d and %d overlap "
                    "(%d,%d %dx%d and %d,%d %dx%d)"
                    % (i + 1, i + 2, x1, y1, w1, CARD_HEIGHT, x2, y2, w2, CARD_HEIGHT))

    # The 20 units of air at the bottom are not an oversight: with them the
    # canvas is 526 instead of 506, so the cards do not sit flush against the
    # edge.
    high = row * pitch + CARD_HEIGHT + BOTTOM_PADDING

    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        template = fh.read()

    # str.replace hits every occurrence, and a marker can be quoted in a comment:
    # the body would then be written inside the comment too. Check before writing.
    for marker in ("{{CARDS}}", "{{HEIGHT}}", "{{GENERATED}}"):
        occurrences = template.count(marker)
        if occurrences != 1:
            raise SystemExit(
                "error: placeholder %s appears %d times in %s, it should appear once.\n"
                "       most likely a comment quotes the literal token."
                % (marker, occurrences, os.path.relpath(TEMPLATE_PATH, ROOT)))

    body = "\n".join(cards)
    output = (template
              .replace("{{CARDS}}", body)
              .replace("{{HEIGHT}}", str(high))
              .replace("{{GENERATED}}", generado))
    return output, high


def main():
    dry = "--dry-run" in sys.argv
    owner = os.environ.get("REPO_OWNER") or "stezf"
    metrics = Metrics(METRICS_PATH)

    print("querying the GitHub API (%s):" % owner)
    data = fetch_repos(owner)
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output, high = render(data, metrics, generado)

    # Validate before writing: if the XML does not parse, the good file stays.
    import xml.dom.minidom
    xml.dom.minidom.parseString(output)

    if dry:
        print("\n--dry-run: the SVG is valid (%d cards, canvas 1500x%d),"
              " nothing written" % (len(data), high))
        return

    with open(DESTINATION, "w", encoding="utf-8") as fh:
        fh.write(output)
    print("\nwrote %s (%d cards, canvas 1500x%d, %d bytes)"
          % (os.path.relpath(DESTINATION, ROOT), len(data), high,
             len(output.encode())))


if __name__ == "__main__":
    main()
