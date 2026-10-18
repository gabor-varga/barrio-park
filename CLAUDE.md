# Project notes for Claude

Neighbourhood campaign (Las Palmas de Gran Canaria) for a public park and playground next to
Ctra. de Chile and C/ Dr. Alfonso Chiscano Díaz, on land of the municipal Las Palmeras Golf.
The repo owner lives at Ctra. de Chile 37 and leads the campaign.

## Conventions

- Neighbour-facing material (flyer, website) is in Spanish. The study is bilingual (EN + ES); keep both
  versions in sync.
- Always name both streets: Carretera de Chile **and** Calle Doctor Alfonso Chiscano Díaz.
- Use walking distances only. No straight-line ("as the crow flies") distances: they confuse readers.
- The city is a UNICEF *Ciudad Amiga de la Infancia* (since 2021, renewed April 2026), not a
  "capital of children".
- Paths across the golf course were considered and dropped. The ask is a park on the land itself.
- Golf "public use" means open to anyone who pays the green fee; it is not a public park.

## Current proposal (the ask)

- **Plot A**: abandoned plot (failed wellness-centre project) next to the golf car park, 0.38 ha.
  Not part of the course as mapped. Ownership unknown: check in the Catastro.
- **Strip B**: strip of the course along C/ Dr. Alfonso Chiscano Díaz, 0.55 ha (0.53 ha on the course,
  about 6%). A low fence separates it from the pavement. The tall ball-stop net of the driving range
  runs along its east side. It holds four small practice putting greens. It cannot extend south: the
  course's irrigation pond is there.
- The script's automatic 30/40/50 m strips cross that pond, so they are not feasible as drawn.

## Key numbers (run of 2026-10-18, `analysis/results/2026-10-18/`)

| Measure | Value |
| --- | --- |
| Pocket | 26 buildings, 816 homes, ~2,040 residents (2.5 per home, placeholder) |
| Median walk to park / playground | 1,145 m / 877 m (worst building 1,247 m / 1,124 m) |
| From Ctra. de Chile 37 | park 1,100 m, playground 921 m; 88.7% / 87.7% of city residents are closer |
| Las Torres (barrio) | 79% within 500 m of a park, rank 74 of 106 (1 = worst), median 154 m |
| Plot A only | pocket median 369 m, 74% within 500 m, ~5,200 people citywide gain a park ≤ 500 m |
| Strip B only | 258 m, 100%, ~4,900 |
| A + B (the ask) | 0.93 ha, 258 m, 100% of pocket within 500 m, ~6,400 people citywide |
| Whole course | 220 m, 100%; city share within 500 m 77.4% → 79.4% |

## Where things live

- `analysis/green_access.py`: the whole analysis (OSM network via osmnx, EPSG:32628, multi-source
  Dijkstra). Streets are densified every 20 m (`DENSIFY_M`); a park's entrances are street points within
  30 m of its edge. `--proposal-file` evaluates hand-drawn areas and writes `map_proposals.png` plus
  `entrances` and `proposals` layers in `layers.gpkg`.
- `analysis/proposals/proposed_parks.geojson`: plot A + strip B (B drawn to touch C/ Dr. Alfonso
  Chiscano Díaz). This is the file the 2026-10-18 run used.
- `study/`: markdown export of the study. The live, editable study is a Claude Docs document:
  https://claude.ai/code/artifact/e5e4bef3-f24d-4a6c-831b-1d688d774c70
- `flyer/flyer.html`: static copy of the A4 flyer (Claude design canvas:
  https://claude.ai/artifact/Fd2qSGw1sqkF3BFhnmQQNW).
- `docs/index.html`: campaign website, live at https://gabor-varga.github.io/barrio-park/ (older copy at
  https://claude.ai/artifact/Q3spwuWrBJaGMjUxUEUUEF). Its "Apúntate" button is disabled until a sign-up
  form exists; the meeting date says "por anunciar".
- `.github/workflows/pages.yml` deploys the site on push to `master`. `scripts/build-site.sh` copies
  `docs/` and renders `study/*.md` with pandoc into `estudio/` (ES) and `estudio/en.html` (EN), so the
  markdown study is what the public sees: keep it in sync with the Claude Docs version.

## Open items

- [ ] Sign-up form (Google Forms or Tally) with the data-protection notice; link it from `docs/index.html`.
- [ ] Meeting date and place, contact; fill them into the site and flyer.
- [ ] QR code on the flyer pointing to https://gabor-varga.github.io/barrio-park/.
- [ ] Check in the Catastro who owns plot A.
- [ ] Household size from INE (census section), including the share of children under 15.
- [ ] Verify on the ground: missing paths, whether the nearest playgrounds are public.
- [ ] Draft the collective petition (derecho de petición, LO 4/2001) with a signature sheet
      (name, DNI, address, signature) and the information request (concession contract,
      Plan General classification, ownership of plot A).
- [ ] Contact the residents' association, the Junta Municipal de Distrito, and council groups for a moción.
