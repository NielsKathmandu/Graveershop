/**
 * Kathmandu Graveerweekend – server voor de personeelstool op de tablet.
 *
 * Draait als Cloudflare Worker (gratis abonnement volstaat). Routes:
 *   POST /pin          controleert de pincode van de medewerkers
 *   POST /winkelorder  bestelling uit de winkel -> ordermail met SVG/voorbeeld naar MAIL_ORDERS (de klant krijgt geen mail)
 *
 * Instellingen (Cloudflare: Settings -> Variables and Secrets):
 *   SITE_URL        https://nielskathmandu.github.io/Graveershop   (zonder slash)
 *   PINCODE         de pincode voor de tablets (bij voorkeur 6 cijfers)
 *   RESEND_API_KEY  (geheim) voor het versturen van e-mail via resend.com
 *   MAIL_VAN        bijv. "Kathmandu Graveerwerk <onboarding@resend.dev>"
 *   MAIL_ORDERS     adres waar de ordermails binnenkomen (nu niels@kathmandu.nl, later servicedesk@kathmandu.nl)
 * (Oude webshopcode met Buckaroo staat in de git-geschiedenis.)
 */

const WINKELS = {
  Amsterdam: { kort: "A", adres: "Haarlemmerstraat 123, Amsterdam" },
  Utrecht: { kort: "U", adres: "Oudegracht 256-264, Utrecht" },
};
const MAX_STUKS = 100;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));
      if (url.pathname === "/pin" && request.method === "POST") return cors(env, await pin(request, env));
      if (url.pathname === "/winkelorder" && request.method === "POST") return cors(env, await winkelorder(request, env));
      return new Response("Kathmandu Graveerweekend", { status: 200 });
    } catch (e) {
      console.error(e);
      return cors(env, json({ fout: "Er ging iets mis op de server. Probeer het opnieuw." }, 500));
    }
  },
};

/* ---------------- pincode ---------------- */
async function pinKlopt(env, invoer) {
  if (!env.PINCODE) return false;
  const ok = String(invoer || "") === String(env.PINCODE);
  if (!ok) await new Promise(r => setTimeout(r, 800)); // raden traag maken
  return ok;
}
async function pin(request, env) {
  if (!env.PINCODE) return json({ fout: "Er is nog geen pincode ingesteld in Cloudflare (variabele PINCODE)." }, 500);
  const { pin } = await request.json().catch(() => ({}));
  return (await pinKlopt(env, pin)) ? json({ ok: true }) : json({ fout: "Pincode klopt niet." }, 403);
}

/* ---------------- bestelling uit de winkel ---------------- */
async function winkelorder(request, env) {
  const invoer = await request.json().catch(() => ({}));
  if (!(await pinKlopt(env, invoer.pin))) return json({ fout: "Pincode klopt niet." }, 403);
  const winkel = WINKELS[invoer.winkel] ? invoer.winkel : null;
  if (!winkel) return json({ fout: "Kies eerst de winkel." }, 400);
  const k = pick(invoer.klant || {}, ["naam", "email", "telefoon", "opmerking"]);
  if (!k.naam.trim()) return json({ fout: "Vul de naam van de klant in." }, 400);
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(k.email)) return json({ fout: "Het e-mailadres van de klant klopt niet." }, 400);
  const medewerker = String(invoer.medewerker || "").trim().slice(0, 60);
  if (!medewerker) return json({ fout: "Vul je eigen naam in." }, 400);
  if (!Array.isArray(invoer.regels) || !invoer.regels.length) return json({ fout: "De bestelling is leeg." }, 400);

  // namen uit voorraad.json, niet uit de browser
  const voorraad = await (await fetch(`${env.SITE_URL}/voorraad.json`, { cf: { cacheTtl: 300 } })).json();
  const perCode = Object.fromEntries(voorraad.producten.map(p => [p.bestelcode, p]));
  const regels = [];
  for (const r of invoer.regels) {
    const p = perCode[r.code];
    const n = Math.floor(Number(r.n));
    if (!p) return json({ fout: `Artikel ${r.code} is niet gevonden.` }, 400);
    if (!(n >= 1 && n <= MAX_STUKS)) return json({ fout: "Ongeldig aantal." }, 400);
    const g = r.gravure;
    if (!g || typeof g.svg !== "string" || !g.svg.trim().startsWith("<")) return json({ fout: "Er ontbreekt een ontwerp bij een artikel." }, 400);
    regels.push({ code: p.bestelcode, naam: p.naam, n, gravure: g });
  }

  const nu = new Date();
  const dag = nu.toLocaleDateString("nl-NL", { timeZone: "Europe/Amsterdam", day: "2-digit", month: "2-digit" }).replace(/\D/g, "");
  const tekens = "ACDEFHJKLMNPRTUVWXY3479";
  const willekeurig = [...crypto.getRandomValues(new Uint8Array(3))].map(b => tekens[b % tekens.length]).join("");
  const order = {
    nummer: `${WINKELS[winkel].kort}-${dag}-${willekeurig}`,
    datum: nu.toLocaleString("nl-NL", { timeZone: "Europe/Amsterdam", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
    winkel, klant: k, medewerker, regels,
    stuks: regels.reduce((t, r) => t + r.n, 0),
  };

  const ok = await stuurOrdermail(env, order);
  if (!ok) return json({ fout: "De ordermail kon niet worden verstuurd. Controleer de Resend-instellingen." }, 502);
  return json({ ok: true, nummer: order.nummer, naar: env.MAIL_ORDERS });
}

/* ---------------- e-mail via Resend ---------------- */
async function mail(env, bericht) {
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.MAIL_VAN, ...bericht }),
  });
  if (!r.ok) console.error("Mail mislukt", r.status, await r.text());
  return r.ok;
}

async function stuurOrdermail(env, o) {
  const bijlagen = [];
  const td = (x, rechts = false) => `<td style="padding:8px;border-top:1px solid #ddd;vertical-align:top${rechts ? ";text-align:right;white-space:nowrap" : ""}">${x}</td>`;
  const rijen = o.regels.map((r, i) => {
    const nr = i + 1, c = r.gravure.config || {};
    const basis = `${o.nummer}_regel${nr}_${r.code}`;
    bijlagen.push({ filename: `${basis}_xtool.svg`, content: btoa(unescape(encodeURIComponent(r.gravure.svg))) });
    if (r.gravure.voorbeeld) bijlagen.push({ filename: `${basis}_voorbeeld.png`, content: r.gravure.voorbeeld.split(",")[1] });
    if (r.gravure.logo) bijlagen.push({ filename: `${basis}_logo.png`, content: r.gravure.logo.split(",")[1] });
    return `<tr>${td(nr)}${td(`<b>${esc(r.naam)}</b><br>Bestelcode ${esc(r.code)}`)}${td(r.n, true)}
      ${td(`Regel 1: <b>${esc(c.r1 || "–")}</b><br>Regel 2: <b>${esc(c.r2 || "–")}</b><br>Lettertype: ${esc(c.lettertype || "")} · Symbool: ${esc(c.symbool || "")}<br>
        Formaat: ${esc(r.gravure.formaat || "")}<br><span style="color:#555">Bijlagen: ${basis}_xtool.svg${r.gravure.voorbeeld ? `, ${basis}_voorbeeld.png` : ""}${r.gravure.logo ? `, ${basis}_logo.png` : ""}</span>`)}</tr>`;
  }).join("");
  const k = o.klant, naarUtrecht = o.winkel !== "Utrecht";
  const html = `<div style="font-family:Arial,sans-serif;font-size:14px;color:#17201C">
    <p style="font-size:30px;font-weight:bold;margin:0 0 4px;letter-spacing:1px">${esc(o.nummer)}</p>
    <p style="background:#E9DCC6;padding:10px;font-size:16px;margin:0 0 12px">
      Gekocht in <b>${esc(o.winkel)}</b>. Na het graveren <b>terug naar ${esc(o.winkel)}</b>: de klant haalt de fles daar op.
      ${naarUtrecht ? "<br>De fles komt met dit ordernummer van Amsterdam naar Utrecht." : ""}</p>
    <p><b>Graveerweekend:</b> de klant rekent de fles af aan de kassa, graveren is gratis. Het ordernummer staat op de kassabon die aan de fles zit.</p>
    <table style="border-collapse:collapse">
      <tr><td style="padding:2px 16px 2px 0;color:#555">Klant</td><td>${esc(k.naam)}</td></tr>
      <tr><td style="padding:2px 16px 2px 0;color:#555">E-mail</td><td>${esc(k.email)}</td></tr>
      ${k.telefoon ? `<tr><td style="padding:2px 16px 2px 0;color:#555">Telefoon</td><td>${esc(k.telefoon)}</td></tr>` : ""}
      <tr><td style="padding:2px 16px 2px 0;color:#555">Opgenomen door</td><td>${esc(o.medewerker)}, ${esc(o.datum)}</td></tr>
      ${k.opmerking ? `<tr><td style="padding:2px 16px 2px 0;color:#555">Opmerking</td><td>${esc(k.opmerking)}</td></tr>` : ""}
    </table>
    <h3>Te graveren (${o.stuks} ${o.stuks === 1 ? "fles" : "flessen"})</h3>
    <table style="border-collapse:collapse;width:100%">
      <tr style="text-align:left"><th style="padding:8px">#</th><th style="padding:8px">Artikel</th><th style="padding:8px;text-align:right">Aantal</th><th style="padding:8px">Gravure</th></tr>${rijen}
    </table>
    <p style="color:#555">Beantwoorden van deze mail gaat naar de klant, bijvoorbeeld om te laten weten dat de fles klaarligt in ${esc(o.winkel)}.</p></div>`;
  return mail(env, { to: env.MAIL_ORDERS, reply_to: k.email, subject: `Graveerorder ${o.nummer} – ${o.winkel} – ${k.naam} – ${o.stuks} st.`, html, attachments: bijlagen });
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
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pick = (o, ks) => Object.fromEntries(ks.map(k => [k, String(o[k] || "").slice(0, 500)]));
