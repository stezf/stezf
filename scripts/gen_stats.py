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

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
PLANTILLA = os.path.join(AQUI, "stats.template.svg")
METRICAS = os.path.join(AQUI, "text-metrics.json")
DESTINO = os.path.join(RAIZ, "assets", "stats.svg")

# --- matrix window and size -------------------------------------------------
MESES = 6
PITCH = 44
CELDA = 36
HUECO = PITCH - CELDA
ANCHO_LIENZO = 1500

# --- vertical ---------------------------------------------------------------
BASE_RESUMEN = 40
TOPE_CURVA = 92        # y of the curve peak
BASE_CURVA = 268       # y of the curve floor
TOPE_MESES = 322       # baseline of the month labels
TOPE_MATRIZ = 340      # y of the first row of cells
ALTO_CELDA = PITCH     # 7 days per column

# Iniciales de lunes, miercoles, viernes y domingo. El identificador sigue
# llamandose DIAS_SEMANA para no tocar nombres, pero los valores ya estan
# en ingles: M, W, F, S.
DIAS_SEMANA = ["M", "", "W", "", "F", "", "S"]
# Abreviaturas de mes. El identificador sigue llamandose MESES_ES para no tocar
# nombres, pero los valores ya estan en ingles. "may" es igual en los dos
# idiomas, de modo que de abril a octubre solo cambian "abr" a "apr" y "ago" a
# "aug"; los siete meses que se ven ahora son esos.
MESES_ES = ["jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec"]

# Color scale by daily contribution count. It is ABSOLUTE, not quartile-based
# like GitHub's: over 6 months counts run 1..18, so quartiles would leave
# almost everything at level 1.
NIVELES = [(1, 2), (3, 5), (6, 9), (10, 10 ** 9)]
COLOR_NIVEL = ["#e8ecf1", "#1a3a6b", "#24509e", "#2f6ad0", "#568eff"]
COLOR_NIVEL_OSCURO = ["#1b2233", "#1b3257", "#21468f", "#2a5cb8", "#568eff"]
COLOR_BORDE = "#3a416a"


class Metricas:
    """Measures text with the width table in scripts/text-metrics.json."""

    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
        self.regular = datos["regular"]

    def ancho(self, texto, cuerpo, seguro=True):
        """Width of a text in screen units.

        seguro=True adds a 2% margin, used when fitting lines so text does not
        overflow in a viewer with a slightly wider font. To CENTER, measure with
        seguro=False: with the cushion the line is painted shorter than planned.
        """
        total = 0.0
        for ch in texto:
            total += self.regular.get(ch, self.regular.get("n", 500))
        return total / 1000.0 * cuerpo * (1.02 if seguro else 1.0)


def escapar(texto):
    return (texto.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def traer(duenno):
    """Fetches the graph fragment and returns {ISO date: contribution}."""
    url = "https://github.com/users/%s/contributions" % duenno
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
    dias = {}
    for td in re.findall(r"<td\b[^>]*>", html):
        fecha = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', td)
        nivel = re.search(r'data-level="(\d)"', td)
        ident = re.search(r'id="(contribution-day-component-[\d-]+)"', td)
        if fecha and nivel:
            dias[fecha.group(1)] = {"nivel": int(nivel.group(1)),
                                    "id": ident.group(1) if ident else None}

    # The exact count only appears when there is something to count; it is matched
    # to the day by the tool-tip id, the same one the td carries.
    conteos = {}
    for ident, cuerpo in re.findall(
            r'<tool-tip\b[^>]*\bfor="([^"]+)"[^>]*>(.*?)</tool-tip>', html, re.S):
        m = re.search(r"(\d+)\s+contribution", cuerpo)
        if m:
            conteos[ident] = int(m.group(1))

    for iso, dia in dias.items():
        dia["conteo"] = conteos.get(dia["id"], 0) if dia["id"] else 0

    if not dias:
        raise SystemExit(
            "error: not a single day was found in the response.\n"
            "       GitHub has most likely changed the markup of the "
            "graph.\n"
            "       assets/stats.svg is left untouched.")
    return dias


def comprobar(dias, meses):
    """Fails if the data does not look normal, before anything is written."""
    fechas = sorted(dias)
    esperado_aprox = meses * 30
    if len(fechas) < esperado_aprox:
        raise SystemExit(
            "error: only %d days, at least %d were expected for %d months.\n"
            "       assets/stats.svg is left untouched."
            % (len(fechas), esperado_aprox, meses))
    sin_tooltip = sum(1 for d in dias.values() if d["id"] is None)
    if sin_tooltip > len(dias) * 0.05:
        raise SystemExit(
            "error: %d of %d days without an id, the matching by id is broken.\n"
            "       assets/stats.svg is left untouched." % (sin_tooltip, len(dias)))


def recortar(dias, meses):
    """Keeps the last N whole weeks, aligned Sunday to Saturday."""
    hoy = date.today()
    desde = hoy - timedelta(days=31 * meses)
    domingo = desde - timedelta(days=(desde.weekday() + 1) % 7)
    ventana = {}
    for iso, dia in dias.items():
        if domingo <= date.fromisoformat(iso) <= hoy:
            ventana[iso] = dia
    return domingo, ventana


# --------------------------------------------------------------------------
# computation
# --------------------------------------------------------------------------
def nivel_de(conteo):
    for indice, (bajo, alto) in enumerate(NIVELES):
        if bajo <= conteo <= alto:
            return indice + 1
    return 0


def resumen_texto(ventana):
    total = sum(d["conteo"] for d in ventana.values())
    activos = sum(1 for d in ventana.values() if d["conteo"] > 0)
    mejor = max(ventana.values(), key=lambda d: d["conteo"])["conteo"] if ventana else 0

    # Current and longest streak, in consecutive days with activity.
    fechas = sorted(ventana)
    dias_con = {date.fromisoformat(f) for f in fechas if ventana[f]["conteo"] > 0}
    larga = actual = 0
    anterior = None
    for d in sorted(dias_con):
        actual = actual + 1 if anterior and (d - anterior).days == 1 else 1
        larga = max(larga, actual)
        anterior = d
    # The current streak counts back from today, or from yesterday if today is empty.
    racha = 0
    d = date.today()
    if d not in dias_con:
        d -= timedelta(days=1)
    while d in dias_con:
        racha += 1
        d -= timedelta(days=1)
    return {"total": total, "activos": activos, "mejor": mejor,
            "racha": racha, "larga": larga}


def semanas(ventana):
    """Groups by week: [{total, [levels of the 7 days]}, ...].

    Each day's level comes from NIVELES and the exact count, NOT from GitHub's
    data-level: that one is a quartile of the user's own activity, so a day with
    9 commits would top out at level 4 while here the top starts at 10+.
    """
    columnas = []
    for i in range(0, len(ventana), 7):
        trozo = [ventana[k] for k in sorted(ventana)[i:i + 7]]
        if len(trozo) < 7:
            break
        columnas.append({"total": sum(d["conteo"] for d in trozo),
                         "dias": [nivel_de(d["conteo"]) for d in trozo]})
    return columnas


def curva_suave(puntos):
    """Smooth Bezier through the points (Catmull-Rom converted)."""
    if len(puntos) < 2:
        return ""
    d = ["M %.2f %.2f" % puntos[0]]
    for i in range(len(puntos) - 1):
        p0 = puntos[i - 1] if i > 0 else puntos[i]
        p1 = puntos[i]
        p2 = puntos[i + 1]
        p3 = puntos[i + 2] if i + 2 < len(puntos) else puntos[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        d.append("C %.2f %.2f, %.2f %.2f, %.2f %.2f"
                 % (c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
    return " ".join(d)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(ventana, columnas, metricas):
    info = resumen_texto(ventana)
    # PAINTED width of the matrix, not the grid pitch: the last cell has no gap
    # after it, so it is (columns - 1) * PITCH + CELDA. The wrong count left the
    # block 4 units short and off-centre (measured centre 746 on a 750 canvas).
    ancho_grafico = (len(columnas) - 1) * PITCH + CELDA
    # The graph is CENTERED, and computed rather than hardcoded so that changing
    # STATS_MONTHES keeps it in the middle with no edit here.
    grid_x = (ANCHO_LIENZO - ancho_grafico) // 2

    # --- the numbers line, centered, every run measured ---
    # Centering needs the total width up front: sum every run and start at
    # (1500 - total)/2, since placing left to right would not know where it ends.
    SEPARADOR = 26
    ESPACIO = 14
    runs = [
        ("%d" % info["total"], "num", False),
        ("contributions", "lab", True),
        ("%d" % info["activos"], "num", True),
        ("active days", "lab", True),
        ("%d" % info["mejor"], "num", True),
        ("best day", "lab", True),
        ("%d" % info["larga"], "num", True),
        ("longest streak", "lab", False),
    ]
    ancho_linea = 0
    for texto, _clase, punto in runs:
        if punto:
            ancho_linea += SEPARADOR
        ancho_linea += metricas.ancho(texto, 34, seguro=False) + ESPACIO
    ancho_linea -= ESPACIO          # the last run has no gap after it

    partes = []
    x = (ANCHO_LIENZO - ancho_linea) / 2
    for texto, clase, punto in runs:
        if punto:
            partes.append('  <text class="sep" x="%.1f" y="%d">\u00b7</text>'
                          % (x + 6, BASE_RESUMEN))
            x += SEPARADOR
        partes.append('  <text class="%s" x="%.1f" y="%d">%s</text>'
                      % (clase, x, BASE_RESUMEN, escapar(texto)))
        x += metricas.ancho(texto, 34, seguro=False) + ESPACIO
    resumen = "\n".join(partes)

    # --- the curve: one point per week ---
    maximo = max((c["total"] for c in columnas), default=0) or 1
    puntos = []
    for i, columna in enumerate(columnas):
        px = grid_x + i * PITCH + CELDA / 2
        py = BASE_CURVA - (columna["total"] / maximo) * (BASE_CURVA - TOPE_CURVA)
        puntos.append((px, py))
    curva = curva_suave(puntos)
    suelo = " L %.2f %.2f L %.2f %.2f Z" % (
        puntos[-1][0], BASE_CURVA, puntos[0][0], BASE_CURVA)
    area = curva + suelo

    # --- month labels ---
    # Months do NOT fall on column boundaries (May 1 landed inside the column
    # starting April 26), so days are walked and the column holding the first of
    # each month is labeled. If two months share a column only the first one is
    # labeled, otherwise the labels overlap.
    etiquetas = []
    vistos = set()
    mes_previo = None
    for k, iso in enumerate(sorted(ventana)):
        mes = date.fromisoformat(iso).month
        if mes != mes_previo:
            columna = k // 7
            if columna not in vistos:
                vistos.add(columna)
                etiquetas.append('  <text class="mes" x="%.1f" y="%d">%s</text>'
                                 % (grid_x + columna * PITCH, TOPE_MESES,
                                    MESES_ES[mes - 1]))
            mes_previo = mes
    meses = "\n".join(etiquetas)

    # --- the matrix ---
    celdas = []
    for i, columna in enumerate(columnas):
        for fila, nivel in enumerate(columna["dias"]):
            x = grid_x + i * PITCH
            y = TOPE_MATRIZ + fila * PITCH
            # Color goes in an attribute, not only in the class, as a fallback for
            # renderers that do not apply CSS.
            celdas.append(
                '  <rect class="cuadrado n%d" x="%d" y="%d" width="%d" height="%d"'
                ' fill="%s" fill-opacity="%s" stroke="%s" stroke-opacity="%s"/>'
                % (nivel, x, y, CELDA, CELDA,
                   COLOR_NIVEL[nivel],
                   "1" if nivel else ".55",
                   COLOR_BORDE, ".18" if nivel else ".38"))
    # Weekday labels, in the left margin.
    for fila, letra in enumerate(DIAS_SEMANA):
        if not letra:
            continue
        celdas.append('  <text class="dia" x="%d" y="%d" text-anchor="end">%s</text>'
                      % (grid_x - 16, TOPE_MATRIZ + fila * PITCH + CELDA * 0.72, letra))
    matriz = "\n".join(celdas)

    # --- the legend ---
    # BELOW the matrix, not to the right: only 178 units were left there and the
    # legend needed 260, so it ran off a 1500 wide canvas.
    y_leyenda = TOPE_MATRIZ + 7 * PITCH + 34
    # The whole block is measured and started at (1500 - total)/2, same as the
    # numbers line: eyeballing the center never comes out right.
    HUECO_TEXTO = 16
    PASO_CUADRADO = 39
    ANCHO_CUADRADO = 30
    ancho_leyenda = (metricas.ancho("-", 24, seguro=False) + HUECO_TEXTO
                     + 5 * ANCHO_CUADRADO + 4 * (PASO_CUADRADO - ANCHO_CUADRADO)
                     + HUECO_TEXTO + metricas.ancho("+", 24, seguro=False))
    x = (ANCHO_LIENZO - ancho_leyenda) / 2
    leyenda = []
    leyenda.append('  <text class="esc" x="%.1f" y="%d">-</text>' % (x, y_leyenda))
    x += metricas.ancho("-", 24, seguro=False) + HUECO_TEXTO
    for nivel in range(5):
        leyenda.append(
            '  <rect class="cuadrado n%d" x="%.1f" y="%d" width="%d" height="%d"'
            ' fill="%s" fill-opacity="%s" stroke="%s" stroke-opacity="%s"/>'
            % (nivel, x, y_leyenda - 27, ANCHO_CUADRADO, ANCHO_CUADRADO,
               COLOR_NIVEL[nivel],
               "1" if nivel else ".55", COLOR_BORDE, ".18" if nivel else ".38"))
        x += PASO_CUADRADO
    x += HUECO_TEXTO - (PASO_CUADRADO - ANCHO_CUADRADO)
    leyenda.append('  <text class="esc" x="%.1f" y="%d">+</text>' % (x, y_leyenda))
    leyenda_texto = "\n".join(leyenda)

    alto = y_leyenda + 30   # includes the legend, which sits under the matrix

    with open(PLANTILLA, encoding="utf-8") as fh:
        plantilla = fh.read()
    for marcador in ("{{ALTO}}", "{{RESUMEN}}", "{{AREA}}", "{{CURVA}}",
                     "{{MESES}}", "{{MATRIZ}}", "{{LEYENDA}}", "{{GENERADO}}"):
        if plantilla.count(marcador) != 1:
            raise SystemExit(
                "error: placeholder %s does not appear exactly once in the template"
                % marcador)

    salida = (plantilla
              .replace("{{ALTO}}", str(alto))
              .replace("{{RESUMEN}}", resumen)
              .replace("{{AREA}}", area)
              .replace("{{CURVA}}", curva)
              .replace("{{MESES}}", meses)
              .replace("{{MATRIZ}}", matriz)
              .replace("{{LEYENDA}}", leyenda_texto)
              .replace("{{GENERADO}}", ""))
    return salida, alto, info


def main():
    seco = "--dry-run" in sys.argv
    duenno = os.environ.get("REPO_OWNER") or "stezf"
    meses = int(os.environ.get("STATS_MONTHES") or MESES)
    metricas = Metricas(METRICAS)

    print("fetching the contributions graph of %s..." % duenno)
    dias = traer(duenno)
    comprobar(dias, meses)
    domingo, ventana = recortar(dias, meses)
    columnas = semanas(ventana)

    info = resumen_texto(ventana)
    print("  window: %s -> %s (%d weeks)"
          % (domingo.isoformat(), max(ventana), len(columnas)))
    print("  %d contributions, %d active days, best day %d, longest streak %d"
          % (info["total"], info["activos"], info["mejor"], info["larga"]))

    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    salida, alto, _ = render(ventana, columnas, metricas)

    import xml.dom.minidom
    xml.dom.minidom.parseString(salida)

    if seco:
        print("\n--dry-run: valid XML (canvas 1500x%d), nothing written" % alto)
        return
    with open(DESTINO, "w", encoding="utf-8") as fh:
        fh.write(salida)
    print("\nwrote %s (canvas 1500x%d, %d cells, %d bytes)"
          % (os.path.relpath(DESTINO, RAIZ), alto, len(columnas) * 7,
             len(salida.encode())))


if __name__ == "__main__":
    main()
