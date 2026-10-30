#!/usr/bin/env python3
"""
Voorraad-sync voor de graveershop.

Leest de merkpagina's van kathmandu.nl (Stanley en Klean Kanteen, categorie
drinkflessen en thermosflessen), opent elke productpagina, telt de
'Lokale winkelvoorraad' van Amsterdam en Utrecht bij elkaar op en schrijft
het resultaat naar voorraad.json. De webshop leest dat bestand.

Gebruik:
    python voorraad_sync.py                 # schrijft voorraad.json
    python voorraad_sync.py --uit pad.json  # ander uitvoerbestand

Bij een fout (pagina onbereikbaar, of een product zonder leesbare voorraad)
eindigt het script met exitcode 1, zodat GitHub Actions een melding stuurt.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASIS = "https://www.kathmandu.nl"
MERKPAGINAS = {
    "Stanley": f"{BASIS}/uitrusting/drinkflessen-en-thermosflessen/stanley",
    "Klean Kanteen": f"{BASIS}/uitrusting/drinkflessen-en-thermosflessen/klean-kanteen",
}
WINKELS = ("Amsterdam", "Utrecht")


PRODUCT_URL = re.compile(r"^https://www\.kathmandu\.nl/[a-z0-9\-]+-(\d{6})$")

HEADERS = {
    "User-Agent": "KathmanduGraveershop-voorraadsync/1.0 (+intern gebruik Kathmandu)"
}
PAUZE_SECONDEN = 0.5  # vriendelijk voor de eigen webshop


def haal(url: str, sessie: requests.Session) -> str:
    for poging in range(3):
        try:
            r = sessie.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if poging == 2:
                raise
            time.sleep(2 * (poging + 1))
    raise RuntimeError("onbereikbaar")


def parse_prijs(tekst: str):
    m = re.search(r"(\d+),(\d{2})", tekst)
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def producten_op_lijstpagina(html: str):
    """Geeft (url, naam, prijs) per product op een categorie/merkpagina."""
    soup = BeautifulSoup(html, "html.parser")
    gevonden = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(BASIS, a["href"].split("?")[0])
        if not PRODUCT_URL.match(url) or url in gevonden:
            continue
        tekst = a.get_text(" ", strip=True)
        if not tekst:  # afbeeldingslink zonder tekst; de tekstlink volgt nog
            continue
        # De linktekst eindigt op de prijs; naam en details komen van de productpagina.
        prijs = parse_prijs(tekst.split()[-1])
        gevonden[url] = {"url": url, "linktekst": tekst, "prijs": prijs}
    return list(gevonden.values())


def parse_afmetingen(tekst: str):
    """Haalt doorsnede en hoogte (mm) uit de producttekst. Kathmandu.nl schrijft dit op
    verschillende manieren, bijvoorbeeld 'Afmetingen: 69 x 68 x 227 mm' of
    'Grootte 18,5 cm H x 7,62 cm'. Geeft None als er geen bruikbare maat staat."""
    def mm(getal, eenheid):
        v = float(getal.replace(",", "."))
        return v * 10 if eenheid and eenheid.lower() == "cm" else v
    getal = r"(\d+(?:[.,]\d+)?)"
    for regel in tekst.splitlines():
        laag = regel.lower()
        if not any(w in laag for w in ("afmeting", "grootte", "formaat", "maten", "dimensie")):
            continue
        delen = re.findall(getal + r"\s*(mm|cm)?\s*(h|hoog|hoogte)?", regel, re.I)
        delen = [(g, e, h) for g, e, h in delen if float(g.replace(",", ".")) > 0]
        if len(delen) < 2 or "x" not in laag:
            continue
        # eenheid die achteraan staat geldt voor alle getallen zonder eigen eenheid
        laatste = next((e for _, e, _ in reversed(delen) if e), "mm")
        waarden = [(mm(g, e or laatste), bool(h)) for g, e, h in delen[:3]]
        hoogte = next((v for v, h in waarden if h), max(v for v, _ in waarden))
        rest = [v for v, h in waarden if v != hoogte] or [hoogte]
        d = min(rest)
        if 30 <= d <= 250 and 50 <= hoogte <= 600 and hoogte >= d * .6:
            return {"d": round(d, 1), "h": round(hoogte, 1)}
    return None


TOEGESTANE_TAGS = {"p", "ul", "ol", "li", "h3", "h4", "strong", "b", "em", "i", "br"}


def lees_omschrijving(soup):
    """Producttekst onder 'Over dit item' tot de volgende hoofdkop, als schone HTML
    (alleen alinea's, lijsten, tussenkoppen en nadruk; geen links, scripts of opmaak)."""
    from bs4 import NavigableString, Tag
    kop = soup.find(lambda t: t.name in ("h2", "h3") and t.get_text(strip=True).lower() == "over dit item")
    if not kop:
        return None

    def schoon(el):
        if isinstance(el, NavigableString):
            return re.sub(r"\s+", " ", str(el)).replace("<", "&lt;").replace(">", "&gt;")
        if not isinstance(el, Tag) or el.name in ("script", "style", "img", "table", "form", "button"):
            return ""
        binnen = "".join(schoon(k) for k in el.children).strip()
        if el.name == "br":
            return "<br>"
        if el.name in TOEGESTANE_TAGS and binnen:
            naam = {"b": "strong", "i": "em"}.get(el.name, el.name)
            return f"<{naam}>{binnen}</{naam}>"
        if el.name in ("div", "section") and binnen and not binnen.startswith("<"):
            return f"<p>{binnen}</p>"
        return binnen

    delen = []
    for el in kop.next_siblings:
        if isinstance(el, Tag) and el.name == "h2":
            break
        tekst = schoon(el).strip()
        if tekst:
            delen.append(tekst if tekst.startswith("<") else f"<p>{tekst}</p>")
    if not delen:
        # kop zit in een eigen omhulsel: loop vooruit door het document tot de volgende h2
        gezien = []
        for el in kop.find_all_next(True):
            if el.name == "h2":
                break
            if el.name in ("p", "ul", "ol", "h3", "h4") and not any(g in el.parents for g in gezien):
                gezien.append(el)
                tekst = schoon(el).strip()
                if tekst:
                    delen.append(tekst if tekst.startswith("<") else f"<p>{tekst}</p>")
    html = "".join(delen)
    return html or None


def lees_productpagina(html: str):
    """Haalt naam, prijs, bestelcode, leverancierscode, afbeelding en voorraad uit een productpagina."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    naam = h1.get_text(" ", strip=True) if h1 else None

    prijs = None
    if h1:
        for el in h1.find_all_next(string=True, limit=40):
            if h1 in el.parents:  # '0,59L' in de titel is geen prijs
                continue
            p = parse_prijs(el) if re.fullmatch(r"\s*€?\s*\d+,\d{2}\s*", el) else None
            if p:
                prijs = p
                break

    def kenmerk(label):
        cel = soup.find(lambda t: t.name in ("td", "th") and t.get_text(strip=True) == label)
        if cel:
            buur = cel.find_next_sibling(["td", "th"])
            if buur:
                return buur.get_text(" ", strip=True)
        return None

    afbeelding = None
    og = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
    if og and og.get("content"):
        afbeelding = og["content"]

    # Alle productfoto's: kathmandu.nl gebruikt /pimages/cache/../<code>_<n>-<px>-<px>.jpg.
    # Per fotonummer bewaren we de grootste variant.
    fotos = {}
    for tag in soup.find_all(["img", "a"]):
        src = tag.get("src") or tag.get("href") or ""
        m = re.search(r"/pimages/(?:cache/[\d/]+/)?(\d{6})_(\d+)(?:-(\d+)-\d+)?\.(jpe?g|png|webp)$", src)
        if not m:
            continue
        nr, px = int(m.group(2)), int(m.group(3) or 0)
        if nr not in fotos or px > fotos[nr][0]:
            fotos[nr] = (px, urljoin(BASIS, src))
    afbeeldingen = [fotos[n][1] for n in sorted(fotos)]
    if not afbeeldingen and afbeelding:
        afbeeldingen = [afbeelding]

    voorraad = {w: 0 for w in WINKELS}
    tabel_gevonden = False
    kop = soup.find(lambda t: t.name in ("h2", "h3", "h4", "div", "span", "p")
                    and "lokale winkelvoorraad" in t.get_text(" ", strip=True).lower()
                    and len(t.get_text(strip=True)) < 60)
    if kop:
        tabel = kop.find_next("table")
        if tabel:
            tabel_gevonden = True
            for rij in tabel.find_all("tr"):
                cellen = [c.get_text(" ", strip=True) for c in rij.find_all(["td", "th"])]
                if not cellen:
                    continue
                winkel = cellen[0]
                for w in WINKELS:
                    if w.lower() in winkel.lower():
                        voorraad[w] += sum(int(c) for c in cellen[1:] if c.isdigit())

    return {
        "naam": naam,
        "prijs": prijs,
        "bestelcode": kenmerk("Bestelcode"),
        "leverancierscode": kenmerk("Leverancierscode"),
        "kleur": kenmerk("Kleur"),
        "afbeelding": afbeeldingen[0] if afbeeldingen else None,
        "afbeeldingen": afbeeldingen,
        "afmetingen_mm": parse_afmetingen(soup.get_text("\n")),
        "omschrijving": lees_omschrijving(soup),
        "voorraad_per_winkel": voorraad,
        "voorraad": sum(voorraad.values()),
        "voorraadtabel_gevonden": tabel_gevonden,
    }


def is_graveerbaar(naam: str) -> bool:
    """Losse doppen en onderdelen eruit; een fles 'met koffiedop' blijft erin."""
    if not naam:
        return False
    n = naam.lower()
    if re.search(r"\bvoor (classic|tk|rise)", n):  # 'Sportdop (voor classic flessen)'
        return False
    is_onderdeel = re.search(r"(dop|cap|deksel|spork|rietje|straw)\b", n)
    is_fles = re.search(r"\bmet\b", n) or re.search(r"\b(bottle|mug|jar|flask)\b", n)
    return not (is_onderdeel and not is_fles)


def verzamel_merk(merk: str, start_url: str, sessie: requests.Session):
    alle, pagina = {}, 1
    while True:
        url = start_url if pagina == 1 else f"{start_url}?page={pagina}"
        nieuw = [p for p in producten_op_lijstpagina(haal(url, sessie)) if p["url"] not in alle]
        if not nieuw:
            break
        for p in nieuw:
            alle[p["url"]] = p
        pagina += 1
        if pagina > 20:  # vangnet
            break
        time.sleep(PAUZE_SECONDEN)
    return list(alle.values())


# ---------- rechte vooraanzichten van de fabrikant (Stanley) ----------
STANLEY = "https://eu.stanley1913.com"
STANLEY_PAGINAS = [
    (r"legendary classic bottle 2,3", "/products/classic-legendary-bottle-2-3-l"),
    (r"legendary classic bottle 1,9", "/products/classic-legendary-bottle-1-9-l"),
    (r"legendary classic bottle 1,4", "/products/legendary-classic-bottle-1-4-l"),
    (r"legendary classic bottle 0,94", "/products/classic-legendary-bottle-0-94-l"),
    (r"legendary classic bottle 0,59", "/products/classic-legendary-bottle-0-59-l"),
    (r"adventure to-go bottle 1", "/products/adventure-to-go-bottle-1-l"),
    (r"adventure to-go bottle 0,75", "/products/adventure-to-go-bottle-0-75-l"),
    (r"adventure to-go food jar 0,53", "/products/adventure-to-go-food-jar-0-53-l"),
    (r"adventure to-go food jar", "/products/adventure-to-go-food-jar-0-35-l"),
    (r"legendary food jar \+ spork", "/products/legendary-food-jar-spork-0-4-l"),
    (r"transit fliptop mug 0,47", "/products/transit-fliptop-mug-0-47-l"),
    (r"transit fliptop mug 0,35", "/products/transit-fliptop-mug-0-35-l"),
    (r"caf.-to-go travel mug 0,23", "/products/cafe-to-go-travel-mug-0-23-l"),
    (r"caf.-to-go travel mug 0,35", "/products/cafe-to-go-travel-mug-0-35-l"),
    (r"everyday camp mug 0,23", "/products/classic-legendary-camp-mug-0-23-l"),
    (r"everyday camp mug 0,35", "/products/classic-legendary-camp-mug-0-35-l"),
    (r"stacking camp cup", "/products/stay-hot-stacking-camp-cup-0-29-l"),
    (r"stacking tumbler", "/products/adventure-stacking-beer-pint-16-oz"),
    (r"legendary food jar 0,7", "/products/classic-legendary-food-jar-0-71-l"),
    (r"growler", "/products/classic-easy-pour-growler-64-oz"),
    (r"beer stein", "/products/adventure-big-grip-beer-stein-24-oz"),
    (r"easy fill flask", "/products/classic-easy-fill-wide-mouth-flask-0-23-l"),
    (r"iceflow", "/products/the-iceflow-aerolight-water-bottle-fast-flow-0-59-l"),
]


def kleur_uit_naam(naam):
    """'Stanley The Legendary Classic Bottle 0,59L  Hammertone Green' -> 'Hammertone Green'."""
    n = re.sub(r"\s+", " ", naam)
    m = re.search(r"\d+(?:,\d+)?\s?(?:ml|l|L|oz)\b(?:/\d+oz)?\s*(.*)$", n)
    kleur = m.group(1) if m else ""
    kleur = re.sub(r"^met \S+?,?\s+", "", kleur, flags=re.I)
    if not kleur:  # bv. 'Stay Hot Stacking Camp Cup Ash'
        kleur = " ".join(n.split()[-1:])
    return kleur.strip()


def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower().replace("2.0", ""))


def stanley_vooraanzicht(naam, sessie):
    """Zoekt op eu.stanley1913.com de rechte vooraanzicht-foto ('Front View') in de kleur
    van dit artikel. Geeft de afbeeldings-URL of None."""
    from difflib import SequenceMatcher
    laag = naam.lower()
    pad = next((p for r, p in STANLEY_PAGINAS if re.search(r, laag)), None)
    if pad is None:
        return None   # alleen bekende modelpagina's; vrij zoeken gaf verkeerde producten
    soup = BeautifulSoup(haal(STANLEY + pad, sessie), "html.parser")
    gewenst = _norm(kleur_uit_naam(naam))
    beste, score = None, 0.0
    for img in soup.find_all("img"):
        alt = img.get("alt") or ""
        if "front view" not in alt.lower():
            continue
        m = re.search(r"\bin (.+?)\s*\|\s*front view", alt, re.I)
        if not m:
            continue
        kandidaat = _norm(m.group(1))
        s = SequenceMatcher(None, gewenst, kandidaat).ratio()
        if kandidaat == gewenst:
            s = 1.0
        elif len(gewenst) >= 3 and gewenst in kandidaat:   # "Ash" past bij "Hammertone Ash"
            s = max(s, .9)
        src = img.get("src") or img.get("data-src") or ""
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[0].split()[0]
        if src and s > score:
            beste, score = src, s
    if not beste or score < .85:
        return None
    if beste.startswith("//"):
        beste = "https:" + beste
    beste = urljoin(STANLEY, beste)
    # grote versie opvragen
    beste = re.sub(r"([?&])(width|height)=\d+", r"\g<1>", beste).rstrip("&?")
    return beste + ("&" if "?" in beste else "?") + "width=1200"


def foto_naar_wit(data: bytes) -> bytes:
    """Transparante PNG op een witte achtergrond zetten (JPEG-bytes terug)."""
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        wit = Image.new("RGBA", im.size, (255, 255, 255, 255))
        wit.alpha_composite(im)
        im = wit
    uit = BytesIO()
    im.convert("RGB").save(uit, "JPEG", quality=95)
    return uit.getvalue()


def recht_fotovlak(pad, merk):
    """Meet op de rechte foto het graveervlak en de verhouding pixels/hoogte,
    zodat de shop de gravure op ware schaal kan tonen."""
    from PIL import Image
    v = meet_graveervlak(pad, merk)
    if not v:
        return None
    grijs = Image.open(pad).convert("L")
    masker = grijs.point(lambda x: 255 if x < 242 else 0)
    box = masker.getbbox()
    if box:
        v["product_y0"] = round(box[1] / grijs.height, 4)
        v["product_h"] = round((box[3] - box[1]) / grijs.height, 4)
    return v


FOTO_PX = 1000      # breedte en hoogte van de opgeslagen foto
FOTO_MARGE = 0.07   # witruimte rond de fles, als deel van de foto


def _snij_achtergrond(im, kleur, drempel=14):
    """Bounding box van alles wat afwijkt van de achtergrondkleur."""
    from PIL import Image, ImageChops
    vlak = Image.new("RGB", im.size, kleur)
    verschil = ImageChops.difference(im, vlak).convert("L").point(lambda v: 255 if v > drempel else 0)
    return verschil.getbbox()


def bewerk_foto(data: bytes) -> bytes:
    """Snijdt witte rand en een egaal (grijs) fotovlak weg, zet de fles gecentreerd op wit
    en schaalt naar FOTO_PX. Valt terug op de originele foto als er iets misgaat."""
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(data)).convert("RGB")
    for _ in range(2):  # eerst de witte rand, dan eventueel het grijze vlak daarbinnen
        hoeken = [im.getpixel(p) for p in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1))]
        gem = tuple(sum(c[i] for c in hoeken) // 4 for i in range(3))
        egaal = all(max(abs(c[i] - gem[i]) for i in range(3)) < 10 for c in hoeken) and min(gem) > 200
        if not egaal:
            break
        box = _snij_achtergrond(im, gem)
        if not box or (box[2] - box[0]) * (box[3] - box[1]) > 0.97 * im.width * im.height:
            break
        im = im.crop(box)
    # Restjes lichtgrijs fotovlak rond de fles wit maken
    licht = im.convert("L").point(lambda v: 255 if v >= 240 else 0)
    im.paste((255, 255, 255), mask=licht)
    binnen = int(FOTO_PX * (1 - 2 * FOTO_MARGE))
    schaal = binnen / max(im.width, im.height)  # ook vergroten, zodat elke fles even groot staat
    im = im.resize((max(1, round(im.width * schaal)), max(1, round(im.height * schaal))), Image.LANCZOS)
    vel = Image.new("RGB", (FOTO_PX, FOTO_PX), (255, 255, 255))
    vel.paste(im, ((FOTO_PX - im.width) // 2, (FOTO_PX - im.height) // 2))
    uit = BytesIO()
    vel.save(uit, "JPEG", quality=84, optimize=True, progressive=True)
    return uit.getvalue()


def meet_graveervlak(pad, merk=""):
    """Meet op de (bijgesneden) hoofdfoto waar het rechte deel van de fles zit.
    Geeft het graveervlak als fracties van de foto plus de lak- en graveerkleur,
    zodat de shop de gravure op de echte foto kan tonen."""
    from PIL import Image
    im = Image.open(pad).convert("RGB")
    k = 250 / im.width
    klein = im.resize((250, round(im.height * k)))
    grijs = klein.convert("L")
    W, H = klein.size
    px = grijs.load()
    rijen = []  # per rij: (lengte, start) van het langste niet-witte stuk; hengsels zijn dunner
    for y in range(H):
        beste, start, run = (0, 0), None, 0
        for x in range(W + 1):
            donker = x < W and px[x, y] < 242
            if donker:
                if start is None:
                    start = x
            elif start is not None:
                if x - start > beste[0]:
                    beste = (x - start, start)
                start = None
        rijen.append(beste)
    gevuld = [y for y, r in enumerate(rijen) if r[0] > 3]
    if not gevuld:
        return None
    top, bodem = gevuld[0], gevuld[-1]
    hoog = bodem - top
    band = [rijen[y] for y in range(top + int(hoog * .3), bodem - int(hoog * .12))]
    if not band:
        return None
    breed = sorted(r[0] for r in band)[len(band) // 2]
    # langste aaneengesloten stuk waar de fles (bijna) volle breedte heeft: het cilindrische deel
    beste, cur = (0, 0), None
    for y in range(top, bodem + 1):
        if rijen[y][0] >= .9 * breed:
            cur = y if cur is None else cur
            if y - cur > beste[1] - beste[0]:
                beste = (cur, y)
        else:
            cur = None
    y0, y1 = beste
    if y1 - y0 < 10:
        return None
    midden = sorted(rijen[y][1] + rijen[y][0] / 2 for y in range(y0, y1 + 1))[(y1 - y0) // 2]
    vw = breed * .78                                   # zichtbaar front waarbinnen de gravure past
    # lakkleur: mediaan van een smalle strook midden op de fles
    strook = [klein.getpixel((int(midden + dx), int(y))) for y in range(y0, y1, 2) for dx in (-breed * .12, 0, breed * .12)]
    kleur = tuple(sorted(c[i] for c in strook)[len(strook) // 2] for i in range(3))

    # Merklogo's en opdruk overslaan: rijen waar het midden van de fles duidelijk van de lak afwijkt
    xa, xb = int(midden - breed * .3), int(midden + breed * .3)
    def afwijkend(y):
        n = sum(1 for x in range(xa, xb) if sum(abs(a - b) for a, b in zip(klein.getpixel((x, y)), kleur)) > 70)
        return n > (xb - xa) * .05
    logo = [afwijkend(y) for y in range(y0, y1 + 1)]
    vrij = [not any(logo[max(0, i - 3):i + 4]) for i in range(len(logo))]   # 3 rijen marge rond opdruk
    best, cur = (0, -1), None
    for i, v in enumerate(vrij + [False]):
        if v and cur is None:
            cur = i
        elif not v and cur is not None:
            if i - cur > best[1] - best[0] + 1:
                best = (cur, i - 1)
            cur = None
    seg = y1 - y0
    if any(logo) and best[1] - best[0] + 1 >= seg * .3:
        cy, vh = y0 + (best[0] + best[1]) / 2, (best[1] - best[0]) * .85
    elif merk.lower() == "stanley":
        # opdruk niet gevonden (bijv. donker logo op donkere lak): Stanley-logo zit altijd bovenaan
        cy, vh = y0 + seg * .7, seg * .42
    else:
        cy, vh = y0 + seg * .5, seg * .8
    # dopkleur: bovenste 7% van het product, midden van elke rij
    dop_px = []
    for y in range(top, top + max(2, int(hoog * .07))):
        lengte, start = rijen[y]
        if lengte > 3:
            for dx in (-.15, 0, .15):
                dop_px.append(klein.getpixel((int(start + lengte * (.5 + dx)), y)))
    dop = tuple(sorted(c[i] for c in dop_px)[len(dop_px) // 2] for i in range(3)) if dop_px else kleur
    lum = .299 * kleur[0] + .587 * kleur[1] + .114 * kleur[2]
    verzadiging = max(kleur) - min(kleur)
    if lum < 140:
        gravure = "#D6D9DA"     # donkere lak: laser legt het staal bloot, licht zilver
    elif verzadiging < 18:
        gravure = "#3E4144"     # blank RVS of wit: laser kleurt donker
    else:
        gravure = "#8F9497"     # lichte lak: mat zilver (iets donkerder voor leesbaarheid)
    return {
        "cx": round(midden / W, 4), "cy": round(cy / H, 4),   # midden van het vlak
        "w": round(vw / W, 4), "h": round(vh / H, 4),         # maximale ruimte
        "lak": "#%02X%02X%02X" % kleur, "dop": "#%02X%02X%02X" % dop, "gravure": gravure,
    }


def download_fotos(info, map_, sessie, problemen, max_fotos=4):
    """Slaat de foto's op als img/<bestelcode>_<n>.jpg. Bestaande bestanden worden
    niet opnieuw gedownload, dus na de eerste run kost dit vrijwel niets."""
    os.makedirs(map_, exist_ok=True)
    code = info.get("bestelcode") or "onbekend"
    lokaal = []
    for i, url in enumerate(info.get("afbeeldingen", [])[:max_fotos], start=1):
        ext = os.path.splitext(url)[1].lower() or ".jpg"
        naam = f"{code}_{i}{ext}"
        pad = os.path.join(map_, naam)
        if not os.path.exists(pad):
            try:
                r = sessie.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                if not r.headers.get("Content-Type", "").startswith("image/"):
                    raise ValueError(f"geen afbeelding ({r.headers.get('Content-Type')})")
                try:
                    inhoud = bewerk_foto(r.content)
                except Exception:  # noqa: BLE001  (Pillow ontbreekt of onleesbare foto)
                    inhoud = r.content
                with open(pad, "wb") as f:
                    f.write(inhoud)
                time.sleep(PAUZE_SECONDEN)
            except Exception as e:  # noqa: BLE001
                problemen.append(f"foto {url}: {e}")
                continue
        lokaal.append(f"{os.path.basename(map_)}/{naam}")
    return lokaal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uit", default="voorraad.json")
    ap.add_argument("--fotos", action="store_true", help="productfoto's downloaden")
    ap.add_argument("--fotomap", default="img", help="map voor de foto's, naast voorraad.json")
    args = ap.parse_args()
    args.fotomap = os.path.join(os.path.dirname(os.path.abspath(args.uit)), args.fotomap)

    sessie = requests.Session()
    producten, problemen = [], []

    for merk, url in MERKPAGINAS.items():
        lijst = verzamel_merk(merk, url, sessie)
        print(f"{merk}: {len(lijst)} producten op de merkpagina's")
        for item in lijst:
            time.sleep(PAUZE_SECONDEN)
            try:
                info = lees_productpagina(haal(item["url"], sessie))
            except Exception as e:  # noqa: BLE001
                problemen.append(f"{item['url']}: {e}")
                continue
            if not is_graveerbaar(info["naam"]):
                continue
            if args.fotos:
                info["lokale_afbeeldingen"] = download_fotos(info, args.fotomap, sessie, problemen)
                if merk == "Stanley" and info.get("bestelcode"):
                    recht = os.path.join(args.fotomap, f"{info['bestelcode']}_recht.jpg")
                    try:
                        if not os.path.exists(recht):
                            url = stanley_vooraanzicht(info["naam"] or "", sessie)
                            if url:
                                r = sessie.get(url, headers=HEADERS, timeout=30)
                                r.raise_for_status()
                                with open(recht, "wb") as f:
                                    f.write(bewerk_foto(foto_naar_wit(r.content)))
                                time.sleep(PAUZE_SECONDEN)
                            else:
                                print(f"geen Stanley-vooraanzicht gevonden: {info['naam']}", file=sys.stderr)
                        if os.path.exists(recht):
                            info["recht_foto"] = {"pad": f"{os.path.basename(args.fotomap)}/{os.path.basename(recht)}",
                                                  **(recht_fotovlak(recht, merk) or {})}
                    except Exception as e:  # noqa: BLE001
                        print(f"Stanley-vooraanzicht mislukt voor {info.get('bestelcode')}: {e}", file=sys.stderr)
                if info["lokale_afbeeldingen"]:
                    try:
                        hoofd = os.path.join(os.path.dirname(args.fotomap), info["lokale_afbeeldingen"][0])
                        info["graveervlak_foto"] = meet_graveervlak(hoofd, merk)
                    except Exception as e:  # noqa: BLE001
                        print(f"graveervlak niet gemeten voor {info.get('bestelcode')}: {e}", file=sys.stderr)
            # Controle: wijkt de lakkleur van de fabrikantfoto sterk af van de foto op kathmandu.nl,
            # dan is waarschijnlijk de verkeerde kleur gepakt. Dan geen fabrikantfoto gebruiken.
            r, k = info.get("recht_foto"), info.get("graveervlak_foto")
            if r and k and r.get("lak") and k.get("lak"):
                a = [int(r["lak"][i:i + 2], 16) for i in (1, 3, 5)]
                b = [int(k["lak"][i:i + 2], 16) for i in (1, 3, 5)]
                if sum(abs(x - y) for x, y in zip(a, b)) > 150:
                    print(f"kleur fabrikantfoto wijkt af, niet gebruikt: {info['naam']}", file=sys.stderr)
                    info["recht_foto"] = {"afgekeurd": True, "lak": r["lak"]}
            producten.append({"merk": merk, "url": item["url"], **info})

    zonder_tabel = sum(1 for p in producten if not p["voorraadtabel_gevonden"])
    if producten and zonder_tabel / len(producten) > 0.8:
        problemen.append(
            f"Bij {zonder_tabel} van {len(producten)} producten geen voorraadtabel gevonden. "
            "Waarschijnlijk is de opbouw van de productpagina veranderd."
        )

    uitvoer = {
        "bijgewerkt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bron": "kathmandu.nl, lokale winkelvoorraad Amsterdam + Utrecht",
        "aantal": len(producten),
        "producten": sorted(producten, key=lambda p: (p["merk"], p["naam"] or "")),
    }
    with open(args.uit, "w", encoding="utf-8") as f:
        json.dump(uitvoer, f, ensure_ascii=False, indent=2)
    print(f"{len(producten)} graveerbare producten geschreven naar {args.uit}")

    if problemen:
        print("PROBLEMEN:", *problemen, sep="\n- ", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
