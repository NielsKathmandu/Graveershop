# Mailserver en afrekenen (Cloudflare Worker): zo zet je het aan

De shop staat op GitHub Pages en kan zelf geen mail versturen of geheime sleutels bewaren. Dat doet deze kleine server, een Cloudflare Worker. Gratis, zonder abonnement, alles op naam van Kathmandu.

## Stap 1: Resend (versturen van de mails)

1. Maak een gratis account op **resend.com** met **niels@kathmandu.nl**.
2. Ga naar **API Keys**, kies **Create API Key** (rechten: Sending access) en kopieer de sleutel. Die begint met `re_`.

Testen kan meteen. Zolang het domein nog niet geverifieerd is, verstuurt Resend alleen naar het adres van je eigen account (niels@kathmandu.nl), vanaf `onboarding@resend.dev`. Voor de livegang laat je onder **Domains** kathmandu.nl verifiëren. Resend geeft dan een paar DNS-records op die bij jullie domeinbeheer worden toegevoegd.

## Stap 2: Cloudflare (de server)

1. Maak een gratis account op **dash.cloudflare.com**.
2. **Opslag:** ga naar **Storage & Databases → KV** en maak een namespace aan met de naam `ORDERS`.
3. **Worker:** ga naar **Compute (Workers) → Create → Start with Hello World**. Noem hem `graveerwerk-afrekenen` en kies **Deploy**.
4. Kies **Edit code**. Vervang alle code door de inhoud van `worker/src/index.js` uit deze repository en kies **Deploy**.
5. Ga naar **Settings → Bindings → Add → KV namespace**. Vul bij Variable name `ORDERS` in en kies de namespace `ORDERS`.
6. Ga naar **Settings → Variables and Secrets** en voeg deze toe:

| Naam | Type | Waarde |
|---|---|---|
| `SITE_URL` | Text | `https://nielskathmandu.github.io/Graveershop` |
| `MAIL_ORDERS` | Text | `niels@kathmandu.nl` |
| `MAIL_VAN` | Text | `Kathmandu Graveerwerk <onboarding@resend.dev>`. Na het verifiëren van het domein wordt dit `Kathmandu Graveerwerk <graveerwerk@kathmandu.nl>`. |
| `TESTMODUS` | Text | `1` |
| `BUCKAROO_TEST` | Text | `1` |
| `RESEND_API_KEY` | Secret | de sleutel uit stap 1 |

7. Kopieer het adres van de Worker, bijvoorbeeld `https://graveerwerk-afrekenen.<naam>.workers.dev`, en geef het aan Claude. Dat wordt in de shop gezet bij `AFREKEN_URL`.

Daarna werkt de **Testbetaling**: de ordermail met SVG, voorbeeldafbeelding en logo komt binnen op niels@kathmandu.nl, samen met een voorbeeld van de bevestiging die de klant krijgt.

## Stap 3 (later): Buckaroo

1. Maak in Buckaroo Plaza een extra website aan voor de graveershop en kopieer de **website key** en **secret key**. Begin in de testomgeving.
2. Voeg ze toe als Secret: `BUCKAROO_WEBSITE_KEY` en `BUCKAROO_SECRET_KEY`.
3. In de shop zet Claude `BUCKAROO_ACTIEF` op `true`. Dan verschijnt de knop **Betalen** in plaats van **Testbetaling**.
4. Na het testen: zet `BUCKAROO_TEST` op `0` en `TESTMODUS` op `0`.

## Automatische test

`node worker/test/test.mjs` controleert de bestelling, de prijsberekening, de Buckaroo-ondertekening, de ordermail met bijlagen, de testbestelling en de terugkeer naar de shop, met nagebootste diensten.
