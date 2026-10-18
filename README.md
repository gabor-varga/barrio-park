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
| `docs/` | Campaign website (Spanish), published with GitHub Pages |
| `study/` | The study, in English and Spanish, with its maps |
| `flyer/` | One-page A4 flyer in Spanish (open in a browser and print to PDF) |
| `analysis/` | `green_access.py`, the proposal areas and the results of the latest run |
| `data/` | Where to download the input data (not committed) |

## Publish the website with GitHub Pages

1. Push this repository to GitHub. It must be public for GitHub Pages on a free account.
2. On GitHub: **Settings → Pages → Build and deployment → Source: Deploy from a branch**,
   branch `main`, folder `/docs`, then **Save**.
3. After a minute the site is live at `https://<your-user>.github.io/<repo-name>/`.
   Put that address in the flyer's QR code.

## Print the flyer

Open `flyer/flyer.html` in Chrome, press Ctrl+P (Cmd+P on a Mac), choose A4, margins **None**, and
turn on **Background graphics**. Fill in the placeholders in brackets first: meeting date and place,
contact, QR code and study link.

## Run the analysis

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r analysis/requirements.txt

python analysis/green_access.py \
  --barrios data/<istac-barrios-2018>.geojson \
  --names-csv data/<CL_BARRIOS_ES70_2024>/codes.csv \
  --catastro data/A.ES.SDGC.BU.<code>.zip \
  --proposal-file analysis/proposals/proposed_parks.geojson \
  --strip-widths 30,40,50 \
  --outdir green_access_out
```

The run downloads the OpenStreetMap walking network, parks and playgrounds, so it needs internet access
and takes several minutes. Outputs go to `green_access_out/` (ignored by git); copy the ones worth keeping
into `analysis/results/<date>/`. See `data/README.md` for the input files.

## Data and licences

Walking network, parks and playgrounds: © OpenStreetMap contributors (ODbL).
Buildings and homes: Dirección General del Catastro, INSPIRE buildings.
Barrio boundaries and names: ISTAC (Instituto Canario de Estadística).
