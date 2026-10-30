// Test van de Worker met nagebootste Buckaroo, voorraad.json en Resend. Uitvoeren: node worker/test/test.mjs
import { createHash, webcrypto } from "node:crypto";
import assert from "node:assert";

// Cloudflare ondersteunt MD5 in crypto.subtle; Node niet. Nabootsen:
const echteDigest = crypto.subtle.digest.bind(crypto.subtle);
crypto.subtle.digest = async (alg, data) =>
  alg === "MD5" ? createHash("md5").update(Buffer.from(data)).digest().buffer : echteDigest(alg, data);
// globalThis.crypto is in Node al webcrypto; digest hierboven is aangepast

const { default: worker } = await import("../src/index.js");

const kv = new Map();
const env = {
  SITE_URL: "https://nielskathmandu.github.io/Graveershop",
  BUCKAROO_TEST: "1", BUCKAROO_WEBSITE_KEY: "WEBKEY", BUCKAROO_SECRET_KEY: "GEHEIM",
  RESEND_API_KEY: "re_test", TESTMODUS: "1", MAIL_VAN: "Graveerwerk <x@kathmandu.nl>", MAIL_ORDERS: "webshop@kathmandu.nl",
  ORDERS: { get: async k => kv.get(k) ?? null, put: async (k, v) => void kv.set(k, v) },
};
const verzoeken = [];
globalThis.fetch = async (url, opt = {}) => {
  url = String(url); verzoeken.push({ url, opt });
  if (url.endsWith("/voorraad.json"))
    return new Response(JSON.stringify({ producten: [{ bestelcode: "029031", naam: "Stanley Legendary 0,59L Black", prijs: 54.95, voorraad: 4 }] }));
  if (url.includes("buckaroo.nl/json/Transaction/Status/"))
    return new Response(JSON.stringify({ Key: "TX1", Status: { Code: { Code: 190 } }, AmountDebit: 117.40 }));
  if (url.includes("buckaroo.nl/json/Transaction"))
    return new Response(JSON.stringify({ Key: "TX1", RequiredAction: { RedirectURL: "https://testcheckout.buckaroo.nl/html/redirect?x=1" } }));
  if (url.includes("api.resend.com")) return new Response(JSON.stringify({ id: "m1" }));
  throw new Error("onverwacht verzoek " + url);
};

const klant = { naam: "Sanne Test", email: "sanne@example.com", straat: "Oudegracht 1", postcode: "3511 AA", plaats: "Utrecht", land: "Nederland" };
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="83mm" height="60mm"><path d="M0 0L1 1Z"/></svg>';
const png = "data:image/png;base64,iVBORw0KGgo=";
const vraag = (pad, body, methode = "POST", type = "application/json") =>
  worker.fetch(new Request("https://graveerwerk.example.workers.dev" + pad, { method: methode, headers: { "Content-Type": type }, body }), env);

// 1. prijs uit de browser wordt genegeerd; te veel stuks wordt geweigerd
let r = await vraag("/bestelling", JSON.stringify({ klant, akkoord: true, regels: [{ code: "029031", n: 5 }] }));
assert.equal(r.status, 409, "voorraadcontrole");

// 2. geldige bestelling: 1 zonder + 1 met gravure = 54,95 + 54,95 + 7,50 = 117,40 (geen verzendkosten)
r = await vraag("/bestelling", JSON.stringify({ klant, akkoord: true, regels: [
  { code: "029031", n: 1, prijs: 0.01 },
  { code: "029031", n: 1, gravure: { svg, voorbeeld: png, formaat: "83 × 60 mm", config: { r1: "Sanne", r2: "", lettertype: "Strak", symbool: "Ster" } } },
] }));
const d = await r.json();
assert.equal(r.status, 200, JSON.stringify(d));
assert.ok(d.doorsturen.startsWith("https://testcheckout.buckaroo.nl"));
const tx = verzoeken.find(v => v.url === "https://testcheckout.buckaroo.nl/json/Transaction");
const body = JSON.parse(tx.opt.body);
assert.equal(body.AmountDebit, 117.4, "bedrag server-side berekend");
assert.match(tx.opt.headers.Authorization, /^hmac WEBKEY:[A-Za-z0-9+/]{43}=:[0-9a-f]{32}:\d{10}$/, "HMAC-header");
assert.equal(r.headers.get("Access-Control-Allow-Origin"), "https://nielskathmandu.github.io");

// 3. push van Buckaroo -> status opvragen -> ordermail met bijlagen + bevestiging
r = await vraag("/push", JSON.stringify({ Transaction: { Key: "TX1", Invoice: d.nummer, Status: { Code: { Code: 190 } } } }));
assert.equal(r.status, 200);
const mails = verzoeken.filter(v => v.url.includes("resend")).map(v => JSON.parse(v.opt.body));
assert.equal(mails.length, 2, "ordermail + bevestiging");
const order = mails.find(m => m.to === "webshop@kathmandu.nl");
assert.deepEqual(order.attachments.map(a => a.filename.replace(d.nummer, "NR")), ["NR_regel2_029031_xtool.svg", "NR_regel2_029031_voorbeeld.png"]);
assert.equal(Buffer.from(order.attachments[0].content, "base64").toString(), svg);
assert.match(order.html, /<b>Graveren<\/b>/, "gravure als losse regel"); assert.ok(order.html.includes("7,50"), "graveerprijs apart");
assert.equal(JSON.parse(kv.get(d.nummer)).status, "betaald");

// 4. dubbele push mailt niet opnieuw
await vraag("/push", JSON.stringify({ Transaction: { Key: "TX1", Invoice: d.nummer } }));
assert.equal(verzoeken.filter(v => v.url.includes("resend")).length, 2);

// 5. terugkeer van de klant
r = await vraag("/terug", "brq_statuscode=190&brq_invoicenumber=X", "POST", "application/x-www-form-urlencoded");
assert.equal(r.status, 303); assert.equal(r.headers.get("Location"), env.SITE_URL + "/#bedankt");
r = await vraag("/terug", "brq_statuscode=890", "POST", "application/x-www-form-urlencoded");
assert.equal(r.headers.get("Location"), env.SITE_URL + "/#betaling-mislukt");

// 6. testbestelling zonder betaling: ordermail met TEST-label + voorbeeld-klantmail naar MAIL_ORDERS, geen Buckaroo
const voor = verzoeken.length;
r = await vraag("/testbestelling", JSON.stringify({ klant, akkoord: true, regels: [{ code: "029031", n: 1, gravure: { svg, voorbeeld: png, formaat: "83 × 60 mm", config: { r1: "Test" } } }] }));
const td = await r.json();
assert.equal(r.status, 200, JSON.stringify(td)); assert.ok(td.nummer.startsWith("TEST-"));
const nieuw = verzoeken.slice(voor);
assert.ok(!nieuw.some(v => v.url.includes("buckaroo")), "geen Buckaroo bij testbestelling");
const tm = nieuw.filter(v => v.url.includes("resend")).map(v => JSON.parse(v.opt.body));
assert.equal(tm.length, 2); assert.equal(tm[0].to, "webshop@kathmandu.nl"); assert.equal(tm[1].to, "sanne@example.com", "klantmail naar ingevuld adres");
assert.equal(td.klantmail, "sanne@example.com");
assert.match(tm[0].subject, /^TESTBESTELLING TEST-/); assert.equal(tm[0].attachments.length, 2);
env.TESTMODUS = "0";
r = await vraag("/testbestelling", JSON.stringify({ klant, akkoord: true, regels: [{ code: "029031", n: 1 }] }));
assert.equal(r.status, 403, "testmodus uit");

console.log("Alle tests geslaagd. Ordermail-onderwerp:", order.subject);

// 7. cadeauverpakking: aparte regel van 2,95, ontvanger verplicht, melding in ordermail
env.TESTMODUS = "1";
r = await vraag("/testbestelling", JSON.stringify({ klant, akkoord: true, cadeau: true, regels: [{ code: "029031", n: 1 }] }));
assert.equal(r.status, 400, "ontvanger verplicht bij cadeau");
const v7 = verzoeken.length;
r = await vraag("/testbestelling", JSON.stringify({ klant: { ...klant, ontvanger: "Piet Cadeau" }, akkoord: true, cadeau: true, regels: [{ code: "029031", n: 1 }] }));
const cd = await r.json();
assert.equal(cd.totaal, 57.9, "54,95 + 2,95 cadeau, gratis verzending");
const cm = verzoeken.slice(v7).filter(v => v.url.includes("resend")).map(v => JSON.parse(v.opt.body));
assert.match(cm[0].html, /CADEAUVERPAKKING/); assert.match(cm[0].html, /Piet Cadeau/); assert.match(cm[1].html, /Cadeauverpakking/);
console.log("Cadeauverpakking ok");

// 9. Resend weigert klantadres (domein niet geverifieerd) -> voorbeeld naar MAIL_ORDERS
const echteFetch = globalThis.fetch;
globalThis.fetch = async (url, opt = {}) => (String(url).includes("api.resend.com") && JSON.parse(opt.body).to === "sanne@example.com")
  ? new Response('{"message":"You can only send testing emails to your own email address"}', { status: 403 }) : echteFetch(url, opt);
const v9 = verzoeken.length;
r = await vraag("/testbestelling", JSON.stringify({ klant, akkoord: true, regels: [{ code: "029031", n: 1 }] }));
const d9 = await r.json();
assert.equal(d9.klantmail, null);
const m9 = verzoeken.slice(v9).filter(v => v.url.includes("resend")).map(v => JSON.parse(v.opt.body));
assert.equal(m9.at(-1).to, "webshop@kathmandu.nl"); assert.match(m9.at(-1).subject, /Voorbeeld klantmail/);
console.log("Klantmail-terugval ok");
