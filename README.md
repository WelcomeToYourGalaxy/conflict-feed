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
