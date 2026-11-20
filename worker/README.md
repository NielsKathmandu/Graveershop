# Server voor het graveerweekend (Cloudflare Worker)

De personeelstool staat op GitHub Pages. Deze kleine server controleert de pincode en verstuurt de mails. Gratis, zonder abonnement.

## Instellen in Cloudflare

1. Open de Worker **graveerwerk-afrekenen** en kies **Edit code**. Vervang alle code door de inhoud van `worker/src/index.js` en kies **Deploy**.
2. Ga naar **Settings → Variables and Secrets** en zorg dat deze er staan:

| Naam | Type | Waarde |
|---|---|---|
| `SITE_URL` | Text | `https://nielskathmandu.github.io/Graveershop` |
| `MAIL_ORDERS` | Text | `niels@kathmandu.nl` (na het testen: `servicedesk@kathmandu.nl`) |
| `MAIL_VAN` | Text | `Kathmandu Graveerwerk <onboarding@resend.dev>` |
| `RESEND_API_KEY` | Secret | de sleutel van resend.com |
| `PINCODE` | Secret | de pincode voor de tablets, bij voorkeur 6 cijfers |

De oude variabelen `TESTMODUS` en `BUCKAROO_TEST` en de KV-koppeling `ORDERS` worden niet meer gebruikt en mogen weg.

## Mail

De klant krijgt geen mail. Het e-mailadres van de klant staat in de ordermail; antwoorden op de ordermail gaat naar de klant, bijvoorbeeld om te melden dat de fles klaarligt.

## Automatische test

`node worker/test/test.mjs` controleert de pincode, de bestelling, de ordermail met bijlagen en de bevestiging, met nagebootste diensten.
