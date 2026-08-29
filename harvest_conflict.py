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
     ("kr","Korea",["korea","korean"]),
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
