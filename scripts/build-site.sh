#!/usr/bin/env bash
# Build the GitHub Pages site: the campaign page from docs/ plus the study (study/*.md) rendered to
# HTML under estudio/. Needs pandoc. Used by .github/workflows/pages.yml; also runs locally.
#
#   scripts/build-site.sh [outdir]     (default: _site)
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-_site}"
rm -rf "$out"
mkdir -p "$out/estudio"
cp -r docs/. "$out/"
cp -r study/img "$out/estudio/img"

nav=$(mktemp -d)
trap 'rm -rf "$nav"' EXIT

# render <lang> <source.md> <output.html> <nav html>
render() {
  printf '%s\n' "$4" > "$nav/$1.html"
  pandoc "$2" --from markdown-citations+autolink_bare_uris --to html5 --standalone \
    --shift-heading-level-by=-1 \
    --metadata lang="$1" \
    --css ../study.css \
    --include-before-body "$nav/$1.html" \
    --output "$3"
}

render es study/study_es.md "$out/estudio/index.html" \
  '<nav class="top"><a href="../">← Volver a la campaña</a><a href="en.html" hreflang="en" lang="en">English</a></nav>'
render en study/study_en.md "$out/estudio/en.html" \
  '<nav class="top"><a href="../" hreflang="es">← Campaign page (Spanish)</a><a href="./" hreflang="es" lang="es">Español</a></nav>'

echo "Site built in $out/"
