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
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
PLANTILLA = os.path.join(AQUI, "projects.template.svg")
METRICAS = os.path.join(AQUI, "text-metrics.json")
DESTINO = os.path.join(RAIZ, "assets", "projects.svg")

# --- card design (viewBox units, see the template) --------------------------
MARGEN = 40
SEPARACION = 40
ALTO_TARJETA = 236
HUECO_FILAS = 34
RELLENO = 36              # inner padding
CUERPO_NOMBRE = 46
CUERPO_DESCRIPCION = 29
CUERPO_LENGUAJE = 27
LINEA_DESCRIPCION = 40
BASE_NOMBRE = 62
BASE_DESCRIPCION_1 = 112
BASE_LENGUAJE = 202
RADIO_ESQUINA = 28
RADIO_PUNTO = 9
LINEAS_DESCRIPCION = 2
# With an odd number of repos the last row has a gap: True makes the last card
# span both columns and cover it.
ULTIMA_OCUPA_AMBAS_COLUMNAS = False

# Language colors from github-linguist (lib/linguist/languages.yml). Add new
# languages here; unknown ones fall back to the brand blue.
COLOR_LENGUAJE = {
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
COLOR_MARCA = "#568eff"

# Liberation Sans (Arial metrics) is narrower than Segoe UI but wider than other
# substitutes; this margin covers the difference so text never overflows.
MARGEN_SEGURIDAD = 1.06


class Metricas:
    """Measures strings with the width table in scripts/text-metrics.json."""

    def __init__(self, ruta):
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
        self.regular = datos["regular"]
        self.negrita = datos["negrita"]
        self.objetivo = 0.0

    def ancho(self, texto, cuerpo, negrita=False):
        tabla = self.negrita if negrita else self.regular
        total = 0.0
        for ch in texto:
            total += tabla.get(ch, tabla.get("n", 500))
        return total / 1000.0 * cuerpo * MARGEN_SEGURIDAD

    def ajusta(self, texto, cuerpo, negrita=False):
        """Truncates with an ellipsis until it fits the target width."""
        objetivo = int(self.objetivo)
        if self.ancho(texto, cuerpo, negrita) <= objetivo:
            return texto
        puntos = self.ancho("...", cuerpo, negrita)
        # Invariant: 'bajo' is a prefix that DOES fit with the dots, 'alto' one
        # that does not. The +1 here is deliberate: without it the previous
        # version overshot by one glyph (measured 620.2 vs a 618 target).
        bajo, alto = 0, len(texto)
        while bajo + 1 < alto:
            medio = (bajo + alto) // 2
            if self.ancho(texto[:medio], cuerpo, negrita) + puntos <= objetivo:
                bajo = medio
            else:
                alto = medio
        return texto[:bajo].rstrip(" ,;:.-") + "..."

    def _cabe(self, palabras, limite):
        """Greedily wraps words into lines of width `limite`, without balancing."""
        lineas = []
        actual = ""
        for palabra in palabras:
            tentativa = palabra if not actual else actual + " " + palabra
            if not actual or self.ancho(tentativa, self.cuerpo) <= limite:
                actual = tentativa
            else:
                lineas.append(actual)
                actual = palabra
        if actual:
            lineas.append(actual)
        return lineas

    def envuelve(self, texto, cuerpo, lineas):
        """Wraps text into `lineas` lines, widest first.

        The wrap is BALANCED, and that matters: a greedy wrap fills the first
        line and dumps the rest in the last one, so a description that fits in
        two lines ends up ellipsized. So we look for the MINIMUM width that
        still fits the text in `lineas` lines.

        The search is over INTEGERS and without the +1, both tested: in floats
        it stalls (once low and high meet, (low+high)/2 rounds down to low, the
        step stops shrinking the interval and the loop never ends), and with the
        +1 a failed test jumps past the answer and truncates text that fits.
        """
        self.cuerpo = cuerpo
        palabras = texto.split()
        if not palabras:
            return [""] * lineas

        objetivo = int(self.objetivo)
        ancho_completo = self.ancho(texto, cuerpo)

        if ancho_completo <= objetivo:
            return [texto] + [""] * (lineas - 1)

        # A single word wider than the card cannot be wrapped; truncate it.
        if max(self.ancho(p, cuerpo) for p in palabras) > objetivo:
            return [self.ajusta(palabras[0], cuerpo)] + [""] * (lineas - 1)

        # Invariant: 'alto' is always a width the text DOES fit in 'lineas'
        # lines, and the search is BOUNDED by the card width (an unbounded
        # minimum returned 874 for a usable width of 618).
        if len(self._cabe(palabras, objetivo)) > lineas:
            # Does not fit even at full card width: greedy-wrap it and ellipsize
            # the last line, which keeps as much text as possible.
            salida = self._cabe(palabras, objetivo)
            ultimas = salida[: lineas - 1]
            ultimas.append(self.ajusta(" ".join(salida[lineas - 1:]), cuerpo))
            return ultimas + [""] * (lineas - len(ultimas))

        # Otherwise take the MINIMUM width that fits: the most even wrap.
        bajo, alto = 0, objetivo
        while bajo + 1 < alto:
            medio = (bajo + alto) // 2
            if len(self._cabe(palabras, medio)) <= lineas:
                alto = medio
            else:
                bajo = medio

        salida = self._cabe(palabras, alto)
        return salida + [""] * (lineas - len(salida))


def escapar(texto):
    """Escapes text so it can be embedded in XML."""
    return (texto.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def pedir_repos(duenno):
    """Fetches one object per listed repo from the GitHub API."""
    cabeceras = {
        "User-Agent": "stezf-profile-readme",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        cabeceras["Authorization"] = "Bearer " + token

    salida = []
    for nombre in REPOS:
        url = "https://api.github.com/repos/%s/%s" % (duenno, nombre)
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
                "not end up empty." % (err.code, duenno, nombre, motivo))
        except urllib.error.URLError as err:
            raise SystemExit(
                "error: could not reach the GitHub API (%s).\n"
                "       assets/projects.svg is left untouched, so the section does\n"
                "       not end up empty." % err.reason)
        salida.append({
            "nombre": crudo.get("name") or nombre,
            "descripcion": (crudo.get("description") or "").strip(),
            "lenguaje": crudo.get("language") or "",
        })
        print("  %-14s %-10s %s" % (
            salida[-1]["nombre"], salida[-1]["lenguaje"] or "-",
            (salida[-1]["descripcion"][:54] or "(sin descripcion)")))
    return salida


def tarjeta(dato, x, y, ancho, metricas):
    """One card: name, two-line description and language with its dot."""
    metricas.objetivo = ancho - 2 * RELLENO

    lineas = metricas.envuelve(
        dato["descripcion"] or "Sin descripcion.", CUERPO_DESCRIPCION,
        LINEAS_DESCRIPCION)
    nombre = metricas.ajusta(dato["nombre"], CUERPO_NOMBRE, negrita=True)
    lenguaje = dato["lenguaje"]
    color = COLOR_LENGUAJE.get(lenguaje, COLOR_MARCA)
    x_texto = x + RELLENO

    partes = ["    <g>"]
    partes.append(
        '      <rect class="card" x="%d" y="%d" width="%d" height="%d" rx="%d"'
        ' filter="url(#card-shadow)"/>'
        % (x, y, ancho, ALTO_TARJETA, RADIO_ESQUINA))
    partes.append('      <text class="name" x="%d" y="%d">%s</text>'
                  % (x_texto, y + BASE_NOMBRE, escapar(nombre)))
    for indice, linea in enumerate(lineas):
        if not linea:
            continue
        partes.append('      <text class="desc" x="%d" y="%d">%s</text>'
                      % (x_texto, y + BASE_DESCRIPCION_1 + indice * LINEA_DESCRIPCION,
                         escapar(linea)))
    if lenguaje:
        partes.append('      <circle cx="%d" cy="%d" r="%d" fill="%s"/>'
                      % (x_texto + RADIO_PUNTO, y + BASE_LENGUAJE - 9,
                         RADIO_PUNTO, color))
        partes.append('      <text class="lang" x="%d" y="%d">%s</text>'
                      % (x_texto + 2 * RADIO_PUNTO + 14, y + BASE_LENGUAJE,
                         escapar(metricas.ajusta(lenguaje, CUERPO_LENGUAJE))))
    partes.append("    </g>")
    return "\n".join(partes)


def render(datos, metricas, generado):
    total = len(datos)
    ancho_columna = (1500 - 2 * MARGEN - SEPARACION) // 2
    ancho_completo = 1500 - 2 * MARGEN
    impar = total % 2 == 1 and ULTIMA_OCUPA_AMBAS_COLUMNAS
    paso = ALTO_TARJETA + HUECO_FILAS

    tarjetas = []
    cajas = []
    fila = 0
    for indice, dato in enumerate(datos):
        if impar and indice == total - 1:
            # Odd repo count: the last card spans both columns instead of leaving a gap.
            # It always starts a new row, so the row is counted first.
            if indice > 0:
                fila += 1
            cajas.append((MARGEN, fila * paso, ancho_completo))
            tarjetas.append(tarjeta(dato, MARGEN, fila * paso, ancho_completo, metricas))
            continue
        if indice % 2 == 0 and indice > 0:
            fila += 1
        x = MARGEN + (indice % 2) * (ancho_columna + SEPARACION)
        cajas.append((x, fila * paso, ancho_columna))
        tarjetas.append(tarjeta(dato, x, fila * paso, ancho_columna, metricas))

    # Overlap check: a card in the wrong row draws on top of another one and the SVG
    # is still valid XML, so the bug would only show up when rendering.
    for i, (x1, y1, w1) in enumerate(cajas):
        for x2, y2, w2 in cajas[i + 1:]:
            solapan = (x1 < x2 + w2 and x2 < x1 + w1
                       and y1 < y2 + ALTO_TARJETA and y2 < y1 + ALTO_TARJETA)
            if solapan:
                raise SystemExit(
                    "error: cards %d and %d overlap "
                    "(%d,%d %dx%d and %d,%d %dx%d)"
                    % (i + 1, i + 2, x1, y1, w1, ALTO_TARJETA, x2, y2, w2, ALTO_TARJETA))

    alto = fila * paso + ALTO_TARJETA

    with open(PLANTILLA, encoding="utf-8") as fh:
        plantilla = fh.read()

    # str.replace hits every occurrence, and a marker can be quoted in a comment:
    # the body would then be written inside the comment too. Check before writing.
    for marcador in ("{{CARDS}}", "{{ALTO}}", "{{GENERADO}}"):
        apariciones = plantilla.count(marcador)
        if apariciones != 1:
            raise SystemExit(
                "error: placeholder %s appears %d times in %s, it should appear once.\n"
                "       most likely a comment quotes the literal token."
                % (marcador, apariciones, os.path.relpath(PLANTILLA, RAIZ)))

    cuerpo = "\n".join(tarjetas)
    salida = (plantilla
              .replace("{{CARDS}}", cuerpo)
              .replace("{{ALTO}}", str(alto))
              .replace("{{GENERADO}}", generado))
    return salida, alto


def main():
    seco = "--dry-run" in sys.argv
    duenno = os.environ.get("REPO_OWNER") or "stezf"
    metricas = Metricas(METRICAS)

    print("querying the GitHub API (%s):" % duenno)
    datos = pedir_repos(duenno)
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    salida, alto = render(datos, metricas, generado)

    # Validate before writing: if the XML does not parse, the good file stays.
    import xml.dom.minidom
    xml.dom.minidom.parseString(salida)

    if seco:
        print("\n--dry-run: the SVG is valid (%d cards, canvas 1500x%d),"
              " nothing written" % (len(datos), alto))
        return

    with open(DESTINO, "w", encoding="utf-8") as fh:
        fh.write(salida)
    print("\nwrote %s (%d cards, canvas 1500x%d, %d bytes)"
          % (os.path.relpath(DESTINO, RAIZ), len(datos), alto,
             len(salida.encode())))


if __name__ == "__main__":
    main()
