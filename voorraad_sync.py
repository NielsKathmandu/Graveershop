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
                with open(pad, "wb") as f:
                    f.write(r.content)
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
