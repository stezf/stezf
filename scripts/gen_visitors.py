#!/usr/bin/env python3
"""Builds assets/visitors.svg with the profile view count.

The number comes from komarev, which counts profile views. It cannot be used
directly in the README as <img src="https://komarev..."> because komarev only
replies with SVG, and GitHub serves external images through the camo proxy,
which does not accept SVG: it answers 404 "Cannot proxy the given URL" even
when komarev returns a perfectly fine 200. The SVG files in this repo do show
up, because they are served straight from raw.githubusercontent.com and never
go through the proxy.

So the count is read here and drawn into an SVG of our own, with the same
palette as the rest of the profile.

Usage:
    python3 scripts/gen_visitors.py            # queries and writes the SVG
    python3 scripts/gen_visitors.py --dry-run  # queries and only reports

Environment variables:
    REPO_OWNER   repo owner, defaults to stezf
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "visitors.template.svg")
METRICS = os.path.join(HERE, "text-metrics.json")
DESTINATION = os.path.join(ROOT, "assets", "visitors.svg")

LABEL = "Visits"

# badge geometry
HEIGHT = 56
RADIUS = 28
PADDING = 22
GAP = 14
FONT_SIZE = 30
STROKE = 2


class Metrics:
    """Text widths, from the table in scripts/text-metrics.json."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.regular = data["regular"]
        self.bold = data["negrita"]

    def width(self, text, size, bold=False):
        table = self.bold if bold else self.regular
        total = 0.0
        for ch in text:
            total += table.get(ch, table.get("n", 500))
        return total / 1000.0 * size


def fetch_count(owner):
    """Return the visit count, read out of komarev's SVG.

    komarev offers no JSON endpoint, only SVG, so the number is pulled from the
    <text> elements of the response: every label appears twice (a shadow copy
    underneath the real one), and the count is the only text made purely of
    digits.
    """
    url = "https://komarev.com/ghpvc/?username=%s" % owner
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) stezf-profile-readme",
        "Accept": "image/svg+xml",
    }
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            svg = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        raise SystemExit("error: komarev returned %s\n"
                         "       assets/visitors.svg is left untouched." % err.code)
    except urllib.error.URLError as err:
        raise SystemExit(
            "error: could not reach komarev (%s).\n"
            "       assets/visitors.svg is left untouched, so the profile keeps\n"
            "       showing the last count that could be read." % err.reason)

    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    numbers = [t.strip() for t in texts if re.fullmatch(r"[\d.,]+", t.strip())]
    if not numbers:
        raise SystemExit(
            "error: no number found in the komarev response.\n"
            "       they have most likely changed the SVG format.\n"
            "       assets/visitors.svg is left untouched.")
    raw = numbers[-1]
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        raise SystemExit("error: the count came back as %r, with no digits" % raw)
    return digits


def render(number, metrics):
    label_width = metrics.width(LABEL, FONT_SIZE)
    number_width = metrics.width(number, FONT_SIZE, bold=True)
    width = STROKE + PADDING + label_width + GAP + number_width + PADDING + STROKE

    with open(TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()

    # str.replace hits EVERY occurrence, and the template comment is the usual
    # trap: if it quotes a marker, the body would also be written inside the
    # comment. Requiring each marker to appear exactly once does not work here,
    # because WIDTH and HEIGHT legitimately appear twice on the same line (in
    # the viewBox and in the attribute). What has to be true is: every marker
    # appears outside any comment, and never inside one.
    inside = "".join(re.findall(r"<!--.*?-->", template, flags=re.S))
    outside = re.sub(r"<!--.*?-->", "", template, flags=re.S)
    for marker in ("{{WIDTH}}", "{{WIDTH_INT}}", "{{HEIGHT}}", "{{HEIGHT_INT}}",
                   "{{RADIUS}}", "{{X_LABEL}}", "{{X_NUM}}", "{{BASE}}",
                   "{{NUMBER}}"):
        if inside.count(marker):
            raise SystemExit(
                "error: the marker %s appears inside a comment in %s.\n"
                "       the replace would write it there too. Quote it without"
                " the braces." % (marker, os.path.relpath(TEMPLATE, ROOT)))
        if outside.count(marker) == 0:
            raise SystemExit(
                "error: the marker %s does not appear in %s"
                % (marker, os.path.relpath(TEMPLATE, ROOT)))

    # The baseline is derived from the text box instead of eyeballed: at 30px the
    # box runs from 0.72em (no capitals, no accents) to 0.95em (both), and
    # "Visits" happens to be exactly the capital-heavy case.
    box_height = FONT_SIZE * 0.72
    baseline = STROKE + (HEIGHT - 2 * STROKE + box_height) / 2

    output = (template
              .replace("{{WIDTH}}", "%g" % width)
              .replace("{{WIDTH_INT}}", "%g" % (width - 2 * STROKE))
              .replace("{{HEIGHT}}", "%g" % HEIGHT)
              .replace("{{HEIGHT_INT}}", "%g" % (HEIGHT - 2 * STROKE))
              .replace("{{RADIUS}}", "%g" % RADIUS)
              .replace("{{X_LABEL}}", "%g" % (STROKE + PADDING))
              .replace("{{X_NUM}}", "%g" % (STROKE + PADDING + label_width + GAP))
              .replace("{{BASE}}", "%g" % baseline)
              .replace("{{NUMBER}}", number))
    return output, width


def main():
    dry_run = "--dry-run" in sys.argv
    owner = os.environ.get("REPO_OWNER") or "stezf"
    metrics = Metrics(METRICS)

    print("reading the visit counter for %s..." % owner)
    number = fetch_count(owner)
    output, width = render(number, metrics)

    import xml.dom.minidom
    xml.dom.minidom.parseString(output)

    if dry_run:
        print("\n--dry-run: valid XML (%s visits, badge %g wide), nothing written"
              % (number, width))
        return

    read_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(DESTINATION, "w", encoding="utf-8") as fh:
        fh.write(output)
    print("\nwrote %s (%s visits, badge %gx%g, %d bytes)"
          % (os.path.relpath(DESTINATION, ROOT), number, width, HEIGHT,
             len(output.encode())))
    print("read at %s" % read_at)


if __name__ == "__main__":
    main()
