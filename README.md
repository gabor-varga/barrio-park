# Un parque para Ctra. de Chile y C/ Dr. Alfonso Chiscano Díaz

Neighbourhood campaign for a free public park with a children's playground for the homes on
Carretera de Chile and Calle Doctor Alfonso Chiscano Díaz (Las Palmas de Gran Canaria).
About 2,000 people in 816 homes live more than 1 km on foot from the nearest public park.

**The ask:** turn the abandoned plot next to the Las Palmeras Golf car park (0.38 ha) and a strip of the
municipal golf course along Calle Dr. Alfonso Chiscano Díaz (0.55 ha, about 6% of the course) into a
public park. Every home in the pocket would then be within 500 m of a park.

## What is in this repository

| Folder | Contents |
| --- | --- |
| `docs/` | Campaign website (Spanish, with an English page in `docs/en/`), published with GitHub Pages |
| `scripts/` | `build-site.sh`, which builds the website with the study for GitHub Pages |
| `study/` | The study, in English and Spanish, with its maps |
| `flyer/` | Flyers in Spanish: A4 poster and A5 letterbox flyer (PDFs to print, HTML to edit) |
| `peticion/` | Templates: the collective petition (Ley Orgánica 4/2001), its signature sheet and the information request. Fill in copies in `peticion/privado/`, which git ignores |
| `analysis/` | `green_access.py`, the proposal areas and the results of the latest run |
| `data/` | Where to download the input data (not committed) |

## Publish the website with GitHub Pages

The site is live at **https://parque.barrionegrin.es/** (custom domain; the old address
https://gabor-varga.github.io/barrio-park/ redirects there). The workflow
`.github/workflows/pages.yml` builds and deploys it on every push to `master` that touches `docs/`,
`study/` or the build script. It can also be run by hand from the **Actions** tab.

The build (`scripts/build-site.sh`) copies `docs/` and renders the study (`study/study_es.md`,
`study/study_en.md`) with pandoc into `estudio/` and `estudio/en.html`. To preview locally:

```bash
scripts/build-site.sh            # needs pandoc; writes _site/ (ignored by git)
python -m http.server -d _site   # then open http://localhost:8000
```

One-time setup on GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**, and
**Custom domain: `parque.barrionegrin.es`** (then tick *Enforce HTTPS* once GitHub has the certificate).
The domain's DNS (at Piensa Solutions) needs a CNAME record `parque` → `gabor-varga.github.io`.

## Print the flyers

| File | Use | Print |
| --- | --- | --- |
| `flyer/flyer.pdf` | A4 poster for building entrances and shops, with the map | A4, colour |
| `flyer/flyer-a5-2up.pdf` | Letterbox flyer: two A5 copies on one A4 sheet | A4 landscape, black and white, then cut in half |
| `flyer/flyer-a5.pdf` | The same letterbox flyer as a single A5 page | A5, black and white |

On a home printer choose "Actual size" (not "Fit to page"); a print shop can use the files as they are.

The QR codes, the printed web address and the A4 map are filled in by `scripts/flyer.py`. After
changing the web address, run it again:

```bash
pip install segno weasyprint==62.3 pydyf==0.10.0
python scripts/flyer.py https://parque.barrionegrin.es/ --pdf
```

The map is copied from the website (`docs/index.html`), so redraw that first if the data changed.
The Archivo fonts are bundled in `flyer/fonts/` (SIL Open Font License), so the PDFs never depend
on the network.
The HTML files (`flyer/flyer.html`, `flyer/flyer-a5.html`) can also be printed from a browser:
margins "None", background graphics on.

## Run the analysis

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r analysis/requirements.txt

python analysis/green_access.py \
  --barrios data/<istac-barrios-2018>.geojson \
  --names-csv data/<CL_BARRIOS_ES70_2024>/codes.csv \
  --catastro data/A.ES.SDGC.BU.<code>.zip \
  --proposal-file analysis/proposals/proposed_parks.geojson \
  --new-buildings analysis/new_buildings.geojson \
  --strip-widths 30,40,50 \
  --outdir green_access_out
```

To redraw the website's maps from the run (Spanish and English pages):

```bash
python analysis/site_map.py green_access_out/layers.gpkg --html docs/index.html
python analysis/site_map.py green_access_out/layers.gpkg --html docs/en/index.html --lang en
```

The run downloads the OpenStreetMap walking network, parks and playgrounds, so it needs internet access
and takes several minutes. Outputs go to `green_access_out/` (ignored by git); copy the ones worth keeping
into `analysis/results/<date>/`. See `data/README.md` for the input files.

## Data and licences

Walking network, parks and playgrounds: © OpenStreetMap contributors (ODbL).
Buildings and homes: Dirección General del Catastro, INSPIRE buildings.
Barrio boundaries and names: ISTAC (Instituto Canario de Estadística).
