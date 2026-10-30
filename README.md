# Kathmandu Graveershop (proof of concept)

Webshop voor gegraveerde Stanley- en Klean Kanteen-flessen. De voorraad komt uit kathmandu.nl.

## Onderdelen

1. `voorraad_sync.py` leest de merkpagina's en productpagina's van kathmandu.nl. Het telt de winkelvoorraad van Amsterdam en Utrecht op, slaat losse doppen over, downloadt de productfoto's naar `docs/img/` en schrijft `docs/voorraad.json`.
2. `.github/workflows/voorraad.yml` draait het script elk uur tussen 07:17 en 22:17. Handmatig starten kan via **Actions → Voorraad bijwerken → Run workflow**.
3. `docs/index.html` is de shop met graveer-configurator. De pagina leest `voorraad.json`. Elke werkbon levert een SVG op ware grootte in millimeters, met de tekst al omgezet naar paden, voor xTool Creative Space.
4. `tests/test_parser.py` controleert de parser met `python tests/test_parser.py`.

## Shop online

Ga naar **Settings → Pages → Build and deployment**. Kies daar **Deploy from a branch**, branch `main` en map `/docs`.
