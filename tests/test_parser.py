"""Test de parser op pagina's die zijn nagebouwd naar de opbouw van kathmandu.nl (29-10-2026)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from voorraad_sync import producten_op_lijstpagina, lees_productpagina, is_graveerbaar

LIJST = """
<html><body>
<a href="https://www.kathmandu.nl/stanley-the-legendary-classic-bottle-0-59l-100000115641-029029" title="Stanley 10-11345-042 Groen"><img src="x.jpg"></a>
<a href="https://www.kathmandu.nl/stanley-the-legendary-classic-bottle-0-59l-100000115641-029029" title="Stanley 10-11345-042 Groen">Stanley The Legendary Classic Bottle 0,59L Hammertone Green Stanley The Legendary Classic Bottle 0,59L Hammertone Green 54,95</a>
<a href="https://www.kathmandu.nl/klean-kanteen-rise-ach-loop-cap-1011655-029873" title="Klean Kanteen 1011655 Zwart">Klean Kanteen Rise Ach Loop Cap Black Klean Kanteen Rise Ach Loop Cap Black 11,95</a>
<a href="https://www.kathmandu.nl/merken">Merken</a>
<a href="https://www.kathmandu.nl/uitrusting/drinkflessen-en-thermosflessen?page=2">2</a>
</body></html>
"""

PRODUCT = """
<html><head><meta property="og:image" content="https://www.kathmandu.nl/pimages/029029_1.jpg"></head><body>
<img src="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_1-200-200.jpg"><img src="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_2-200-200.jpg">
<a href="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_1-1500-1500.jpg"><img src="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_1-800-800.jpg"></a>
<a href="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_2-1500-1500.jpg"><img src="https://www.kathmandu.nl/pimages/cache/02/90/29/029029_2-800-800.jpg"></a>
<img src="https://www.kathmandu.nl/pimages/cache/02/89/97/028997_1-200-200.jpg">
<h1>Stanley The Legendary Classic Bottle 0,59L Hammertone Green</h1>
<div class="price">54,95</div>
<h2>Lokale winkelvoorraad</h2>
<table><thead><tr><th></th><th>ONE</th></tr></thead>
<tbody><tr><td>Amsterdam</td><td>4</td></tr><tr><td>Utrecht</td><td>2</td></tr></tbody></table>
<h2>Kenmerken</h2>
<table>
<tr><td>Merk</td><td><a href="/stanley">Stanley</a></td></tr>
<tr><td>Leverancierscode</td><td>10-11345-042</td></tr>
<tr><td>Bestelcode</td><td>029029</td></tr>
<tr><td>Kleur</td><td>Groen</td></tr>
</table></body></html>
"""

GEEN_VOORRAAD = PRODUCT.split("<h2>Lokale")[0] + "<h2>Kenmerken</h2></body></html>"


def test_lijst():
    p = producten_op_lijstpagina(LIJST)
    assert len(p) == 2, p
    assert p[0]["prijs"] == 54.95


def test_product():
    i = lees_productpagina(PRODUCT)
    assert i["naam"].startswith("Stanley The Legendary")
    assert i["prijs"] == 54.95
    assert i["voorraad_per_winkel"] == {"Amsterdam": 4, "Utrecht": 2}
    assert i["voorraad"] == 6
    assert i["bestelcode"] == "029029"
    assert i["leverancierscode"] == "10-11345-042"
    assert i["afbeeldingen"] == [
        "https://www.kathmandu.nl/pimages/cache/02/90/29/029029_1-1500-1500.jpg",
        "https://www.kathmandu.nl/pimages/cache/02/90/29/029029_2-1500-1500.jpg",
    ], i["afbeeldingen"]


def test_geen_voorraad():
    i = lees_productpagina(GEEN_VOORRAAD)
    assert i["voorraad"] == 0 and not i["voorraadtabel_gevonden"]


def test_graveerbaar():
    assert is_graveerbaar("Klean Kanteen Isolatiefles TKWide 473ml met koffiedop Black")
    assert is_graveerbaar("Stanley The Transit Fliptop Mug 0,47L Ash")
    assert not is_graveerbaar("Klean Kanteen Rise Ach Loop Cap Black")
    assert not is_graveerbaar("Klean Kanteen Sportdop (voor classic flessen) Black")


if __name__ == "__main__":
    for naam, f in list(globals().items()):
        if naam.startswith("test_"):
            f(); print("ok", naam)
