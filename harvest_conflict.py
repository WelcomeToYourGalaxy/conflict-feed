#!/usr/bin/env python3
"""
harvest_conflict.py — the conflict wire: invasions and offensives, strikes,
attacks on civilians, the arms industry and the money behind it, bases,
officials, law, nuclear forces and humanitarian consequence, worldwide.

Self-contained: fetching, feed parsing, word-edge matching and deduplication are
all in this file. Reads sources_conflict.json, writes wire_conflict.json.
Standard library only — no dependencies, no API keys, no model calls.

This is a monitor, not a briefing. It collects what publishers reported and
never characterises it: no side is described, no claim is assessed, nothing is
summarised. Headlines and snippets are the publishers' own and every row links
to the original.

Two directions are marked. Escalation — offensives launched, strikes carried
out, contracts signed, budgets raised, forces deployed. And de-escalation —
ceasefires, withdrawals, treaties, prisoner exchanges, aid corridors opened.
Feeds of this subject overwhelmingly show the first; marking the second makes
it findable.

    python3 harvest_conflict.py
    python3 harvest_conflict.py --dry-run
    python3 harvest_conflict.py --fixtures DIR
"""

import argparse
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, "sources_conflict.json")
OUT_PATH = os.path.join(HERE, "wire_conflict.json")

RETAIN_DAYS = 45
MAX_ITEMS = 1200
WORKERS = 10         # a few hundred wires now
NOTABLE_SCORE = 3       # at or above this a story is marked as well documented

# --------------------------------------------------------------------------
# Plumbing: fetching, feed parsing, word-edge matching, fingerprints.
# --------------------------------------------------------------------------
USER_AGENT = ("Mozilla/5.0 (compatible; space-life-news/1.0; "
              "+https://github.com/WelcomeToYourGalaxy/space-life-news)")

TIMEOUT = 25

SNIPPET_CHARS = 240

TAG_RE = re.compile(r"<[^>]+>")

WS_RE = re.compile(r"\s+")

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

def build_gnews_url(loc):
    # the wire keeps 45 days, so ask the search for the same span rather than 30
    q = loc["query"] + " when:45d"
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
            "&hl=" + loc["hl"] + "&gl=" + loc["gl"] + "&ceid=" + loc["ceid"])

def fetch(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Encoding": "gzip",
                "Accept-Language": "*",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except Exception as exc:                       # noqa: BLE001 — report, don't crash the run
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print("  ! unreachable: %s (%s)" % (url[:90], last), file=sys.stderr)
    return None

def strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag

def text_of(el):
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", el.text or ""))).strip() if el is not None else ""

def child(node, *names):
    for kid in node:
        if strip_ns(kid.tag) in names:
            return kid
    return None

def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None

def parse_feed(raw, src):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Some publishers serve a stray byte before the declaration.
        try:
            root = ET.fromstring(raw[raw.index(b"<"):])
        except Exception:  # noqa: BLE001
            return []

    nodes = [n for n in root.iter() if strip_ns(n.tag) == "item"]
    atom = False
    if not nodes:
        nodes = [n for n in root.iter() if strip_ns(n.tag) == "entry"]
        atom = True

    out = []
    for n in nodes:
        title = text_of(child(n, "title"))
        if atom:
            link = ""
            for kid in n:
                if strip_ns(kid.tag) == "link" and kid.get("rel", "alternate") == "alternate":
                    link = kid.get("href", "")
                    break
        else:
            link_el = child(n, "link")
            link = (link_el.text or "").strip() if link_el is not None else ""
            if not link:
                link = text_of(child(n, "guid"))
        if not title or not link:
            continue

        outlet_el = child(n, "source")
        outlet = text_of(outlet_el) if outlet_el is not None else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -(len(outlet) + 3)].strip()
        elif not outlet and src["name"].startswith("Google News") and " - " in title:
            # Google News appends the outlet to the headline when it omits <source>.
            head, _, tail = title.rpartition(" - ")
            if head and 2 <= len(tail) <= 45:
                title, outlet = head.strip(), tail.strip()

        stamp = parse_date(text_of(child(n, "pubDate", "published", "updated", "date")))
        snippet = text_of(child(n, "description", "summary", "content"))[:SNIPPET_CHARS]

        out.append({
            "t": title,
            "u": link,
            "o": outlet or src["name"].replace("Google News · ", ""),
            "g": src["lang"],
            "r": src["region"],
            "k": src.get("kind", "news"),
            "d": stamp,
            "s": snippet,
            "w": src["name"],
        })
    return out

def _compile(term):
    if any(ord(ch) > 0x24F for ch in term):        # non-Latin script
        # substring matching is already prefix-like in scripts without word
        # breaks, so a trailing * is a no-op — strip it rather than search for
        # a literal asterisk, which is what used to happen.
        return term[:-1] if term.endswith("*") else term
    if term.endswith("*"):
        return re.compile(r"(?<![a-z0-9])" + re.escape(term[:-1]) + r"[a-z0-9\-]*", re.I)
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I)

def _compile_all(terms):
    return [_compile(t) for t in terms]

def hit(text, compiled):
    """True when any compiled term matches."""
    for c in compiled:
        if isinstance(c, str):
            if c in text:
                return True
        elif c.search(text):
            return True
    return False

def fingerprint(title):
    norm = PUNCT_RE.sub(" ", title.lower())
    return " ".join(WS_RE.sub(" ", norm).strip().split()[:9])

def canon_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query)
        query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"),
                                        urllib.parse.urlencode(query), ""))
    except Exception:  # noqa: BLE001
        return url


# --------------------------------------------------------------------------
# Where the story is, in three levels: region, subregion, place. A story naming
# Peru files under Peru, the Andes and Latin America at once, so the page can
# open a continent and drill into it rather than offering ten flat buckets.
# --------------------------------------------------------------------------
# region → subregion → country, with the terms that match each country.
# Matching a country implies its subregion and its region, so a story naming
# Peru files under Peru, South America and Latin America at once.
GEO3 = [
 ("africa", "Africa", [
   ("africa-e", "East Africa", [
     ("ke","Kenya",["kenya","kenyan","nairobi","ogiek","maasai","samburu","turkana"]),
     ("tz","Tanzania",["tanzania","tanzanian","ngorongoro","hadza","serengeti"]),
     ("ug","Uganda",["uganda","ugandan","batwa uganda","karamoja"]),
     ("et","Ethiopia",["ethiopia","ethiopian","omo valley","oromia"]),
     ("so","Somalia",["somalia","somali","somaliland"]),
     ("rw","Rwanda",["rwanda","rwandan"]),
     ("bi","Burundi",["burundi"]),
     ("sd","Sudan",["sudan","sudanese","darfur"]),
     ("ss","South Sudan",["south sudan","dinka","nuer"]),
     ("mg","Madagascar",["madagascar","malagasy"]),
     ("mz","Mozambique",["mozambique","cabo delgado"]),
     ("zm","Zambia",["zambia","zambian"]),
     ("zw","Zimbabwe",["zimbabwe","zimbabwean"]),
     ("mw","Malawi",["malawi"]),
     ("horn","Horn of Africa",["horn of africa"]),
   ]),
   ("africa-w", "West Africa", [
     ("ng","Nigeria",["nigeria","nigerian","ogoni","niger delta","ijaw"]),
     ("gh","Ghana",["ghana","ghanaian"]),
     ("ci","Côte d'Ivoire",["côte d'ivoire","ivory coast","ivorian"]),
     ("sn","Senegal",["senegal","senegalese","casamance"]),
     ("ml","Mali",["mali","malian","bamako","tuareg"]),
     ("bf","Burkina Faso",["burkina faso"]),
     ("ne","Niger",["niger republic","nigerien"]),
     ("lr","Liberia",["liberia","liberian"]),
     ("sl","Sierra Leone",["sierra leone"]),
     ("gn","Guinea",["guinea conakry","guinean"]),
     ("cm","Cameroon",["cameroon","cameroonian","baka"]),
     ("sahel","Sahel",["sahel","liptako","lake chad basin"]),
   ]),
   ("africa-c", "Central Africa", [
     ("cd","DR Congo",["democratic republic of congo","drc","congolese","kivu","batwa"]),
     ("cg","Congo-Brazzaville",["republic of congo","brazzaville"]),
     ("ga","Gabon",["gabon","gabonese"]),
     ("cf","Central African Republic",["central african republic"]),
     ("td","Chad",["chad","chadian"]),
   ]),
   ("africa-s", "Southern Africa", [
     ("za","South Africa",["south africa","south african","khoisan","khoi","xolobeni"]),
     ("bw","Botswana",["botswana","san people","central kalahari"]),
     ("na","Namibia",["namibia","namibian","himba","ovahimba"]),
     ("ao","Angola",["angola","angolan"]),
     ("ls","Lesotho",["lesotho"]),
   ]),
   ("africa-n", "North Africa", [
     ("ma","Morocco",["morocco","moroccan","amazigh","berber","western sahara","sahrawi"]),
     ("dz","Algeria",["algeria","algerian","kabyle"]),
     ("tn","Tunisia",["tunisia"]),
     ("ly","Libya",["libya","libyan","tuareg libya"]),
     ("eg","Egypt",["egypt","egyptian","nubian"]),
   ]),
 ]),
 ("americas-n", "North America", [
   ("na-us", "United States", [
     ("us-ak","Alaska",["alaska","alaskan","inupiat","yupik","gwich'in"]),
     ("us-sw","US Southwest",["navajo","diné","hopi","apache","arizona tribe","new mexico pueblo","tohono o'odham"]),
     ("us-pl","US Plains & Midwest",["standing rock","lakota","dakota access","oglala","cheyenne river","ojibwe","anishinaabe"]),
     ("us-pnw","US Pacific Northwest",["yakama","nez perce","puyallup","lummi","columbia river treaty","klamath"]),
     ("us-e","US East & South",["cherokee","seminole","lumbee","penobscot","wampanoag","mashpee"]),
     ("us-hi","Hawai'i",["native hawaiian","kanaka maoli","mauna kea","hawaii"]),
   ]),
   ("na-ca", "Canada", [
     ("ca-bc","British Columbia",["british columbia","wet'suwet'en","haida","coastal gitxsan","secwepemc"]),
     ("ca-pr","Prairies",["alberta","saskatchewan","manitoba","treaty 8","treaty 6"]),
     ("ca-on","Ontario & Quebec",["ontario first nation","quebec","grassy narrows","innu","cree quebec","atikamekw"]),
     ("ca-n","Northern Canada",["nunavut","northwest territories","yukon","inuit nunangat","dene"]),
     ("ca-at","Atlantic Canada",["mi'kmaq","nova scotia","new brunswick","newfoundland","innu labrador"]),
   ]),
   ("na-mx", "Mexico", [
     ("mx-s","Southern Mexico",["chiapas","oaxaca","zapatista","zapoteco","mixe","tren maya","yucatán","maya"]),
     ("mx-n","Northern Mexico",["yaqui","rarámuri","tarahumara","sonora","chihuahua"]),
   ]),
 ]),
 ("americas-s", "Latin America & Caribbean", [
   ("la-amz", "Amazon Basin", [
     ("br-amz","Brazilian Amazon",["yanomami","munduruku","kayapó","xingu","terra indígena","amazônia","rondônia","pará"]),
     ("pe-amz","Peruvian Amazon",["loreto","ucayali","madre de dios","awajún","shipibo","kakataibo"]),
     ("co-amz","Colombian Amazon",["amazonas colombia","putumayo","caquetá"]),
     ("ec-amz","Ecuadorian Amazon",["yasuní","waorani","sarayaku","sucumbíos","achuar"]),
     ("bo-amz","Bolivian Amazon",["tipnis","beni","chiquitano","bolivian amazon"]),
     ("ve-amz","Venezuelan Amazon",["arco minero","amazonas venezuela","pemón"]),
   ]),
   ("la-and", "Andes & Southern Cone", [
     ("cl","Chile",["chile","chilean","mapuche","araucanía","wallmapu"]),
     ("ar","Argentina",["argentina","argentine","patagonia","qom","wichí"]),
     ("pe","Peru",["peru","peruvian","quechua","aymara peru"]),
     ("bo","Bolivia",["bolivia","bolivian","aymara","quechua bolivia"]),
     ("py","Paraguay",["paraguay","ayoreo","chaco paraguayo"]),
     ("uy","Uruguay",["uruguay"]),
   ]),
   ("la-ca", "Central America", [
     ("gt","Guatemala",["guatemala","guatemalan","ixil","k'iche'","q'eqchi'"]),
     ("hn","Honduras",["honduras","garífuna","lenca","berta cáceres"]),
     ("ni","Nicaragua",["nicaragua","miskito","bosawás"]),
     ("cr","Costa Rica",["costa rica","bribri","térraba"]),
     ("pa","Panama",["panama","guna","ngäbe","emberá"]),
     ("bz","Belize",["belize","maya belize"]),
     ("sv","El Salvador",["el salvador"]),
   ]),
   ("la-car", "Caribbean & Guianas", [
     ("gy","Guyana",["guyana","wapichan","rupununi"]),
     ("sr","Suriname",["suriname","saamaka","maroon suriname","kaliña"]),
     ("gf","French Guiana",["guyane","french guiana","wayana"]),
     ("do","Caribbean islands",["dominica kalinago","caribbean indigenous","taino","haiti","jamaica","puerto rico"]),
   ]),
   ("la-br", "Brazil (other)", [
     ("br-ne","Brazil northeast & cerrado",["cerrado","bahia indígena","maranhão","quilombola","pataxó","guarani-kaiowá","mato grosso do sul"]),
   ]),
 ]),
 ("asia-s", "South Asia", [
   ("sa-in", "India", [
     ("in-c","Central India",["chhattisgarh","jharkhand","odisha","madhya pradesh","hasdeo","niyamgiri","bastar"]),
     ("in-ne","Northeast India",["assam","manipur","nagaland","mizoram","meghalaya","arunachal"]),
     ("in-s","South & West India",["kerala adivasi","tamil nadu tribal","karnataka tribal","gujarat adivasi","maharashtra adivasi"]),
     ("in-h","Himalayan India",["ladakh","uttarakhand","himachal","sikkim"]),
   ]),
   ("sa-oth", "Rest of South Asia", [
     ("bd","Bangladesh",["bangladesh","chittagong hill tracts","jumma","chakma"]),
     ("np","Nepal",["nepal","tharu","newar","chepang"]),
     ("pk","Pakistan",["pakistan","balochistan","kalash"]),
     ("lk","Sri Lanka",["sri lanka","vedda"]),
     ("bt","Bhutan",["bhutan"]),
   ]),
 ]),
 ("asia-se", "Southeast Asia", [
   ("se-mar", "Maritime Southeast Asia", [
     ("id","Indonesia",["indonesia","indonesian","masyarakat adat","papua","west papua","kalimantan","dayak","sulawesi","sumatra","mentawai"]),
     ("ph","Philippines",["philippines","filipino","lumad","igorot","mindanao","cordillera","ancestral domain"]),
     ("my","Malaysia",["malaysia","sarawak","sabah","penan","orang asli","bakun"]),
     ("tl","Timor-Leste",["timor-leste","east timor"]),
     ("pg-ind","Papua New Guinea",["papua new guinea","bougainville","porgera"]),
   ]),
   ("se-main", "Mainland Southeast Asia", [
     ("th","Thailand",["thailand","karen thailand","bangkloi","chao lay","hill tribe"]),
     ("mm","Myanmar",["myanmar","burma","karen state","kachin","chin state","rakhine"]),
     ("vn","Vietnam",["vietnam","montagnard","central highlands vietnam"]),
     ("kh","Cambodia",["cambodia","bunong","ratanakiri"]),
     ("la","Laos",["laos","hmong laos"]),
   ]),
 ]),
 ("asia-e", "East & Central Asia", [
   ("ea-e", "East Asia", [
     ("tw","Taiwan",["taiwan","原住民族","傳統領域","amis","atayal","bunun"]),
     ("jp","Japan",["japan","ainu","hokkaido","okinawa","ryukyu"]),
     ("cn","China",["china","tibet","tibetan","xinjiang","uyghur","inner mongolia","yunnan minority"]),
     ("kr","South Korea",["south korea","republic of korea","seoul","south korean"]),
     ("mn","Mongolia",["mongolia","mongolian","dukha","tsaatan"]),
   ]),
   ("ea-c", "Central Asia & Siberia", [
     ("ru-sib","Siberia & Russian North",["siberia","evenki","nenets","khanty","yamal","sakha","chukotka","коренные малочисленные"]),
     ("kz","Kazakhstan",["kazakhstan"]),
     ("kg","Kyrgyzstan",["kyrgyzstan"]),
     ("uz","Uzbekistan",["uzbekistan"]),
   ]),
 ]),
 ("mena", "Middle East & North Africa", [
   ("me-lev", "Levant & Gulf", [
     ("il","Israel & Palestine",["bedouin","negev","naqab","palestinian land","israel","west bank"]),
     ("jo","Jordan",["jordan","bedouin jordan"]),
     ("iq","Iraq",["iraq","marsh arabs","yazidi","kurdistan iraq"]),
     ("ir","Iran",["iran","qashqai","bakhtiari","ahwazi"]),
     ("sa","Gulf states",["saudi arabia","uae","oman","qatar","kuwait"]),
     ("tr","Turkey",["turkey","türkiye","kurdish","hasankeyf","alevi"]),
   ]),
 ]),
 ("europe", "Europe", [
   ("eu-n", "Nordic & Arctic Europe", [
     ("no","Norway",["norway","norwegian","sápmi","fosen","finnmark"]),
     ("se","Sweden",["sweden","swedish","girjas","gällivare","kiruna","samer"]),
     ("fi","Finland",["finland","finnish","inari","sámi parliament"]),
     ("gl","Greenland",["greenland","kalaallit","nuuk"]),
     ("ru-eu","Russian Karelia & Kola",["kola peninsula","karelia","murmansk sami"]),
   ]),
   ("eu-o", "Rest of Europe", [
     ("ua","Ukraine",["ukraine","crimean tatars","krym"]),
     ("ru","Russia (European)",["russia","russian federation"]),
     ("eu","European Union",["european union","european commission","brussels"]),
     ("uk","United Kingdom",["united kingdom","britain","scotland","wales"]),
     ("es","Spain",["spain","spanish"]),
     ("fr","France",["france","french"]),
     ("de","Germany",["germany","german"]),
   ]),
 ]),
 ("oceania", "Oceania", [
   ("oc-au", "Australia", [
     ("au-n","Northern Australia",["northern territory","arnhem land","kimberley","juukan gorge","tiwi","gulf country"]),
     ("au-w","Western Australia",["western australia","pilbara","noongar","yindjibarndi"]),
     ("au-e","Eastern Australia",["queensland","new south wales","victoria aboriginal","wiradjuri","gunditjmara","adani","carmichael"]),
     ("au-c","Central & South Australia",["south australia","adnyamathanha","arrernte","alice springs","olympic dam"]),
   ]),
   ("oc-nz", "Aotearoa New Zealand", [
     ("nz","Aotearoa",["new zealand","aotearoa","māori","maori","iwi","waitangi","ngāi tahu","tainui"]),
   ]),
   ("oc-pac", "Pacific Islands", [
     ("fj","Fiji",["fiji","fijian","itaukei"]),
     ("nc","Kanaky New Caledonia",["new caledonia","kanaky","kanak","nouméa"]),
     ("sb","Solomon Islands",["solomon islands"]),
     ("vu","Vanuatu",["vanuatu","ni-vanuatu"]),
     ("ws","Polynesia & Micronesia",["samoa","tonga","tuvalu","kiribati","marshall islands","palau","guam","chamorro","tahiti","rapa nui","easter island"]),
   ]),
 ]),
 ("polar", "Arctic & Antarctic", [
   ("pol-arc", "Circumpolar", [
     ("arctic","Arctic Council region",["arctic council","circumpolar","inuit circumpolar","arctic indigenous"]),
   ]),
 ]),
]

# Approximate centroids for the gazetteer: place, then subregion, then region.
# A marker sits at the most specific level a story resolved to, so a story that
# names only "the Sahel" lands on the Sahel rather than nowhere.
# --------------------------------------------------------------------------
# The gazetteer above was built for territorial subjects and named the places
# those stories name. A conflict feed names different ones: Syria, Yemen,
# Afghanistan, North Korea and half of Europe were simply absent, and any story
# about them fell to "unlocated". These fill that in, and add the capitals and
# demonyms that headlines use instead of country names — "Kyiv says", "Pentagon
# confirms", "Israeli strikes" — which is the other half of what was missing.
# --------------------------------------------------------------------------
ADDITIONS = {
 "me-lev": [
   ("sy", "Syria", ["syria", "syrian", "damascus", "aleppo", "idlib", "homs", "latakia"]),
   ("lb", "Lebanon", ["lebanon", "lebanese", "beirut", "hezbollah", "south lebanon"]),
   ("ye", "Yemen", ["yemen", "yemeni", "sanaa", "houthi", "aden", "hodeidah"]),
   ("ps", "Gaza & West Bank", ["gaza", "west bank", "palestin*", "rafah", "khan younis", "ramallah", "jenin"]),
   ("sy-kur", "Kurdish north-east Syria", ["rojava", "sdf", "syrian democratic forces", "hasakah"]),
   ("cy", "Cyprus", ["cyprus", "cypriot", "nicosia"]),
 ],
 "ea-c": [
   ("af", "Afghanistan", ["afghanistan", "afghan", "kabul", "kandahar", "taliban"]),
   ("tj", "Tajikistan", ["tajikistan", "dushanbe"]),
   ("tm", "Turkmenistan", ["turkmenistan"]),
   ("am", "Armenia", ["armenia", "armenian", "yerevan", "nagorno-karabakh", "karabakh"]),
   ("az", "Azerbaijan", ["azerbaijan", "azerbaijani", "baku"]),
   ("ge", "Georgia (Caucasus)", ["georgia tbilisi", "georgian forces", "abkhazia", "south ossetia"]),
 ],
 "ea-e": [
   ("kp", "North Korea", ["north korea", "dprk", "pyongyang", "kim jong un"]),
   ("hk", "Hong Kong & Macau", ["hong kong", "macau"]),
 ],
 "eu-o": [
   ("pl", "Poland", ["poland", "polish", "warsaw"]),
   ("ro", "Romania", ["romania", "romanian", "bucharest"]),
   ("md", "Moldova", ["moldova", "chisinau", "transnistria"]),
   ("by", "Belarus", ["belarus", "belarusian", "minsk", "lukashenko"]),
   ("balt", "Baltic states", ["estonia", "latvia", "lithuania", "tallinn", "riga", "vilnius", "kaliningrad"]),
   ("balk", "Western Balkans", ["serbia", "kosovo", "bosnia", "belgrade", "pristina", "sarajevo", "republika srpska", "north macedonia", "montenegro", "albania"]),
   ("it", "Italy", ["italy", "italian", "rome"]),
   ("nl", "Netherlands & Belgium", ["netherlands", "dutch", "the hague", "belgium", "brussels"]),
   ("at", "Austria & Switzerland", ["austria", "vienna", "switzerland", "swiss", "geneva", "bern"]),
   ("pt", "Portugal", ["portugal", "lisbon"]),
   ("gr", "Greece", ["greece", "greek", "athens"]),
   ("ie", "Ireland", ["ireland", "irish", "dublin"]),
   ("hu", "Hungary & Czechia & Slovakia", ["hungary", "budapest", "czech", "prague", "slovakia", "bratislava"]),
 ],
 "na-us": [("us", "United States", ["united states", "u.s. military", "us military", "washington",
                                    "pentagon", "white house", "american forces", "us forces", "americans"])],
 "na-ca": [("ca", "Canada", ["canada", "canadian", "ottawa"])],
 "na-mx": [("mx", "Mexico", ["mexico", "mexican", "mexico city"])],
 "la-br": [("br", "Brazil", ["brazil", "brazilian", "brasília", "brasilia"])],
 "la-and": [("co", "Colombia", ["colombia", "colombian", "bogotá", "bogota"]),
            ("ve", "Venezuela", ["venezuela", "venezuelan", "caracas"])],
 "la-car": [("ht", "Haiti", ["haiti", "haitian", "port-au-prince"]),
            ("cu", "Cuba", ["cuba", "cuban", "havana"]),
            ("dom", "Dominican Republic", ["dominican republic", "santo domingo"])],
 "africa-e": [("er", "Eritrea", ["eritrea", "asmara"]),
              ("dj", "Djibouti", ["djibouti"]),
              ("km", "Comoros", ["comoros"])],
 "africa-w": [("bj", "Benin & Togo", ["benin", "togo", "cotonou", "lomé"]),
              ("mr", "Mauritania", ["mauritania", "nouakchott"]),
              ("gw", "Guinea-Bissau & Gambia", ["guinea-bissau", "gambia", "banjul"])],
 "oc-au": [("au", "Australia", ["australia", "australian", "canberra"])],
 "se-main": [("bn", "Brunei", ["brunei"])],
 "sa-oth": [("mv", "Maldives", ["maldives", "malé"])],
}

# capitals, demonyms and force names that headlines use instead of the country
TERM_EXTRAS = {
 "ua": ["kyiv", "kiev", "ukrainian", "kharkiv", "odesa", "donbas", "donetsk", "luhansk",
        "zaporizhzhia", "kherson", "crimea", "mariupol", "bakhmut"],
 "ru": ["moscow", "kremlin", "russian forces", "russians", "putin", "belgorod", "kursk region"],
 "il": ["israel", "israeli", "idf", "tel aviv", "jerusalem", "golan"],
 "iq": ["baghdad", "iraqi", "mosul", "erbil", "kurdistan region"],
 "ir": ["tehran", "iranian", "irgc", "revolutionary guard"],
 "tr": ["ankara", "istanbul", "turkish forces", "turkish army"],
 "sa": ["riyadh", "saudi", "abu dhabi", "dubai", "doha", "kuwait city", "manama", "muscat"],
 "eg": ["cairo", "egyptian", "sinai"],
 "ly": ["tripoli libya", "benghazi", "libyan"],
 "sd": ["khartoum", "sudanese", "darfur", "rsf", "rapid support forces", "port sudan"],
 "ss": ["juba", "south sudanese"],
 "so": ["mogadishu", "somali forces", "al-shabaab", "puntland"],
 "et": ["addis ababa", "ethiopian", "tigray", "amhara", "oromo"],
 "ke": ["nairobi", "kenyan"],
 "ng": ["abuja", "nigerian", "boko haram", "lagos", "borno"],
 "ml": ["bamako", "malian"], "bf": ["ouagadougou", "burkinabè", "burkinabe"],
 "ne": ["niamey", "nigerien"], "cm": ["yaoundé", "yaounde", "cameroonian"],
 "cd": ["kinshasa", "goma", "m23", "north kivu", "congolese army"],
 "za": ["pretoria", "johannesburg", "cape town"],
 "cn": ["beijing", "chinese military", "pla", "people's liberation army", "taiwan strait"],
 "tw": ["taipei", "taiwanese"], "jp": ["tokyo", "japanese", "self-defense forces"],
 "kr": ["seoul", "south korean"], "in-c": ["new delhi", "indian army", "indian forces"],
 "pk": ["islamabad", "pakistani", "rawalpindi", "kashmir"],
 "mm": ["naypyidaw", "myanmar military", "tatmadaw", "rakhine", "rohingya"],
 "th": ["bangkok", "thai army"], "ph": ["manila", "philippine army", "west philippine sea"],
 "id": ["jakarta", "indonesian military", "tni"], "vn": ["hanoi", "vietnamese"],
 "uk": ["london", "british forces", "royal navy", "raf", "whitehall", "ministry of defence"],
 "fr": ["paris", "french forces", "élysée", "elysee"],
 "de": ["berlin", "german forces", "bundeswehr"],
 "es": ["madrid", "spanish forces"],
 "no": ["oslo", "norwegian"], "se": ["stockholm", "swedish forces"],
 "fi": ["helsinki", "finnish forces"],
 "gt": ["guatemala city"], "hn": ["tegucigalpa"], "ni": ["managua"],
 "ar": ["buenos aires", "argentine"], "cl": ["santiago", "chilean"], "pe": ["lima", "peruvian"],
 "nz": ["wellington", "new zealand defence"],
 "eu": ["european commission", "eu council", "european parliament", "nato headquarters"],
}


def _merge_gazetteer():
    """Fold the additions into GEO3 in place, so everything downstream — the
    matcher, the payload, the map — sees one gazetteer."""
    by_sub = {}
    for _rid, _rl, subs in GEO3:
        for sid, _sl, places in subs:
            by_sub[sid] = places
    for sid, extra in ADDITIONS.items():
        if sid in by_sub:
            have = {pid for pid, _l, _t in by_sub[sid]}
            by_sub[sid].extend([e for e in extra if e[0] not in have])
    for _rid, _rl, subs in GEO3:
        for _sid, _sl, places in subs:
            for idx, (pid, label, terms) in enumerate(places):
                if pid in TERM_EXTRAS:
                    places[idx] = (pid, label, terms + [t for t in TERM_EXTRAS[pid] if t not in terms])


_merge_gazetteer()


COORDS = {
 # --- regions ---
 "africa": [1.5, 20.0], "americas-n": [45.0, -100.0], "americas-s": [-12.0, -60.0],
 "asia-s": [22.0, 79.0], "asia-se": [2.0, 112.0], "asia-e": [40.0, 100.0],
 "mena": [28.0, 42.0], "europe": [52.0, 15.0], "oceania": [-25.0, 140.0], "polar": [78.0, 0.0],
 # --- subregions ---
 "africa-e": [1.0, 37.0], "africa-w": [10.0, -2.0], "africa-c": [0.0, 20.0],
 "africa-s": [-24.0, 24.0], "africa-n": [28.0, 12.0],
 "na-us": [39.0, -98.0], "na-ca": [58.0, -100.0], "na-mx": [23.0, -102.0],
 "la-amz": [-4.0, -62.0], "la-and": [-25.0, -68.0], "la-ca": [14.0, -87.0],
 "la-car": [8.0, -60.0], "la-br": [-13.0, -47.0],
 "sa-in": [22.0, 79.0], "sa-oth": [27.0, 85.0],
 "se-mar": [-2.0, 118.0], "se-main": [16.0, 101.0],
 "ea-e": [35.0, 118.0], "ea-c": [50.0, 80.0],
 "me-lev": [32.0, 40.0],
 "eu-n": [65.0, 20.0], "eu-o": [50.0, 15.0],
 "oc-au": [-25.0, 134.0], "oc-nz": [-41.0, 174.0], "oc-pac": [-15.0, 170.0],
 "pol-arc": [80.0, 0.0],
 # --- places: Africa ---
 "ke": [0.2, 37.9], "tz": [-6.4, 34.9], "ug": [1.4, 32.3], "et": [9.1, 40.5],
 "so": [5.2, 46.2], "rw": [-1.9, 29.9], "bi": [-3.4, 29.9], "sd": [15.6, 30.2],
 "ss": [7.9, 30.0], "mg": [-18.8, 46.9], "mz": [-18.7, 35.5], "zm": [-13.1, 27.8],
 "zw": [-19.0, 29.2], "mw": [-13.3, 34.3],
 "ng": [9.1, 8.7], "gh": [7.9, -1.0], "ci": [7.5, -5.5], "sn": [14.5, -14.5],
 "ml": [17.6, -4.0], "bf": [12.2, -1.6], "ne": [17.6, 8.1], "lr": [6.4, -9.4],
 "sl": [8.5, -11.8], "gn": [9.9, -9.7], "cm": [7.4, 12.4], "sahel": [15.0, 2.0], "horn": [8.0, 45.0],
 "cd": [-4.0, 21.8], "cg": [-0.2, 15.8], "ga": [-0.8, 11.6], "cf": [6.6, 20.9], "td": [15.5, 18.7],
 "za": [-30.6, 22.9], "bw": [-22.3, 24.7], "na": [-22.9, 18.5], "ao": [-11.2, 17.9], "ls": [-29.6, 28.2],
 "ma": [31.8, -7.1], "dz": [28.0, 1.7], "tn": [33.9, 9.5], "ly": [26.3, 17.2], "eg": [26.8, 30.8],
 # --- places: North America ---
 "us-ak": [64.0, -152.0], "us-sw": [34.5, -110.0], "us-pl": [44.0, -100.0],
 "us-pnw": [46.5, -121.0], "us-e": [35.5, -80.0], "us-hi": [20.8, -156.3],
 "ca-bc": [54.0, -125.0], "ca-pr": [52.0, -106.0], "ca-on": [49.0, -80.0],
 "ca-n": [64.0, -105.0], "ca-at": [46.5, -63.0],
 "mx-s": [17.0, -94.0], "mx-n": [28.5, -108.0],
 # --- places: Latin America ---
 "br-amz": [-4.5, -60.0], "pe-amz": [-6.0, -75.0], "co-amz": [-1.0, -72.0],
 "ec-amz": [-1.5, -76.5], "bo-amz": [-14.5, -65.0], "ve-amz": [5.0, -65.0],
 "cl": [-35.7, -71.5], "ar": [-38.4, -63.6], "pe": [-9.2, -75.0], "bo": [-16.3, -63.6],
 "py": [-23.4, -58.4], "uy": [-32.5, -55.8],
 "gt": [15.8, -90.2], "hn": [15.2, -86.2], "ni": [12.9, -85.2], "cr": [9.7, -83.8],
 "pa": [8.5, -80.8], "bz": [17.2, -88.5], "sv": [13.8, -88.9],
 "gy": [4.9, -58.9], "sr": [3.9, -56.0], "gf": [3.9, -53.1], "do": [18.7, -70.2],
 "br-ne": [-10.0, -45.0],
 # --- places: South Asia ---
 "in-c": [21.5, 82.0], "in-ne": [26.0, 93.0], "in-s": [13.0, 77.5], "in-h": [32.0, 78.0],
 "bd": [23.7, 90.4], "np": [28.4, 84.1], "pk": [30.4, 69.3], "lk": [7.9, 80.8], "bt": [27.5, 90.4],
 # --- places: Southeast Asia ---
 "id": [-2.5, 118.0], "ph": [12.9, 121.8], "my": [4.2, 109.5], "tl": [-8.9, 125.7], "pg-ind": [-6.3, 143.9],
 "th": [15.9, 100.99], "mm": [21.9, 95.96], "vn": [14.1, 108.3], "kh": [12.6, 104.99], "la": [19.9, 102.5],
 # --- places: East & Central Asia ---
 "tw": [23.7, 121.0], "jp": [36.2, 138.3], "cn": [35.9, 104.2], "kr": [36.5, 127.9], "mn": [46.9, 103.8],
 "ru-sib": [62.0, 105.0], "kz": [48.0, 66.9], "kg": [41.2, 74.8], "uz": [41.4, 64.6],
 # --- places: MENA ---
 "il": [31.5, 35.0], "jo": [30.6, 36.2], "iq": [33.2, 43.7], "ir": [32.4, 53.7],
 "sa": [24.0, 45.0], "tr": [39.0, 35.2],
 # --- places: Europe ---
 "no": [64.6, 12.0], "se": [62.0, 15.0], "fi": [64.0, 26.0], "gl": [71.7, -42.6], "ru-eu": [67.5, 35.0],
 "ua": [48.4, 31.2], "ru": [56.0, 40.0], "eu": [50.8, 4.4], "uk": [54.0, -2.5],
 "es": [40.2, -3.7], "fr": [46.6, 2.4], "de": [51.2, 10.4],
 # --- places: Oceania ---
 "au-n": [-15.0, 133.0], "au-w": [-25.0, 121.0], "au-e": [-30.0, 148.0], "au-c": [-29.0, 135.0],
 "nz": [-41.0, 174.0], "fj": [-17.7, 178.0], "nc": [-21.3, 165.5], "sb": [-9.6, 160.2],
 "vu": [-15.4, 166.9], "ws": [-13.8, -172.1],
 # --- polar ---
 "arctic": [80.0, 0.0],
}

COORDS.update({
 "sy": [35.0, 38.0], "lb": [33.9, 35.9], "ye": [15.5, 48.0], "ps": [31.5, 34.5],
 "sy-kur": [36.4, 40.7], "cy": [35.1, 33.4],
 "af": [33.9, 67.7], "tj": [38.9, 71.3], "tm": [39.0, 59.6], "am": [40.1, 45.0],
 "az": [40.1, 47.6], "ge": [42.3, 43.4],
 "kp": [40.3, 127.5], "hk": [22.3, 114.2],
 "pl": [52.0, 19.1], "ro": [45.9, 25.0], "md": [47.0, 28.4], "by": [53.7, 27.95],
 "balt": [56.9, 24.6], "balk": [43.8, 20.5], "it": [42.5, 12.6], "nl": [52.1, 5.3],
 "at": [47.6, 12.0], "pt": [39.4, -8.2], "gr": [39.1, 22.0], "ie": [53.3, -8.0], "hu": [47.9, 18.5],
 "us": [39.0, -98.0], "ca": [58.0, -100.0], "mx": [23.0, -102.0], "br": [-13.0, -47.0],
 "co": [4.6, -74.1], "ve": [8.0, -66.0], "ht": [18.9, -72.3], "cu": [21.9, -79.5], "dom": [18.7, -70.2],
 "er": [15.3, 39.0], "dj": [11.8, 42.6], "km": [-11.9, 43.9],
 "bj": [8.5, 1.6], "mr": [20.0, -10.9], "gw": [12.4, -14.5],
 "au": [-25.0, 134.0], "bn": [4.5, 114.7], "mv": [3.2, 73.2],
})

# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------
TOPICS = [
    ("offensive", "Offensives & incursions", [
        ("offensive", ["military", "launched", "ground", "counter"]),
        ("invasion", ["military", "troops", "forces", "border", "full-scale"]),
        ("incursion*", None), ("crossed the border", None), ("ground operation", None),
        ("front line", None), ("frontline", None), ("advance", ["troops", "forces", "front"]),
        ("seized", ["town", "city", "village", "territory", "positions"]),
        ("captured", ["town", "city", "village", "territory", "positions"]),
        ("occupation", ["forces", "military", "territory"]), ("annex*", ["territory", "region"]),
        ("mobilis*", ["troops", "reservists"]), ("mobiliz*", ["troops", "reservists"]),
        ("ofensiva", ["militar", "lanzó"]), ("offensive militaire", None),
        ("militäroffensive", None), ("наступлени", None), ("наступ", ["військ", "росій"]),
        ("军事行动", None), ("軍事行動", None), ("軍事作戦", None), ("군사 작전", None),
    ]),
    ("strikes", "Strikes & bombardment", [
        ("airstrike*", None), ("air strike*", None), ("missile strike*", None),
        ("drone strike*", None), ("shelling", None), ("bombardment", None),
        ("artillery", None), ("rocket attack*", None), ("cruise missile*", None),
        ("ballistic missile*", None), ("loitering munition*", None), ("kamikaze drone*", None),
        ("air defence intercept*", None), ("air defense intercept*", None),
        ("frappe aérienne", None), ("luftangriff", None), ("ataque aéreo", None),
        ("ataque com drones", None), ("ракетный удар", None), ("ракетний удар", None),
        ("空襲", None), ("空爆", None), ("空袭", None), ("공습", None), ("غارة جوية", None),
        ("हवाई हमला", None), ("导弹袭击", None), ("飛彈攻擊", None), ("ミサイル攻撃", None),
    ]),
    ("civilians", "Civilians & atrocities", [
        ("civilians killed", None), ("civilian casualties", None), ("massacre*", None),
        ("hospital", ["hit", "struck", "shelled", "bombed"]),
        ("school", ["hit", "struck", "shelled", "bombed"]),
        ("market", ["bombed", "struck", "shelling"]),
        ("suicide bomb*", None), ("car bomb*", None), ("ied", ["blast", "attack", "roadside"]),
        ("terrorist attack*", None), ("claimed responsibility", None),
        ("hostage*", ["taken", "released", "held"]), ("abduct*", ["armed", "militants", "gunmen"]),
        ("gunmen", ["killed", "attacked", "opened fire"]), ("ambush", ["convoy", "patrol", "soldiers"]),
        ("mass grave*", None), ("sexual violence", ["conflict", "war", "troops"]),
        ("víctimas civiles", None), ("civils tués", None), ("zivilisten getötet", None),
        ("мирных жителей", None), ("平民 死亡", None), ("民間人 犠牲", None),
    ]),
    ("arms", "Arms industry & trade", [
        ("arms deal*", None), ("arms export*", None), ("arms sale*", None),
        ("weapons contract*", None), ("defence contract*", None), ("defense contract*", None),
        ("procurement", ["defence", "defense", "military", "weapons"]),
        ("order for", ["jets", "tanks", "missiles", "frigates", "howitzers", "drones"]),
        ("production line", ["ammunition", "missile", "shell", "artillery"]),
        ("export licen*", ["arms", "weapons", "military"]),
        ("lockheed", None), ("raytheon", None), ("rtx", ["defense", "missile"]), ("bae systems", None),
        ("rheinmetall", None), ("thales", ["defence", "defense", "missile"]), ("leonardo", ["defence", "defense"]),
        ("dassault", None), ("saab", ["gripen", "defence"]), ("kongsberg", None), ("elbit", None),
        ("hanwha", ["defense", "aerospace"]), ("norinco", None), ("rostec", None), ("hal", ["tejas", "defence"]),
        ("venta de armas", None), ("vente d'armes", None), ("rüstungsexport", None),
        ("поставки оружия", None), ("军售", None), ("武器輸出", None), ("무기 수출", None),
    ]),
    ("money", "Budgets & money", [
        ("defence budget", None), ("defense budget", None), ("military spending", None),
        ("military aid", None), ("aid package", ["military", "weapons", "security"]),
        ("supplemental", ["aid", "funding", "military"]), ("appropriation*", ["defence", "defense"]),
        ("sanction*", ["military", "arms", "defence", "weapons", "export"]),
        ("frozen assets", None), ("reconstruction fund", None), ("cost overrun", ["programme", "program", "jet", "ship"]),
        ("gasto militar", None), ("dépenses militaires", None), ("verteidigungshaushalt", None),
        ("оборонный бюджет", None), ("军费", None), ("防衛費", None), ("국방 예산", None),
    ]),
    ("bases", "Bases, forces & corridors", [
        ("military base", None), ("air base", None), ("naval base", None), ("basing agreement", None),
        ("troop deployment", None), ("deployed troops", None), ("garrison", None),
        ("port access", ["naval", "military"]), ("overflight", None), ("no-fly zone", None),
        ("exercise*", ["military", "joint", "naval", "live-fire"]), ("war games", None),
        ("peacekeep*", None), ("withdraw*", ["troops", "forces", "base"]),
        ("military corridor", None), ("supply line*", ["military", "front"]),
        ("base militaire", None), ("militärstützpunkt", None), ("военная база", None),
        ("军事基地", None), ("軍事基地", None), ("군사 기지", None),
    ]),
    ("command", "Officials & command", [
        ("defence minister", None), ("defense minister", None), ("defence secretary", None),
        ("chief of staff", ["army", "defence", "general", "military"]),
        ("general", ["appointed", "dismissed", "resigned", "commander"]),
        ("commander", ["appointed", "killed", "replaced", "dismissed"]),
        ("military coup", None), ("junta", None), ("mutiny", None),
        ("mercenar*", None), ("private military company", None), ("wagner", ["group", "mercenar"]),
        ("conscription", None), ("draft", ["military", "conscription", "mobilisation"]),
        ("desertion", ["soldiers", "troops"]), ("court martial", None),
        ("ministro de defensa", None), ("ministre de la défense", None), ("verteidigungsminister", None),
        ("министр обороны", None), ("国防部长", None), ("防衛大臣", None), ("국방부 장관", None),
    ]),
    ("law", "Law & accountability", [
        ("war crimes", None), ("crimes against humanity", None), ("genocide", ["case", "court", "inquiry", "charges"]),
        ("icc", ["warrant", "prosecutor", "charges", "court"]), ("international criminal court", None),
        ("international court of justice", None), ("arrest warrant", None),
        ("commission of inquiry", None), ("tribunal", ["war", "crimes", "conflict"]),
        ("ceasefire", None), ("truce", None), ("peace deal", None), ("peace talks", None),
        ("prisoner exchange", None), ("geneva convention*", None), ("arms embargo", None),
        ("alto el fuego", None), ("cessez-le-feu", None), ("waffenstillstand", None),
        ("прекращение огня", None), ("停火", None), ("停戦", None), ("휴전", None),
    ]),
    ("nuclear", "Nuclear & strategic", [
        ("nuclear warhead*", None), ("nuclear weapon*", None), ("nuclear test*", None),
        ("nuclear doctrine", None), ("icbm", None), ("hypersonic", None),
        ("missile test", None), ("enrichment", ["uranium", "nuclear", "inspectors"]),
        ("iaea", None), ("npt", ["treaty", "nuclear"]), ("new start", ["treaty"]),
        ("arms control", None), ("deterrence", None), ("submarine", ["ballistic", "nuclear", "patrol"]),
        ("armas nucleares", None), ("arme nucléaire", None), ("atomwaffen", None),
        ("ядерное оружие", None), ("核武器", None), ("核兵器", None), ("핵무기", None),
    ]),
    ("humanitarian", "Humanitarian consequence", [
        ("displaced", ["fighting", "conflict", "war", "offensive", "shelling"]),
        ("refugee*", ["fleeing", "conflict", "war", "border", "camp"]),
        ("famine", ["conflict", "war", "siege", "blockade"]),
        ("siege", None), ("blockade", None), ("aid convoy", None), ("humanitarian corridor", None),
        ("aid blocked", None), ("hunger", ["conflict", "war", "siege"]),
        ("casualt*", ["toll", "civilian", "reported"]), ("death toll", None),
        ("hospitals overwhelmed", None), ("cholera", ["conflict", "displacement", "camp"]),
        ("desplazados", ["conflicto", "violencia"]), ("déplacés", ["conflit", "combats"]),
        ("вынужденные переселенцы", None), ("难民", None), ("避難民", None),
    ]),
    ("cyber", "Cyber & information", [
        ("cyberattack*", ["military", "government", "infrastructure", "defence", "grid"]),
        ("hacked", ["ministry", "defence", "military", "government"]),
        ("disinformation campaign", None), ("information operation*", None),
        ("jamming", ["gps", "signal", "satellite"]), ("electronic warfare", None),
        ("surveillance", ["military", "drone", "satellite", "intelligence"]),
        ("intelligence agency", None), ("espionage", None), ("spy", ["arrested", "agency", "charges"]),
        ("кибератака", None), ("网络攻击", None), ("サイバー攻撃", None),
    ]),
]

# --------------------------------------------------------------------------
# The gate.
#
# MILITARY — the story concerns armed force, the institutions that hold it, or
#            the trade and money attached to it, in any of the feed's languages.
# BLOCK    — the metaphors. "Price war", "battle for market share", "attack" in
#            sport, the shooter franchises: this vocabulary is used constantly
#            for things that are not war, and without a block list they dominate.
# --------------------------------------------------------------------------
MILITARY = [
    "military", "armed forces", "army", "navy", "air force", "troops", "soldier*",
    "battalion", "brigade", "regiment", "garrison", "combatant*", "militia*", "insurgen*",
    "guerrilla*", "paramilitar*", "mercenar*", "armed group*", "armed conflict", "warfare",
    "war", "civil war", "fighting", "clashes", "hostilities", "combat", "firefight",
    "front line", "frontline", "offensive", "invasion", "incursion*",
    "occupation", "annexation", "ceasefire", "truce", "armistice", "peacekeep*",
    "airstrike*", "air strike*", "missile*", "rocket*", "artillery", "shelling", "bombardment",
    "drone strike*", "warplane*", "fighter jet*", "warship*", "submarine", "tank*", "howitzer*",
    "munition*", "ammunition", "weapon*", "arms deal*", "arms export*", "arms sale*",
    "defence ministry", "defense ministry", "defence budget", "defense budget",
    "defence contract*", "defense contract*", "nato", "pentagon", "general staff",
    "war crimes", "terrorist attack*", "suicide bomb*", "car bomb*", "ied",
    "nuclear weapon*", "nuclear warhead*", "icbm", "arms control", "arms embargo",
    "coup", "junta", "conscription", "mobilisation", "mobilization",
    "militar*", "ejército", "fuerzas armadas", "ofensiva", "guerra", "alto el fuego",
    "militaire", "armée", "forces armées", "guerre", "cessez-le-feu", "frappe",
    "militär*", "bundeswehr", "krieg", "waffenstillstand", "luftangriff", "rüstung*",
    "esercito", "guerra", "militare", "leger", "oorlog", "krig", "wojsko", "wojna",
    "армия", "войск", "военн", "война", "перемирие", "ракет", "обстрел",
    "армія", "військов", "війна", "ordu", "askeri", "savaş", "ateşkes",
    "جيش", "عسكري", "حرب", "قصف", "وقف إطلاق النار", "ارتش", "نظامی", "جنگ",
    "सेना", "सैन्य", "युद्ध", "हमला सैनिक", "সেনা", "সামরিক", "যুদ্ধ",
    "militer", "tentara", "perang", "quân sự", "quân đội", "chiến tranh",
    "ทหาร", "กองทัพ", "สงคราม", "โจมตีทางอากาศ", "หยุดยิง",
    "軍", "軍隊", "戦争", "自衛隊", "空襲", "空爆", "停戦", "ミサイル", "軍事",
    "军队", "军事", "战争", "空袭", "停火", "导弹", "军售",
    "군", "군사", "전쟁", "공습", "휴전", "미사일", "στρατ*", "πόλεμος", "צבא", "מלחמה",
    "jeshi", "vita vya", "shambulio la anga",
]

BLOCK = [
    # the metaphors, which are relentless
    "price war", "bidding war", "culture war", "war of words", "trade war", "turf war",
    "battle for market share", "battleground state", "war chest", "war room",
    "attack on the record", "under fire over comments", "war on drugs prices",
    "tug of war", "war paint", "fantasy football", "war memorial ceremony gift",
    # sport
    "premier league", "champions league", "nfl", "nba", "mlb", "world cup match",
    "boxing", "ufc", "wrestling", "esports", "counter-strike", "call of duty",
    "battlefield game", "war thunder", "video game", "box office", "season finale",
    # commerce and horoscopes
    "gift guide", "best deals", "coupon", "horoscope", "astrolog*", "zodiac", "tarot",
]

# --------------------------------------------------------------------------
# Direction: escalation, or a step away from it.
# --------------------------------------------------------------------------
ESCALATION = [
    "launched an offensive", "offensive launched", "invaded", "crossed the border",
    "airstrike", "missile strike", "drone strike", "shelling", "bombardment", "seized",
    "captured", "killed", "casualties", "deployed", "reinforcement*", "escalat*",
    "mobilis*", "canceled talks", "cancelled talks", "walked out of talks", "ultimatum",
    "arms deal signed", "weapons delivered", "budget increase", "test-fired", "test fired",
    "state of emergency", "martial law", "coup", "sanctions imposed", "blockade",
]
DEESCALATION = [
    "ceasefire", "truce", "armistice", "peace deal", "peace talks", "agreement reached",
    "withdraw*", "pullback", "pull back", "prisoner exchange", "prisoner swap",
    "aid corridor", "humanitarian corridor", "aid convoy allowed", "de-escalat*",
    "arms control agreement", "treaty signed", "inspectors admitted", "sanctions lifted",
    "released detainees", "resumed talks", "mediation", "negotiations opened",
    "alto el fuego", "cessez-le-feu", "waffenstillstand", "перемирие", "停火", "停戦", "휴전",
]

# --------------------------------------------------------------------------
# Evidence signals for the pressure score.
# --------------------------------------------------------------------------
DOCUMENTED = [
    "confirmed", "said in a statement", "announced", "signed", "approved", "awarded",
    "carried out", "struck", "killed", "wounded", "captured", "seized", "arrested",
    "charged", "verdict", "ruling", "warrant issued", "resolution adopted",
    "imposed sanctions", "delivered", "test-fired", "deployed", "withdrew",
]
INSTITUTIONAL = [
    "united nations", "security council", "ohchr", "icrc", "icc", "international court",
    "nato", "european union", "sipri", "acled", "iiss", "crisis group", "human rights watch",
    "amnesty international", "bellingcat", "airwars", "ministry of defence",
    "ministry of defense", "pentagon", "general staff", "official figures", "peer-reviewed",
    "commission of inquiry", "monitoring group", "satellite imagery",
]
MEASURED = [
    "killed", "wounded", "casualties", "death toll", "per cent", "percent", "%",
    "billion", "million", "kilometres", "kilometers", "troops", "systems", "rounds",
    "warheads", "aircraft", "vehicles", "displaced", "thousands of", "hundreds of",
]
PROJECTED = [
    "planned", "expected", "scheduled", "deadline", "talks due", "will begin",
    "by 2030", "next year", "in the coming weeks", "under negotiation", "proposed",
    "draft resolution", "pending approval",
]


MILITARY_C = _compile_all(MILITARY)
BLOCK_C = _compile_all(BLOCK)
ESCALATION_C = _compile_all(ESCALATION)
DEESCALATION_C = _compile_all(DEESCALATION)
DOCUMENTED_C = _compile_all(DOCUMENTED)
INSTITUTIONAL_C = _compile_all(INSTITUTIONAL)
MEASURED_C = _compile_all(MEASURED)
PROJECTED_C = _compile_all(PROJECTED)
TOPICS_C = [(tid, label, [(_compile(t), _compile_all(g) if g else None) for t, g in terms])
            for tid, label, terms in TOPICS]
GEO3_C = [(rid, rlabel, [(sid, slabel, [(pid, plabel, _compile_all(terms))
                                        for pid, plabel, terms in places])
                        for sid, slabel, places in subs])
          for rid, rlabel, subs in GEO3]


def relevant(text):
    """Armed force, the institutions that hold it, or the trade and money
    attached to it. The metaphors are refused."""
    if hit(text, BLOCK_C):
        return False
    return hit(text, MILITARY_C)


def kind_of(text):
    """Escalation, a step away from it, or both in one story."""
    kinds = []
    if hit(text, ESCALATION_C):
        kinds.append("escalation")
    if hit(text, DEESCALATION_C):
        kinds.append("deescalation")
    return kinds or ["escalation"]


def pressure(text, standing, placed):
    total, reasons = 0, []
    if hit(text, DOCUMENTED_C):
        total += 2
        reasons.append("documented")
    if hit(text, INSTITUTIONAL_C):
        total += 2
        reasons.append("institutional")
    if hit(text, MEASURED_C):
        total += 1
        reasons.append("measured")
    if hit(text, PROJECTED_C):
        total += 1
        reasons.append("projected")
    if placed:
        total += 1
        reasons.append("located")
    if standing in ("official", "research"):
        total += 1
        reasons.append("primary source")
    return total, reasons


def topics_for(text):
    hits = []
    for tid, _label, terms in TOPICS_C:
        for term, guards in terms:
            if not hit(text, [term]):
                continue
            if guards and not hit(text, guards):
                continue
            hits.append(tid)
            break
    return hits


def places_for(text):
    """Returns (regions, subregions, places). Naming a place implies the
    subregion and region above it."""
    regions, subs, places = [], [], []
    for rid, _rl, sublist in GEO3_C:
        for sid, _sl, plist in sublist:
            for pid, _pl, terms in plist:
                if not hit(text, terms):
                    continue
                if pid not in places:
                    places.append(pid)
                if sid not in subs:
                    subs.append(sid)
                if rid not in regions:
                    regions.append(rid)
    return (regions or ["unlocated"], subs or ["unlocated"], places or ["unlocated"])


def point_for(places, subs, regions):
    """The most specific point a story resolved to, or None if it named nowhere.
    A story about several places gets the first; the map is a locator, not an
    atlas of every mention."""
    for level in (places, subs, regions):
        for pid in level:
            if pid in COORDS:
                return COORDS[pid]
    return None


def load_sources():
    with open(SOURCES_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    srcs = []
    for s in cfg.get("direct", []):
        srcs.append({"name": s["name"], "lang": s["lang"], "standing": s["standing"],
                     "region": s["standing"], "kind": s.get("kind", "news"), "url": s["url"]})
    for block, prefix in (("gnews", "Google News · "), ("events", "Events · ")):
        for loc in cfg.get(block, []):
            srcs.append({"name": prefix + loc["label"], "lang": loc["lang"],
                         "standing": loc["standing"], "region": loc["standing"],
                         "kind": "news", "url": build_gnews_url(loc)})
    return srcs, cfg


def run(dry_run=False, fixtures=None):
    sources, cfg = load_sources()
    print("Reading %d wires…" % len(sources))

    def read(src):
        if fixtures:
            path = os.path.join(fixtures, re.sub(r"[^\w.-]", "_", src["name"]) + ".xml")
            if not os.path.exists(path):
                return src, None
            with open(path, "rb") as fh:
                return src, fh.read()
        return src, fetch(src["url"])

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for src, raw in pool.map(read, sources):
            results.append((src, raw))

    previous = []
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as fh:
                previous = json.load(fh).get("items", [])
        except Exception:  # noqa: BLE001
            previous = []

    seen_fp, seen_url, items = set(), set(), []

    def absorb(row):
        fp = fingerprint(row["t"])
        cu = canon_url(row["u"])
        if fp in seen_fp or cu in seen_url:
            return False
        seen_fp.add(fp)
        seen_url.add(cu)
        items.append(row)
        return True

    stats, ok_count, refused = [], 0, 0
    for src, raw in results:
        stat = {"name": src["name"], "lang": src["lang"], "standing": src["standing"],
                "region": src["standing"], "kept": 0, "refused": 0, "ok": False}
        if raw:
            stat["ok"] = True
            ok_count += 1
            for row in parse_feed(raw, src):
                text = (row["t"] + " " + row["s"]).lower()
                if hit(text, BLOCK_C):
                    stat["refused"] += 1
                    refused += 1
                    continue
                if not relevant(text):
                    continue
                regions, subs, places = places_for(text)
                total, reasons = pressure(text, src["standing"], regions != ["unlocated"])
                row["x"] = topics_for(text) or ["offensive"]
                row["w"] = regions
                row["sr"] = subs
                row["pl"] = places
                row["p"] = total
                row["y"] = reasons
                row["ll"] = point_for(places, subs, regions)
                row["st"] = src["standing"]
                row["k"] = kind_of(text)
                if absorb(row):
                    stat["kept"] += 1
        stats.append(stat)
        print("  %-36s %s" % (src["name"][:36],
                              "unreachable" if not raw
                              else "%d kept, %d refused" % (stat["kept"], stat["refused"])))

    fresh_urls = {canon_url(i["u"]) for i in items}
    for row in previous:
        if "x" in row:
            absorb(row)

    cutoff = int(time.time() * 1000) - RETAIN_DAYS * 86400000
    items = [i for i in items if (i.get("d") or cutoff + 1) >= cutoff]
    items.sort(key=lambda i: i.get("d") or 0, reverse=True)
    items = items[:MAX_ITEMS]
    fresh = sum(1 for i in items if canon_url(i["u"]) in fresh_urls)

    languages = {}
    for loc in cfg.get("gnews", []):
        languages.setdefault(loc["lang"], re.sub(r"\s*·.*$|\s*\(.*$|\s+\d+$", "", loc["label"]).strip())
    languages.setdefault("en", "English")

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {"stories": len(items), "new_this_run": fresh,
                   "languages": len({i["g"] for i in items}),
                   "notable": sum(1 for i in items if i.get("p", 0) >= NOTABLE_SCORE),
                   "escalation": sum(1 for i in items if "escalation" in i.get("k", [])),
                   "deescalation": sum(1 for i in items if "deescalation" in i.get("k", [])),
                   "refused": refused,
                   "wires_ok": ok_count, "wires_total": len(sources)},
        "notable_score": NOTABLE_SCORE,
        "languages": languages,
        "kinds": [
            {"id": "escalation", "label": "Escalation"},
            {"id": "deescalation", "label": "De-escalation"},
        ],
        "standings": [
            {"id": "official", "label": "Bodies, courts & ministries"},
            {"id": "research", "label": "Research & monitors"},
            {"id": "trade", "label": "Defence trade press"},
            {"id": "press", "label": "Press"},
        ],
        "topics": [{"id": tid, "label": label} for tid, label, _ in TOPICS],
        "geo": ([{"id": rid, "label": rlabel,
                  "subs": [{"id": sid, "label": slabel,
                            "places": [{"id": pid, "label": plabel} for pid, plabel, _t in places]}
                           for sid, slabel, places in subs]}
                 for rid, rlabel, subs in GEO3] +
                [{"id": "unlocated", "label": "No single region", "subs": []}]),
        "coords": COORDS,
        "sources": stats,
        "items": items,
    }

    print("\n%d stories (%d new, %d well documented) · %d escalation, %d de-escalation · %d refused · %d languages · %d/%d wires answered"
          % (len(items), fresh, payload["counts"]["notable"], payload["counts"]["escalation"],
             payload["counts"]["deescalation"], refused, payload["counts"]["languages"],
             ok_count, len(sources)))

    if dry_run:
        print("\n--dry-run: wire_conflict.json not written")
        return payload

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("Wrote %s (%.0f KB)" % (OUT_PATH, os.path.getsize(OUT_PATH) / 1024))
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures")
    args = ap.parse_args()
    run(dry_run=args.dry_run, fixtures=args.fixtures)
