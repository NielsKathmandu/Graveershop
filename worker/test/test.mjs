// Test van de Worker met nagebootste voorraad.json en Resend. Uitvoeren: node worker/test/test.mjs
import assert from "node:assert";
const { default: worker } = await import("../src/index.js");

const env = {
  SITE_URL: "https://nielskathmandu.github.io/Graveershop", PINCODE: "246810",
  RESEND_API_KEY: "re_test", MAIL_VAN: "Graveerwerk <x@kathmandu.nl>", MAIL_ORDERS: "niels@kathmandu.nl",
};
const verzoeken = [];
let resendWeigertKlant = false;
globalThis.fetch = async (url, opt = {}) => {
  url = String(url); verzoeken.push({ url, opt });
  if (url.endsWith("/voorraad.json"))
    return new Response(JSON.stringify({ producten: [{ bestelcode: "029031", naam: "Stanley Legendary 0,59L Black", prijs: 54.95, voorraad: 4 }] }));
  if (url.includes("api.resend.com")) {
    if (resendWeigertKlant && JSON.parse(opt.body).to !== env.MAIL_ORDERS) return new Response('{"message":"testing emails only to own address"}', { status: 403 });
    return new Response(JSON.stringify({ id: "m1" }));
  }
  throw new Error("onverwacht verzoek " + url);
};
const vraag = (pad, body) => worker.fetch(new Request("https://graveerwerk.example.workers.dev" + pad, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), env);
const mails = () => verzoeken.filter(v => v.url.includes("resend")).map(v => JSON.parse(v.opt.body));

const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="83mm" height="60mm"><path d="M0 0L1 1Z"/></svg>';
const png = "data:image/png;base64,iVBORw0KGgo=";
const klant = { naam: "Sanne Test", email: "sanne@example.com", telefoon: "0612345678" };
const regel = { code: "029031", n: 2, gravure: { svg, voorbeeld: png, formaat: "83 × 60 mm", config: { r1: "Sanne", r2: "", lettertype: "Strak", symbool: "Ster" } } };
const order = extra => ({ pin: "246810", winkel: "Amsterdam", medewerker: "Dries", bonnummer: "123456", klant, regels: [regel], ...extra });

// 1. pincode
let r = await vraag("/pin", { pin: "0000" }); assert.equal(r.status, 403);
r = await vraag("/pin", { pin: "246810" }); assert.equal(r.status, 200);
assert.equal(r.headers.get("Access-Control-Allow-Origin"), "https://nielskathmandu.github.io");

// 2. weigeringen
assert.equal((await vraag("/winkelorder", order({ pin: "1" }))).status, 403, "verkeerde pin");
assert.equal((await vraag("/winkelorder", order({ bonnummer: "" }))).status, 400, "bonnummer verplicht");
assert.equal((await vraag("/winkelorder", order({ winkel: "Rotterdam" }))).status, 400, "onbekende winkel");
assert.equal((await vraag("/winkelorder", order({ regels: [{ code: "029031", n: 1 }] }))).status, 400, "ontwerp verplicht");
assert.equal((await vraag("/winkelorder", order({ klant: { ...klant, email: "x" } }))).status, 400, "e-mail");
assert.equal(mails().length, 0);

// 3. geldige order uit Amsterdam
r = await vraag("/winkelorder", order());
const d = await r.json();
assert.equal(r.status, 200, JSON.stringify(d));
assert.match(d.nummer, /^A-\d{4}-[A-Z0-9]{3}$/);
assert.equal(d.klantmail, "sanne@example.com");
const [om, km] = mails();
assert.equal(om.to, "niels@kathmandu.nl"); assert.equal(om.reply_to, "sanne@example.com");
assert.match(om.subject, /^Graveerorder A-.* – Amsterdam – Sanne Test – 2 st\.$/);
assert.match(om.html, /terug naar Amsterdam/); assert.match(om.html, /123456/); assert.match(om.html, /Dries/);
assert.deepEqual(om.attachments.map(a => a.filename.replace(d.nummer, "NR")), ["NR_regel1_029031_xtool.svg", "NR_regel1_029031_voorbeeld.png"]);
assert.equal(Buffer.from(om.attachments[0].content, "base64").toString(), svg);
assert.equal(km.to, "sanne@example.com"); assert.match(km.html, /Haarlemmerstraat 123/); assert.equal(km.attachments.length, 1);

// 4. Utrecht + Resend weigert klantadres -> voorbeeld naar MAIL_ORDERS
resendWeigertKlant = true;
const voor = mails().length;
r = await vraag("/winkelorder", order({ winkel: "Utrecht" }));
const u = await r.json();
assert.match(u.nummer, /^U-/); assert.equal(u.klantmail, null);
const nieuw = mails().slice(voor);
assert.equal(nieuw.at(-1).to, "niels@kathmandu.nl"); assert.match(nieuw.at(-1).subject, /Voorbeeld klantmail/); assert.match(nieuw.at(-1).html, /Oudegracht 256-264/);

// 5. zonder PINCODE-variabele
delete env.PINCODE;
r = await vraag("/pin", { pin: "246810" }); assert.equal(r.status, 500);

console.log("Alle tests geslaagd. Voorbeeld ordernummer:", d.nummer);
