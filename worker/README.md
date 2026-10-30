# Afrekenen via Buckaroo: zo zet je het aan

De shop zelf staat op GitHub Pages. Die kan geen geheime sleutels bewaren. Daarom loopt het afrekenen via een kleine Cloudflare Worker. Die is gratis (tot 100.000 verzoeken per dag) en vraagt geen abonnement.

## Wat je nodig hebt

1. **Buckaroo:** in Buckaroo Plaza een extra website aanmaken voor de graveershop, onder het bestaande account van kathmandu.nl. Daarvan heb je de **website key** en de **secret key** nodig. Begin met de testomgeving.
2. **Cloudflare:** een gratis account op cloudflare.com.
3. **Resend** (resend.com): een gratis account voor het versturen van de mails, tot 3.000 per maand. Laat het domein kathmandu.nl verifiëren via de DNS-records die Resend opgeeft, zodat mails van graveerwerk@kathmandu.nl komen.

## Installeren (eenmalig, ongeveer 15 minuten)

```bash
cd worker
npx wrangler login                          # inloggen bij Cloudflare
npx wrangler kv namespace create ORDERS     # zet het id in wrangler.toml
npx wrangler secret put BUCKAROO_WEBSITE_KEY
npx wrangler secret put BUCKAROO_SECRET_KEY
npx wrangler secret put RESEND_API_KEY
npx wrangler deploy                         # geeft een adres zoals https://graveerwerk-afrekenen.<naam>.workers.dev
```

Zet dat adres daarna in de shop: in `index.html` bij `const AFREKEN_URL=""`.

## Testen

- `BUCKAROO_TEST = "1"` in `wrangler.toml` gebruikt de testomgeving van Buckaroo. Daar betaal je met testgegevens, er wordt niets echt afgeschreven.
- Doe een paar testbestellingen en controleer de ordermail op niels@kathmandu.nl. Daarin zitten per gravure de SVG voor xTool, de voorbeeldafbeelding en eventueel het logo.
- Werkt alles, zet dan `BUCKAROO_TEST = "0"` en voer `npx wrangler deploy` opnieuw uit.

## Automatische test

`node worker/test/test.mjs` controleert de bestelling, de prijsberekening, de Buckaroo-ondertekening, de ordermail met bijlagen en de terugkeer naar de shop, met nagebootste diensten.
