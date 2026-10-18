#!/usr/bin/env python3
"""Builds assets/stats.svg: the activity curve and the 6-month matrix.

Data comes from https://github.com/users/stezf/contributions, the internal
endpoint GitHub's own web uses to paint the profile graph. It is not the public
API: it is undocumented and GitHub may change the markup, so parsing is
defensive and any mismatch fails without touching the existing SVG.

Note what it counts: CONTRIBUTIONS (commits, pull requests, issues, reviews),
not just commits. It also only keeps a year, so 12 months is the max window.

Usage:
    python3 scripts/gen_stats.py            # query and write the SVG
    python3 scripts/gen_stats.py --dry-run  # query and report only

Environment variables:
    GITHUB_TOKEN   unused here, accepted for symmetry with gen_projects.py
    STATS_MONTHES  months to show, defaults to 6
    REPO_OWNER     repo owner, defaults to stezf
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE_PATH = os.path.join(HERE, "stats.template.svg")
METRICS_PATH = os.path.join(HERE, "text-metrics.json")
DESTINATION = os.path.join(ROOT, "assets", "stats.svg")

# --- matrix window and size -------------------------------------------------
MESES = 6
PITCH = 44
CELDA = 36
HUECO = PITCH - CELDA
ANCHO_LIENZO = 1500

# --- vertical ---------------------------------------------------------------
BASE_RESUMEN = 40
CURVE_TOP = 92        # y of the curve peak
CURVE_BASE = 268       # y of the curve floor
MONTHS_TOP = 322       # baseline of the month labels
MATRIX_TOP = 340      # y of the first row of cells
CELL_HEIGHT = PITCH     # 7 days per column

# Initials for Monday, Wednesday, Friday and Sunday. The identifier stays named
# WEEKDAY_INITIALS to leave the names alone, but the values are already English:
# M, W, F, S.
WEEKDAY_INITIALS = ["M", "", "W", "", "F", "", "S"]
# Month abbreviations. The identifier stays named MONTHS to leave the names
# alone, but the values are already English. "may" is the same in both
# languages, so from April to October only "abr" becomes "apr" and "ago" becomes
# "aug"; those seven are the months currently on screen.
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec"]

# Color scale by daily contribution count. It is ABSOLUTE, not quartile-based
# like GitHub's: over 6 months counts run 1..18, so quartiles would leave
# almost everything at level 1.
NIVELES = [(1, 2), (3, 5), (6, 9), (10, 10 ** 9)]
COLOR_NIVEL = ["#e8ecf1", "#1a3a6b", "#24509e", "#2f6ad0", "#568eff"]
COLOR_NIVEL_OSCURO = ["#1b2233", "#1b3257", "#21468f", "#2a5cb8", "#568eff"]
COLOR_BORDE = "#3a416a"


class Metrics:
    """Measures text with the width table in scripts/text-metrics.json."""

    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as fh:
            data = json.load(fh)
        self.regular = data["regular"]

    def width(self, text, body, seguro=True):
        """Width of a text in screen units.

        seguro=True adds a 2% margin, used when fitting lines so text does not
        overflow in a viewer with a slightly wider font. To CENTER, measure with
        seguro=False: with the cushion the line is painted shorter than planned.
        """
        total = 0.0
        for ch in text:
            total += self.regular.get(ch, self.regular.get("n", 500))
        return total / 1000.0 * body * (1.02 if seguro else 1.0)


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def fetch_html(owner):
    """Fetches the graph fragment and returns {ISO date: contribution}."""
    url = "https://github.com/users/%s/contributions" % owner
    cabeceras = {
        # Without it GitHub serves a login page instead of the fragment and the
        # parse finds no days at all.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) stezf-profile-readme",
        "Accept": "text/html",
    }
    try:
        peticion = urllib.request.Request(url, headers=cabeceras)
        with urllib.request.urlopen(peticion, timeout=45) as respuesta:
            html = respuesta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        raise SystemExit("error: the GitHub website returned %s" % err.code)
    except urllib.error.URLError as err:
        raise SystemExit("error: could not reach github.com (%s)" % err.reason)

    # Each day: <td ... data-date="YYYY-MM-DD" id="...-f-c" data-level="N" ...>
    days = {}
    for td in re.findall(r"<td\b[^>]*>", html):
        iso_date = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', td)
        level = re.search(r'data-level="(\d)"', td)
        ident = re.search(r'id="(contribution-day-component-[\d-]+)"', td)
        if iso_date and level:
            days[iso_date.group(1)] = {"level": int(level.group(1)),
                                    "id": ident.group(1) if ident else None}

    # The exact count only appears when there is something to count; it is matched
    # to the day by the tool-tip id, the same one the td carries.
    conteos = {}
    for ident, body in re.findall(
            r'<tool-tip\b[^>]*\bfor="([^"]+)"[^>]*>(.*?)</tool-tip>', html, re.S):
        m = re.search(r"(\d+)\s+contribution", body)
        if m:
            conteos[ident] = int(m.group(1))

    for iso, day in days.items():
        day["count"] = conteos.get(day["id"], 0) if day["id"] else 0

    if not days:
        raise SystemExit(
            "error: not a single day was found in the response.\n"
            "       GitHub has most likely changed the markup of the "
            "graph.\n"
            "       assets/stats.svg is left untouched.")
    return days


def check(days, month_count):
    """Fails if the data does not look normal, before anything is written."""
    iso_dates = sorted(days)
    expected_approx = month_count * 30
    if len(iso_dates) < expected_approx:
        raise SystemExit(
            "error: only %d days, at least %d were expected for %d months.\n"
            "       assets/stats.svg is left untouched."
            % (len(iso_dates), expected_approx, month_count))
    without_tooltip = sum(1 for d in days.values() if d["id"] is None)
    if without_tooltip > len(days) * 0.05:
        raise SystemExit(
            "error: %d of %d days without an id, the matching by id is broken.\n"
            "       assets/stats.svg is left untouched." % (without_tooltip, len(days)))


def crop(days, month_count):
    """Keeps the last N whole weeks, aligned Sunday to Saturday."""
    hoy = date.today()
    desde = hoy - timedelta(days=31 * month_count)
    sunday = desde - timedelta(days=(desde.weekday() + 1) % 7)
    window = {}
    for iso, day in days.items():
        if sunday <= date.fromisoformat(iso) <= hoy:
            window[iso] = day
    return sunday, window


# --------------------------------------------------------------------------
# computation
# --------------------------------------------------------------------------
def level_for(count):
    for index, (low, high) in enumerate(NIVELES):
        if low <= count <= high:
            return index + 1
    return 0


def resumen_texto(window):
    total = sum(d["count"] for d in window.values())
    active_days = sum(1 for d in window.values() if d["count"] > 0)
    best = max(window.values(), key=lambda d: d["count"])["count"] if window else 0

    # Current and longest streak, in consecutive days with activity.
    iso_dates = sorted(window)
    dias_con = {date.fromisoformat(f) for f in iso_dates if window[f]["count"] > 0}
    longest = actual = 0
    anterior = None
    for d in sorted(dias_con):
        actual = actual + 1 if anterior and (d - anterior).days == 1 else 1
        longest = max(longest, actual)
        anterior = d
    # The current streak counts back from today, or from yesterday if today is empty.
    racha = 0
    d = date.today()
    if d not in dias_con:
        d -= timedelta(days=1)
    while d in dias_con:
        racha += 1
        d -= timedelta(days=1)
    return {"total": total, "active_days": active_days, "best": best,
            "racha": racha, "longest": longest}


def weeks(window):
    """Groups by week: [{total, [levels of the 7 days]}, ...].

    Each day's level comes from NIVELES and the exact count, NOT from GitHub's
    data-level: that one is a quartile of the user's own activity, so a day with
    9 commits would top out at level 4 while here the top starts at 10+.
    """
    columns = []
    for i in range(0, len(window), 7):
        trozo = [window[k] for k in sorted(window)[i:i + 7]]
        if len(trozo) < 7:
            break
        columns.append({"total": sum(d["count"] for d in trozo),
                         "days": [level_for(d["count"]) for d in trozo]})
    return columns


def smooth_curve(points):
    """Smooth Bezier through the points (Catmull-Rom converted)."""
    if len(points) < 2:
        return ""
    d = ["M %.2f %.2f" % points[0]]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else points[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        d.append("C %.2f %.2f, %.2f %.2f, %.2f %.2f"
                 % (c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
    return " ".join(d)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(window, columns, metrics):
    info = resumen_texto(window)
    # PAINTED width of the matrix, not the grid pitch: the last cell has no gap
    # after it, so it is (columns - 1) * PITCH + CELDA. The wrong count left the
    # block 4 units short and off-centre (measured centre 746 on a 750 canvas).
    graph_width = (len(columns) - 1) * PITCH + CELDA
    # The graph is CENTERED, and computed rather than hardcoded so that changing
    # STATS_MONTHES keeps it in the middle with no edit here.
    grid_x = (ANCHO_LIENZO - graph_width) // 2

    # --- the numbers line, centered, every run measured ---
    # Centering needs the total width up front: sum every run and start at
    # (1500 - total)/2, since placing left to right would not know where it ends.
    SEPARADOR = 26
    ESPACIO = 14
    runs = [
        ("%d" % info["total"], "num", False),
        ("contributions", "lab", True),
        ("%d" % info["active_days"], "num", True),
        ("active days", "lab", True),
        ("%d" % info["best"], "num", True),
        ("best day", "lab", True),
        ("%d" % info["longest"], "num", True),
        ("longest streak", "lab", False),
    ]
    line_width = 0
    for text, _clase, dot in runs:
        if dot:
            line_width += SEPARADOR
        line_width += metrics.width(text, 34, seguro=False) + ESPACIO
    line_width -= ESPACIO          # the last run has no gap after it

    parts = []
    x = (ANCHO_LIENZO - line_width) / 2
    for text, clase, dot in runs:
        if dot:
            parts.append('  <text class="sep" x="%.1f" y="%d">\u00b7</text>'
                          % (x + 6, BASE_RESUMEN))
            x += SEPARADOR
        parts.append('  <text class="%s" x="%.1f" y="%d">%s</text>'
                      % (clase, x, BASE_RESUMEN, escape(text)))
        x += metrics.width(text, 34, seguro=False) + ESPACIO
    summary = "\n".join(parts)

    # --- the curve: one point per week ---
    maximo = max((c["total"] for c in columns), default=0) or 1
    points = []
    for i, column in enumerate(columns):
        px = grid_x + i * PITCH + CELDA / 2
        py = CURVE_BASE - (column["total"] / maximo) * (CURVE_BASE - CURVE_TOP)
        points.append((px, py))
    curve = smooth_curve(points)
    ground = " L %.2f %.2f L %.2f %.2f Z" % (
        points[-1][0], CURVE_BASE, points[0][0], CURVE_BASE)
    area = curve + ground

    # --- month labels ---
    # Months do NOT fall on column boundaries (May 1 landed inside the column
    # starting April 26), so days are walked and the column holding the first of
    # each month is labeled. If two months share a column only the first one is
    # labeled, otherwise the labels overlap.
    labels = []
    seen = set()
    previous_month = None
    for k, iso in enumerate(sorted(window)):
        month = date.fromisoformat(iso).month
        if month != previous_month:
            column = k // 7
            if column not in seen:
                seen.add(column)
                labels.append('  <text class="month" x="%.1f" y="%d">%s</text>'
                                 % (grid_x + column * PITCH, MONTHS_TOP,
                                    MONTHS[month - 1]))
            previous_month = month
    month_count = "\n".join(labels)

    # --- the matrix ---
    cells = []
    for i, column in enumerate(columns):
        for row, level in enumerate(column["days"]):
            x = grid_x + i * PITCH
            y = MATRIX_TOP + row * PITCH
            # Color goes in an attribute, not only in the class, as a fallback for
            # renderers that do not apply CSS.
            cells.append(
                '  <rect class="cuadrado n%d" x="%d" y="%d" width="%d" height="%d"'
                ' fill="%s" fill-opacity="%s" stroke="%s" stroke-opacity="%s"/>'
                % (level, x, y, CELDA, CELDA,
                   COLOR_NIVEL[level],
                   "1" if level else ".55",
                   COLOR_BORDE, ".18" if level else ".38"))
    # Weekday labels, in the left margin.
    for row, letter in enumerate(WEEKDAY_INITIALS):
        if not letter:
            continue
        cells.append('  <text class="day" x="%d" y="%d" text-anchor="end">%s</text>'
                      % (grid_x - 16, MATRIX_TOP + row * PITCH + CELDA * 0.72, letter))
    matriz = "\n".join(cells)

    # --- the legend ---
    # BELOW the matrix, not to the right: only 178 units were left there and the
    # legend needed 260, so it ran off a 1500 wide canvas.
    legend_y = MATRIX_TOP + 7 * PITCH + 34
    # The whole block is measured and started at (1500 - total)/2, same as the
    # numbers line: eyeballing the center never comes out right.
    HUECO_TEXTO = 16
    PASO_CUADRADO = 39
    ANCHO_CUADRADO = 30
    legend_width = (metrics.width("-", 24, seguro=False) + HUECO_TEXTO
                     + 5 * ANCHO_CUADRADO + 4 * (PASO_CUADRADO - ANCHO_CUADRADO)
                     + HUECO_TEXTO + metrics.width("+", 24, seguro=False))
    x = (ANCHO_LIENZO - legend_width) / 2
    legend = []
    legend.append('  <text class="esc" x="%.1f" y="%d">-</text>' % (x, legend_y))
    x += metrics.width("-", 24, seguro=False) + HUECO_TEXTO
    for level in range(5):
        legend.append(
            '  <rect class="cuadrado n%d" x="%.1f" y="%d" width="%d" height="%d"'
            ' fill="%s" fill-opacity="%s" stroke="%s" stroke-opacity="%s"/>'
            % (level, x, legend_y - 27, ANCHO_CUADRADO, ANCHO_CUADRADO,
               COLOR_NIVEL[level],
               "1" if level else ".55", COLOR_BORDE, ".18" if level else ".38"))
        x += PASO_CUADRADO
    x += HUECO_TEXTO - (PASO_CUADRADO - ANCHO_CUADRADO)
    legend.append('  <text class="esc" x="%.1f" y="%d">+</text>' % (x, legend_y))
    legend_text = "\n".join(legend)

    high = legend_y + 30   # includes the legend, which sits under the matrix

    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        template = fh.read()
    for marker in ("{{HEIGHT}}", "{{RESUMEN}}", "{{AREA}}", "{{CURVA}}",
                     "{{MESES}}", "{{MATRIZ}}", "{{LEYENDA}}", "{{GENERATED}}"):
        if template.count(marker) != 1:
            raise SystemExit(
                "error: placeholder %s does not appear exactly once in the template"
                % marker)

    output = (template
              .replace("{{HEIGHT}}", str(high))
              .replace("{{RESUMEN}}", summary)
              .replace("{{AREA}}", area)
              .replace("{{CURVA}}", curve)
              .replace("{{MESES}}", month_count)
              .replace("{{MATRIZ}}", matriz)
              .replace("{{LEYENDA}}", legend_text)
              .replace("{{GENERATED}}", ""))
    return output, high, info


def main():
    dry = "--dry-run" in sys.argv
    owner = os.environ.get("REPO_OWNER") or "stezf"
    month_count = int(os.environ.get("STATS_MONTHES") or MESES)
    metrics = Metrics(METRICS_PATH)

    print("fetching the contributions graph of %s..." % owner)
    days = fetch_html(owner)
    check(days, month_count)
    sunday, window = crop(days, month_count)
    columns = weeks(window)

    info = resumen_texto(window)
    print("  window: %s -> %s (%d weeks)"
          % (sunday.isoformat(), max(window), len(columns)))
    print("  %d contributions, %d active days, best day %d, longest streak %d"
          % (info["total"], info["active_days"], info["best"], info["longest"]))

    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output, high, _ = render(window, columns, metrics)

    import xml.dom.minidom
    xml.dom.minidom.parseString(output)

    if dry:
        print("\n--dry-run: valid XML (canvas 1500x%d), nothing written" % high)
        return
    with open(DESTINATION, "w", encoding="utf-8") as fh:
        fh.write(output)
    print("\nwrote %s (canvas 1500x%d, %d cells, %d bytes)"
          % (os.path.relpath(DESTINATION, ROOT), high, len(columns) * 7,
             len(output.encode())))


if __name__ == "__main__":
    main()
