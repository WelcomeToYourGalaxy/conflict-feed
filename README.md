## Embedding

The embed frame is a FIXED height, written into the iframe tag in both the `height` attribute and the
inline style, so it exists before any script runs. The shell script only injects the document — no
measuring, no resize listener, no messages. To change the length, change both numbers in the tag.

Stacking switches at 1200px, not 900: a half-width column on a wide theme is often 800–1000px, which
cleared a 900px breakpoint and stayed side-by-side. Stacked, the list takes a third and scrolls; the map
and the guerillamap panel split the rest — `.cf-map-wrap.gm-open { flex: 0 0 68% }` in the stacked media
query is that split.

# conflict-feed

Armed conflict and the machinery around it, worldwide, in 25 languages: invasions and offensives,
strikes, attacks on civilians, the arms industry, budgets and military aid, bases and deployments,
officials and command, law and accountability, nuclear forces, humanitarian consequence, and cyber
operations.

`harvest_conflict.py` runs every two hours in GitHub Actions, reads 197 wires, marks whether a story
is escalation or de-escalation, grades the evidence, places it three levels deep, and writes
`wire_conflict.json`. `index.html` loads that file and renders it.

## It does not take a side

Headlines and snippets are the publishers' own, truncated but never reworded. No claim is assessed,
no party described, nothing summarised, and no model touches the text. Every row links to the
original.

On a subject where the same event is reported irreconcilably from opposite directions, what a feed
can honestly do is show who published what and let them be read against each other. That is what the
source labels are for, and why outlets on several sides of ongoing wars are all carried.

A high evidence score is not a claim of truth. An official statement scores well and can still be
false. The score records what kind of thing a story is, not whether it is right.

## Direction

**Escalation** — offensives launched, strikes carried out, weapons delivered, budgets raised, forces
deployed, talks abandoned.

**De-escalation** — ceasefires, truces, withdrawals, prisoner exchanges, aid corridors opened,
treaties signed, sanctions lifted.

Feeds of this subject overwhelmingly show the first. Marking the second makes it findable. A story
can carry both — a ceasefire announced after a night of strikes — and then it appears under both.

## Who reports it

| Standing | What it covers |
|---|---|
| Bodies, courts & ministries | UN mechanisms, OHCHR, ICRC, NATO, defence ministries, courts — parties as well as arbiters |
| Research & monitors | SIPRI, ACLED, IISS, Crisis Group, Human Rights Watch, Amnesty, Bellingcat, Airwars, The New Humanitarian |
| Defence trade press | Defense News, Breaking Defense, Naval News, Defence Blog |
| Press | Al Jazeera, the Guardian, Kyiv Independent, Middle East Eye, and 25 language editions |

## The map

A satellite panel sits beside the list, drawn from Esri's free World Imagery layer — no account, no
key, no tracking beyond the tiles the browser requests.

Stories sharing a coordinate are spread along a jittered phyllotaxis spiral rather than concentric
rings, so a crowded city reads as scattered rather than drawn. The spread is measured in screen
pixels against the current zoom, so the geographic error halves with every zoom step and the pins
converge on their true coordinates; below zoom 5 it is capped, and a crowded city collapses to one
dot whose popup still lists every story on it.

A companion panel under the map embeds guerillamap.com with a fixed overlay set, open by default, and
follows this map's centre and zoom by rebuilding the iframe address ~900ms after movement stops. One-way only: a cross-origin
frame cannot be read from this page. Their centre is dropped south by 22% of their frame height, since their panel is shorter and wider than
this map — `gmShift(0.3)` in the console to retune, `gmShift(0)` to switch it off. No guerillamap data is
copied or stored here; attribution sits under
the panel, as their terms require.

Pins scale with the zoom — about half size at world view so they do not blot out the plate, full size
from zoom 6 in.

Above the imagery sits the painted plate from the projects map, cross-faded out between zoom 3 and
zoom 5.6 and redrawn through the zoom animation, so the map reads as a drawn chart wide out and as ground close in. Its southern
edge carries the social-spheres envelope: the paint thins away through the forties instead of ending on
a ruled line. The imagery carries the
projects map's treatment: it supplies the luminance, a sea screen and a green soft-light wash shift
the colour, a warm overlay supplies the sunlight, and an Esri hillshade multiplied on top restores
the relief. Tints ease off with zoom, relief strengthens. There are no place labels — the pins carry
the names. Nothing is redrawn: every coastline is still Esri's. If the hillshade fails it is dropped
and the plain imagery stands.

Title and body type are Uncial Antiqua and Marcellus, matching the projects map, and the plate's
cartouche carries this feed's own name.

## Placement

Stories are placed by the names they contain — no geocoding service is called. The gazetteer covers
countries, cities, provinces, bases and waterways in Chinese, Japanese, Korean, Thai, Greek, Hebrew,
Arabic, Bengali, Hindi, Russian, Ukrainian, Turkish and the major European languages, so 基輔, Kiew,
キーву and Kyiv resolve to one point. Where a story names two countries, one carrying a locative
marker (`im`, `in`, `en`, `au`, `on`, `在`, `في`) is treated as the scene and the other as the actor.

Three outcomes, counted separately under the status line: pinned on a named place, pinned on a
country's centre, or not located and left off the map. On a 1,200-story sample: 230 named places,
786 countries, 184 unplaced — 85% mapped, against 35% before this pass. Every story that resolved to a place gets its
own pin, fanned onto a ring around the point so none hides another; the offsets are deterministic, so
pins do not move between renders.

Clicking a pin opens a box listing **every story within a few pixels of it**, not only the one
clicked — the clicked story first, then its neighbours with their sources and, where they differ,
their own places. Zoomed out, Gaza and Rafah open together because they genuinely overlap on screen;
zoom in and they separate. Every title links to its publisher exactly as a row does.

The pins are built from the same filtered set as the list, so every toggle applies to both. Stories
that named nowhere in particular are counted as unplaced beneath the map rather than dropped
somewhere false.

### How precise a pin is

The harvester looks for a named place first — roughly **260 cities, provinces, bases, islands and
waterways**: Kharkiv, Rafah, El Fasher, Goma, Mekelle, Ramstein, Al Udeid, Diego Garcia, Bab
el-Mandeb, the Taiwan Strait, the Line of Control. A point beats the area containing it, so "M23
advances on Goma in North Kivu" pins Goma, not the province.

Only when no such place is named does the pin fall back to a country centroid, then a subregion, then
a region. Both the row and the popup name whichever level was used, so the precision of any pin is
visible rather than implied. A country-level pin is not the location of an event, and the small
offset that separates neighbouring pins is not a location either.

Choosing a region, subregion or place frames the map on that geography in full, using the gazetteer
rather than the stories — so a quiet region still shows you the whole region. Subject, direction,
evidence and language filters leave the view where you left it.

The top bar reports how many of the whole wire are placed, and the strip under the map reports how
many of the current filter are drawn and how many are unplaced.

### Why a story is not mapped

It is placed only if its text names somewhere the gazetteer knows. Many headlines name nowhere —
"defence budget raised", "arms exports approved" — and are genuinely unplaceable. The rest come down
to gazetteer coverage: it carries **217 entries**, including capitals, demonyms and force names,
because headlines say Kyiv, Pentagon and Israeli far more often than they name the country.

The base gazetteer was built for territorial subjects and was missing most of what a war feed names —
Syria, Lebanon, Yemen, Gaza, Afghanistan, North Korea, Armenia, Azerbaijan, Belarus, the Baltics, the
Balkans, Haiti, Colombia, Venezuela and the United States as a single entry all had to be added.
`ADDITIONS` and `TERM_EXTRAS` at the top of `harvest_conflict.py` hold that work and are meant to
grow: adding a city, a force name or an armed group is one line, and the next harvest maps every
story naming it.

`COORDS` holds one coordinate per gazetteer id. If Leaflet cannot load, the panel hides itself and
the list works as before.

## Eleven subjects

Offensives & incursions, Strikes & bombardment, Civilians & atrocities, Arms industry & trade,
Budgets & money, Bases & forces & corridors, Officials & command, Law & accountability, Nuclear &
strategic, Humanitarian consequence, Cyber & information.

## Evidence

| Signal | Worth |
|---|---|
| A documented act: confirmed, announced, signed, carried out, a verdict, a warrant | 2 |
| Institutional material: UN body, court, ministry, SIPRI, ACLED, satellite imagery, inquiry | 2 |
| A measured figure | 1 |
| A scheduled or pending step | 1 |
| A named place | 1 |
| Primary source | 1 |

At **3** or more the row is marked well documented.

## What is refused

The metaphors, which are relentless in this vocabulary: price wars, bidding wars, culture wars, wars
of words, battles for market share, battleground states. Sport, where attacks and battles are
constant. The shooter franchises. The status line reports how many stories each harvest refused.

## Files

| File | Path in repo | What it is |
|---|---|---|
| `index.html` | `/index.html` | The feed page. Pages serves the repo root, so it must carry this name. |
| `harvest_conflict.py` | `/harvest_conflict.py` | The harvester. Self-contained. |
| `sources_conflict.json` | `/sources_conflict.json` | The wire list, with each wire's standing. |
| `wire_conflict.json` | `/wire_conflict.json` | The output the page reads. Empty placeholder until the first run. |
| `conflict-feed-weebly-embed.html` | `/conflict-feed-weebly-embed.html` | The page wrapped for a Weebly Embed Code element. |
| `verify_sources.py` | `/verify_sources.py` | Reports which wires answer and which are dead. |
| `README.md` | `/README.md` | This file. |
| `harvest.yml` | `/.github/workflows/harvest.yml` | Runs every two hours at :37 and commits the wire. |
| `verify.yml` | `/.github/workflows/verify.yml` | The manual wire check. |

## Setup

1. Push these files to the repository root.
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**, save.
3. Actions tab → **Harvest the conflict wire** → *Run workflow*.
4. Settings → Pages → **Deploy from a branch**, branch `main`, folder `/ (root)`.
5. Confirm
   `https://raw.githubusercontent.com/WelcomeToYourGalaxy/conflict-feed/main/wire_conflict.json`
   loads in a browser.

If the repository is named something else, change `REPO` near the top of the feed script in
`index.html` and regenerate the embed.

## Limits worth knowing

The gate is mechanical: it reads words, not meaning. Casualty figures in headlines are claims by
whoever supplied them, and this feed neither verifies nor reconciles them. Coverage of any given war
is uneven and partisan by language; the language counts show the shape of that rather than correcting
it. Google News caps a query at roughly 100 results over about 45 days.

## Running it locally

```bash
python3 harvest_conflict.py              # full run
python3 harvest_conflict.py --dry-run    # harvest and report, write nothing
python3 verify_sources.py                # which wires are dead
```

Python 3.9 or later.
