/**
 * Kathmandu Graveerwerk – afrekenen via Buckaroo en ordermail.
 *
 * Draait als Cloudflare Worker (gratis abonnement volstaat). Routes:
 *   POST /bestelling  winkelmand + klantgegevens -> prijzen narekenen -> Buckaroo-betaling starten
 *   POST /push        Buckaroo meldt een betaling -> status controleren -> ordermail + bevestiging
 *   GET|POST /terug   klant komt terug van Buckaroo -> door naar de shop (bedankt of mislukt)
 *
 * Instellingen (wrangler.toml [vars] en `wrangler secret put`):
 *   SITE_URL                 https://nielskathmandu.github.io/Graveershop   (zonder slash)
 *   BUCKAROO_TEST            "1" = testomgeving, "0" = live
 *   BUCKAROO_WEBSITE_KEY     (geheim) uit Buckaroo Plaza
 *   BUCKAROO_SECRET_KEY      (geheim) uit Buckaroo Plaza
 *   RESEND_API_KEY           (geheim) voor het versturen van e-mail via resend.com
 *   MAIL_VAN                 bijv. "Kathmandu Graveerwerk <graveerwerk@kathmandu.nl>"
 *   MAIL_ORDERS              adres waar orders binnenkomen: niels@kathmandu.nl
 *   KV-binding ORDERS        opslag van openstaande bestellingen
 */

const GRAVEERPRIJS = n => (n >= 25 ? 4 : n >= 10 ? 5 : 7.5);
const VERZENDKOSTEN = sub => (sub >= 30 ? 0 : 4.95);
const MAX_SNEL = 3; // tot en met 3 stuks: binnen 3 werkdagen verzonden

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));
      if (url.pathname === "/bestelling" && request.method === "POST") return cors(env, await bestelling(request, env, url));
      if (url.pathname === "/push" && request.method === "POST") return await push(request, env);
      if (url.pathname === "/terug") return await terug(request, env);
      return new Response("Kathmandu Graveerwerk", { status: 200 });
    } catch (e) {
      console.error(e);
      return cors(env, json({ fout: "Er ging iets mis bij het afrekenen. Probeer het later opnieuw." }, 500));
    }
  },
};

/* ---------------- bestelling starten ---------------- */
async function bestelling(request, env, url) {
  const invoer = await request.json();
  const k = invoer.klant || {};
  for (const veld of ["naam", "email", "straat", "postcode", "plaats", "land"]) {
    if (!String(k[veld] || "").trim()) return json({ fout: `Vul je ${veld} in.` }, 400);
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(k.email)) return json({ fout: "Dit e-mailadres klopt niet." }, 400);
  if (!invoer.akkoord) return json({ fout: "Ga akkoord met de algemene voorwaarden om te bestellen." }, 400);
  if (!Array.isArray(invoer.regels) || !invoer.regels.length) return json({ fout: "Je winkelmand is leeg." }, 400);

  // Prijzen en voorraad nooit uit de browser overnemen: zelf ophalen
  const voorraad = await (await fetch(`${env.SITE_URL}/voorraad.json`, { cf: { cacheTtl: 300 } })).json();
  const perCode = Object.fromEntries(voorraad.producten.map(p => [p.bestelcode, p]));
  const inMand = {};
  const regels = [];
  for (const r of invoer.regels) {
    const p = perCode[r.code];
    const n = Math.floor(Number(r.n));
    if (!p || !(n >= 1)) return json({ fout: "Een artikel in je winkelmand bestaat niet meer. Verwijder het en probeer opnieuw." }, 400);
    inMand[r.code] = (inMand[r.code] || 0) + n;
    if (inMand[r.code] > p.voorraad) return json({ fout: `Van ${p.naam} zijn er nog ${p.voorraad} op voorraad.` }, 409);
    const gravure = r.gravure && r.gravure.svg ? r.gravure : null;
    if (gravure && gravure.svg.length > 2e6) return json({ fout: "Een gravure is te groot. Gebruik een eenvoudiger logo." }, 400);
    const graveren = gravure ? GRAVEERPRIJS(n) : 0;
    regels.push({ code: r.code, naam: p.naam, n, stukprijs: p.prijs, graveren, bedrag: round(n * (p.prijs + graveren)), gravure });
  }
  const subtotaal = round(regels.reduce((t, r) => t + r.bedrag, 0));
  const verzending = VERZENDKOSTEN(subtotaal);
  const totaal = round(subtotaal + verzending);
  const stuks = regels.reduce((t, r) => t + r.n, 0);

  const datum = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const nummer = `KG-${datum}-${crypto.randomUUID().slice(0, 6).toUpperCase()}`;
  const order = {
    nummer, aangemaakt: new Date().toISOString(), status: "open",
    klant: pick(k, ["naam", "bedrijf", "email", "telefoon", "straat", "postcode", "plaats", "land", "opmerking"]),
    regels, subtotaal, verzending, totaal, stuks, levertijdInOverleg: stuks > MAX_SNEL,
  };
  await env.ORDERS.put(nummer, JSON.stringify(order), { expirationTtl: 60 * 60 * 24 * 30 });

  const eigen = `${url.protocol}//${url.host}`;
  const transactie = {
    Currency: "EUR",
    AmountDebit: totaal,
    Invoice: nummer,
    Description: `Kathmandu Graveerwerk ${nummer}`,
    ReturnURL: `${eigen}/terug`,
    ReturnURLCancel: `${eigen}/terug`,
    ReturnURLError: `${eigen}/terug`,
    ReturnURLReject: `${eigen}/terug`,
    PushURL: `${eigen}/push`,
    ContinueOnIncomplete: "RedirectToHTML",
    ClientIP: { Type: 0, Address: request.headers.get("CF-Connecting-IP") || "0.0.0.0" },
  };
  const antwoord = await buckaroo(env, "POST", "/json/Transaction", transactie);
  const doorsturen = antwoord?.RequiredAction?.RedirectURL;
  if (!doorsturen) {
    console.error("Buckaroo-antwoord zonder RedirectURL", JSON.stringify(antwoord));
    return json({ fout: "De betaalpagina kon niet worden geopend. Probeer het later opnieuw." }, 502);
  }
  order.buckarooKey = antwoord.Key;
  await env.ORDERS.put(nummer, JSON.stringify(order), { expirationTtl: 60 * 60 * 24 * 30 });
  return json({ doorsturen, nummer });
}

/* ---------------- betaling gemeld ---------------- */
async function push(request, env) {
  const tekst = await request.text();
  let key = null, invoice = null;
  try {
    const b = JSON.parse(tekst);
    key = b?.Transaction?.Key; invoice = b?.Transaction?.Invoice;
  } catch {
    const f = new URLSearchParams(tekst);
    key = f.get("brq_transactions"); invoice = f.get("brq_invoicenumber");
  }
  if (!key || !invoice) return new Response("ok");
  // De melding zelf niet vertrouwen: status rechtstreeks bij Buckaroo opvragen
  const status = await buckaroo(env, "GET", `/json/Transaction/Status/${encodeURIComponent(key)}`);
  const code = status?.Status?.Code?.Code;
  const order = JSON.parse((await env.ORDERS.get(invoice)) || "null");
  if (!order) return new Response("ok");
  if (code === 190 && order.status !== "betaald") {
    if (Math.abs(Number(status.AmountDebit) - order.totaal) > 0.009) {
      order.status = "bedrag-wijkt-af";
    } else {
      order.status = "betaald";
      order.betaaldOp = new Date().toISOString();
      await stuurOrdermail(env, order);
      await stuurBevestiging(env, order);
    }
    await env.ORDERS.put(invoice, JSON.stringify(order), { expirationTtl: 60 * 60 * 24 * 90 });
  } else if ([490, 491, 492, 690, 890, 891].includes(code)) {
    order.status = "mislukt";
    await env.ORDERS.put(invoice, JSON.stringify(order), { expirationTtl: 60 * 60 * 24 * 7 });
  }
  return new Response("ok");
}

/* ---------------- klant terug van Buckaroo ---------------- */
async function terug(request, env) {
  let code = null;
  if (request.method === "POST") {
    const f = new URLSearchParams(await request.text());
    code = f.get("brq_statuscode") || f.get("BRQ_STATUSCODE");
  } else {
    const q = new URL(request.url).searchParams;
    code = q.get("brq_statuscode") || q.get("BRQ_STATUSCODE");
  }
  const gelukt = code === "190" || code === "790" || code === "791";
  return Response.redirect(`${env.SITE_URL}/#${gelukt ? "bedankt" : "betaling-mislukt"}`, 303);
}

/* ---------------- Buckaroo met HMAC-ondertekening ---------------- */
async function buckaroo(env, methode, pad, body) {
  const host = env.BUCKAROO_TEST === "0" ? "checkout.buckaroo.nl" : "testcheckout.buckaroo.nl";
  const inhoud = body ? JSON.stringify(body) : "";
  const tijd = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID().replace(/-/g, "");
  const md5 = inhoud ? b64(await crypto.subtle.digest("MD5", new TextEncoder().encode(inhoud))) : "";
  const uri = encodeURIComponent(host + pad).toLowerCase();
  const te = env.BUCKAROO_WEBSITE_KEY + methode + uri + tijd + nonce + md5;
  const sleutel = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.BUCKAROO_SECRET_KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const handtekening = b64(await crypto.subtle.sign("HMAC", sleutel, new TextEncoder().encode(te)));
  const r = await fetch(`https://${host}${pad}`, {
    method: methode,
    headers: {
      "Content-Type": "application/json",
      Authorization: `hmac ${env.BUCKAROO_WEBSITE_KEY}:${handtekening}:${nonce}:${tijd}`,
      Culture: "nl-NL",
    },
    body: inhoud || undefined,
  });
  const t = await r.text();
  try { return JSON.parse(t); } catch { console.error("Buckaroo", r.status, t); return null; }
}

/* ---------------- e-mail via Resend ---------------- */
async function mail(env, bericht) {
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.MAIL_VAN, ...bericht }),
  });
  if (!r.ok) console.error("Mail mislukt", r.status, await r.text());
}

async function stuurOrdermail(env, o) {
  const bijlagen = [];
  const rijen = o.regels.map((r, i) => {
    const nr = i + 1;
    let g = "Zonder gravure";
    if (r.gravure) {
      const c = r.gravure.config || {};
      const basis = `${o.nummer}_regel${nr}_${r.code}`;
      bijlagen.push({ filename: `${basis}_xtool.svg`, content: btoa(unescape(encodeURIComponent(r.gravure.svg))) });
      if (r.gravure.voorbeeld) bijlagen.push({ filename: `${basis}_voorbeeld.png`, content: r.gravure.voorbeeld.split(",")[1] });
      if (r.gravure.logo) bijlagen.push({ filename: `${basis}_logo.png`, content: r.gravure.logo.split(",")[1] });
      g = `<b>Met gravure</b><br>Regel 1: ${esc(c.r1 || "–")}<br>Regel 2: ${esc(c.r2 || "–")}<br>Lettertype: ${esc(c.lettertype || "")} · Symbool: ${esc(c.symbool || "")}<br>
           Formaat: ${esc(r.gravure.formaat || "")}<br>Bijlagen: ${basis}_xtool.svg, ${basis}_voorbeeld.png${r.gravure.logo ? `, ${basis}_logo.png` : ""}`;
    }
    return `<tr><td style="padding:8px;border-top:1px solid #ddd;vertical-align:top">${nr}</td>
      <td style="padding:8px;border-top:1px solid #ddd;vertical-align:top"><b>${esc(r.naam)}</b><br>Bestelcode ${esc(r.code)} · ${r.n} stuks</td>
      <td style="padding:8px;border-top:1px solid #ddd;vertical-align:top">${g}</td>
      <td style="padding:8px;border-top:1px solid #ddd;vertical-align:top;text-align:right">${euro(r.bedrag)}</td></tr>`;
  }).join("");
  const k = o.klant;
  const html = `<div style="font-family:Arial,sans-serif;font-size:14px;color:#17201C">
    <h2>Nieuwe bestelling ${o.nummer}</h2>
    ${o.levertijdInOverleg ? `<p style="background:#FFF3D6;padding:8px"><b>${o.stuks} stuks: levertijd binnen 2 werkdagen met de klant afstemmen.</b></p>` : `<p>${o.stuks} stuks: binnen 3 werkdagen verzenden.</p>`}
    <p><b>Let op:</b> boek de artikelen handmatig uit in de kassa / op kathmandu.nl.</p>
    <h3>Klant</h3>
    <p>${esc(k.naam)}${k.bedrijf ? `<br>${esc(k.bedrijf)}` : ""}<br>${esc(k.straat)}<br>${esc(k.postcode)} ${esc(k.plaats)}<br>${esc(k.land)}<br>
       ${esc(k.email)}${k.telefoon ? `<br>${esc(k.telefoon)}` : ""}</p>
    ${k.opmerking ? `<p><b>Opmerking:</b> ${esc(k.opmerking)}</p>` : ""}
    <h3>Artikelen</h3>
    <table style="border-collapse:collapse;width:100%">${rijen}
      <tr><td colspan="3" style="padding:8px;text-align:right">Subtotaal</td><td style="padding:8px;text-align:right">${euro(o.subtotaal)}</td></tr>
      <tr><td colspan="3" style="padding:8px;text-align:right">Verzenden</td><td style="padding:8px;text-align:right">${o.verzending ? euro(o.verzending) : "gratis"}</td></tr>
      <tr><td colspan="3" style="padding:8px;text-align:right"><b>Totaal betaald</b></td><td style="padding:8px;text-align:right"><b>${euro(o.totaal)}</b></td></tr>
    </table></div>`;
  await mail(env, { to: env.MAIL_ORDERS, reply_to: k.email, subject: `Bestelling ${o.nummer} – ${k.naam} – ${o.stuks} st.`, html, attachments: bijlagen });
}

async function stuurBevestiging(env, o) {
  const k = o.klant;
  const regels = o.regels.map(r => `<li>${r.n} × ${esc(r.naam)}${r.gravure ? ` met gravure “${esc([r.gravure.config?.r1, r.gravure.config?.r2].filter(Boolean).join(" / ") || "symbool/logo")}”` : ""} – ${euro(r.bedrag)}</li>`).join("");
  const html = `<div style="font-family:Arial,sans-serif;font-size:14px;color:#17201C">
    <p>Beste ${esc(k.naam)},</p>
    <p>Bedankt voor je bestelling bij Kathmandu Graveerwerk. We hebben je betaling ontvangen.</p>
    <ul>${regels}</ul>
    <p>Totaal betaald: <b>${euro(o.totaal)}</b> (inclusief ${o.verzending ? euro(o.verzending) + " verzendkosten" : "gratis verzending"}).</p>
    <p>${o.levertijdInOverleg ? "Omdat je 4 of meer artikelen hebt besteld, nemen we binnen 2 werkdagen contact met je op over de levertijd." : "We verzenden je bestelling binnen 3 werkdagen."}</p>
    <p>Gegraveerde artikelen worden speciaal voor jou gemaakt en kun je niet retourneren. Vragen? Beantwoord deze mail of mail naar webshop@kathmandu.nl.</p>
    <p>Met vriendelijke groet,<br>Kathmandu Outdoor &amp; Travel<br>Glencoe B.V. · Oudegracht 256-264, 3511 NV Utrecht · KvK 30166640</p></div>`;
  await mail(env, { to: k.email, reply_to: env.MAIL_ORDERS, subject: `Bevestiging bestelling ${o.nummer}`, html });
}

/* ---------------- hulpjes ---------------- */
function cors(env, r) {
  const h = new Headers(r.headers);
  h.set("Access-Control-Allow-Origin", new URL(env.SITE_URL).origin);
  h.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type");
  return new Response(r.body, { status: r.status, headers: h });
}
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });
const round = n => Math.round(n * 100) / 100;
const euro = n => "€ " + n.toFixed(2).replace(".", ",");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pick = (o, ks) => Object.fromEntries(ks.map(k => [k, String(o[k] || "").slice(0, 500)]));
function b64(buf) { let s = ""; for (const x of new Uint8Array(buf)) s += String.fromCharCode(x); return btoa(s); }
