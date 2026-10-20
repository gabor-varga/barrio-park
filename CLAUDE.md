# Project notes for Claude

Neighbourhood campaign (Las Palmas de Gran Canaria) for a public park and playground next to
Ctra. de Chile and C/ Dr. Alfonso Chiscano Díaz, on land of the municipal Las Palmeras Golf.
The repo owner lives at Ctra. de Chile 37 and leads the campaign.

## Conventions

- Neighbour-facing material (flyer, website) is in Spanish. The website also has an English page
  (`docs/en/`), and the study is bilingual (EN + ES); keep both language versions in sync.
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
- `analysis/new_buildings.geojson`: three buildings north of the pocket, finished in the past year or
  under construction, not yet in the Catastro (traced from the owner's marks, ±5 m). `--new-buildings`
  draws them on the maps only; their homes are not counted (dwellings unknown).
- The study maps draw one dot per home (Catastro dwellings, rounded as a running total so split parcels
  keep their homes), in a grid filling each building's footprint.
- `study/`: markdown export of the study. The live, editable study is a Claude Docs document:
  https://claude.ai/code/artifact/e5e4bef3-f24d-4a6c-831b-1d688d774c70
- `flyer/flyer.html` + `.pdf`: the A4 poster (headline, the ask with the website's map, 1,145 m →
  258 m, sign-up QR), for building entrances (ask the community first) and shops; ~50–60 copies.
  `flyer/flyer-a5.html` + `flyer-a5.pdf` / `flyer-a5-2up.pdf` (two per A4 sheet): the letterbox flyer,
  black and white only (black ink, no filled areas: ~820 copies must be cheap), no map, bigger QR.
  Fonts are bundled in `flyer/fonts/` (Archivo, OFL). `scripts/flyer.py <url> --pdf` fills in the QR codes, printed address
  and map (copied from `docs/index.html`, colours fixed for print) and prints all three PDFs with
  WeasyPrint. No meeting date or contact on it: the sign-up form covers both. (Older design canvas:
  https://claude.ai/artifact/Fd2qSGw1sqkF3BFhnmQQNW.)
- `docs/index.html` (Spanish) and `docs/en/index.html` (English, linked by a switch at the top of each):
  campaign website, live at https://parque.barrionegrin.es/ (custom domain bought at Piensa Solutions;
  DNS: CNAME `parque` → `gabor-varga.github.io`; the github.io address redirects there) (older copy at
  https://claude.ai/artifact/Q3spwuWrBJaGMjUxUEUUEF). Its "Apúntate aquí" button opens the Tally sign-up
  form (https://tally.so/r/VpEBNv: name, phone, street, consent); the meeting date says "por anunciar".
  Campaign email: parque@barrionegrin.es (Piensa mailbox, forwarded to the owner's Gmail; MX
  mx.buzondecorreo.com, SPF set), shown in the sign-up section, the data note and the footer of both pages.
- `docs/site.css` is shared by both pages. `analysis/site_map.py` draws the orientation map (inline SVG
  themed by the `--map-*` CSS variables; `--lang es|en`) from a run's `layers.gpkg` into each page between
  the `<!-- mapa:start/end -->` markers. `docs/og.png` / `og-en.png` (link previews) are screenshots of
  each page's hero.
- `.github/workflows/pages.yml` deploys the site on push to `master`. `scripts/build-site.sh` copies
  `docs/` and renders `study/*.md` with pandoc into `estudio/` (ES) and `estudio/en.html` (EN), so the
  markdown study is what the public sees: keep it in sync with the Claude Docs version.

## Open items

- [x] Sign-up form (Tally, https://tally.so/r/VpEBNv), linked from `docs/index.html`.
- [ ] Meeting date and place, contact; fill them into the site and flyer.
- [x] QR code on the flyer, pointing to https://parque.barrionegrin.es/.
- [ ] Custom domain live: CNAME + TXT at Piensa Solutions, domain verified on GitHub, repo Pages custom
      domain set, HTTPS enforced. Check the QR works before printing.
- [ ] Number of homes in the three new buildings (`analysis/new_buildings.geojson`), to count them in the pocket.
- [ ] Check in the Catastro who owns plot A.
- [ ] Household size from INE (census section), including the share of children under 15.
- [ ] Verify on the ground: missing paths, whether the nearest playgrounds are public.
- [x] Draft the collective petition (`peticion/peticion.html`, addressed to the Alcaldía; asks for the
      park on A + B, entrances from both streets, plot A ownership + Plan General classification, a
      meeting) and the signature sheet (`peticion/hoja-firmas.html`, A4 landscape, 14 per sheet: name,
      DNI/NIE, street and number, signature; GDPR notice). PDFs via `scripts/html2pdf.py`.
- The files in `peticion/` are blank templates with [BRACKETS]. The filled-in copies (owner's name,
  NIE, email) and their PDFs live in `peticion/privado/`, which is git-ignored: the repo is public.
  Never copy personal details into tracked files; edit and print from `privado/`. The campaign
  email (parque@barrionegrin.es) is public and is already in the templates (contact email, GDPR notice).
  The private `peticion.html` still needs the representative's name and DNI.
- [ ] File the request for public information, drafted in `peticion/solicitud-informacion.html` (filled-in copy in `privado/`) (Ley 19/2013,
      Ley 12/2014 de Canarias; to the Ayuntamiento and the IMD): the 2022 concession contract and pliegos
      (recovery and compensation clauses), the limits of the concession (does it include plot A?), who owns
      plot A, the Plan General classification, spending since 2015 and fees received since 2022. One person
      files it online; answer due within a month. No answer = refusal: claim to the Comisionado de
      Transparencia y Acceso a la Información Pública de Canarias.
- [ ] Contact the residents' associations, the Junta Municipal de Distrito, and council groups for a moción.
      Research done (20 Oct 2026) in `privado/contactos.md` (git-ignored): formal channels (Registro de
      Participación de la Ciudadanía unlocks district board and Pleno rights), AVETAL / Tarjas and the
      Nueva Ciudad Alta conflict ("parks without expropriation"), councillors, groups, VOPIA, UNICEF, media.
      Las Torres = Ciudad Alta district (chair: Betsaida González); Guanarteme = Isleta-Puerto-Guanarteme.
