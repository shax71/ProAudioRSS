#!/usr/bin/env python3
"""Fetch pro-audio RSS feeds, merge duplicate stories, and write dashboard.html.

Standard library only. Run: python3 build.py [--days N] [--open]
"""
import argparse
import concurrent.futures
import email.utils
import html
import json
import re
import subprocess
import urllib.error
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FEEDS = [
    ("Bedroom Producers Blog", "https://bedroomproducersblog.com/feed/"),
    ("Create Digital Music", "https://cdm.link/feed/"),
    ("MusicTech", "https://musictech.com/feed/"),
    ("Production Expert", "https://www.production-expert.com/production-expert-1?format=rss"),
    ("SonicState", "https://feeds.feedburner.com/SonicstatecomNews"),
    ("Synthtopia", "https://www.synthtopia.com/feed/"),
]

# Card tags are company names (plus DAWs, so you can mute "Logic Pro" without muting Apple).
# Only the title and the feed's categories are searched, never the summary, so a story that
# merely mentions a brand in passing isn't tagged with it. Mutes in the page are by tag name.
# Each entry is "Canonical Name" or ("Canonical Name", extra alias regex).
COMPANIES = [
    "Behringer", "Roland", "Boss", "Korg", "Yamaha", ("Moog", r"moog music"), "Arturia",
    ("Native Instruments", r"\bNI(?:’s|'s)?\b"), "Novation", "Focusrite", "Akai", "Alesis", "M-Audio",
    ("Universal Audio", r"\bUAD\b"), "Waves", "FabFilter", "iZotope", "Soundtoys", "Plugin Alliance",
    "Brainworx", "Slate Digital", "Softube", "u-he", ("Spitfire Audio", r"\bspitfire\b"),
    "Output", "Splice", ("Loopmasters", r"\bloopcloud\b"), "Elektron", "Teenage Engineering",
    "Sequential", "Oberheim", "Ableton", "Apple", "Avid", "Steinberg", "PreSonus", "Image-Line",
    "Bitwig", "Cockos", "Celemony", "Antares", "Eventide", "Strymon", "Neumann", "Shure",
    "Sennheiser", "AKG", "Audio-Technica", ("Rode", r"\bRØDE\b"), "Genelec", ("ADAM Audio", r"\bADAM\b"),
    "Focal", "KRK", "IK Multimedia", "Positive Grid", "Line 6", "Fender", "Gibson", "Teufel", "RME",
    "Apogee", "Antelope Audio", "MOTU", ("Solid State Logic", r"\bSSL\b"), ("Neve", r"\bAMS Neve\b"),
    "Tascam", "Allen & Heath", "Mackie", "Erica Synths", "Make Noise", "Mutable Instruments",
    "Intellijel", "Instruo", "Pittsburgh Modular", "Doepfer", "Soma", "Polyend", "Synthstrom",
    "1010music", "Expressive E", "ROLI", "ASM", "Groove Synthesis", "Waldorf", ("Nord", r"\bclavia\b"),
    "Casio", "Donner", ("M-Vave", r"\bM-Wave\b"), "Cherry Audio", "GForce", "D16", "Klevgrand",
    ("NUGEN Audio", r"\bNUGEN\b"), "Sonnox", "Kush", "Analog Obsession", "Pulsar Audio", "Tokyo Dawn",
    ("Valhalla DSP", r"\bvalhalla\b"), "Baby Audio", "Minimal Audio", "Xfer", "Vital", "Spectrasonics",
    "Toontrack", "XLN Audio", "Heavyocity", "Orchestral Tools", "EastWest", "Strezov Sampling",
    "Dolby", "Plugin Boutique", "Haken Audio", "Torso", "Squarp", "Melbourne Instruments", "UDO",
    "Black Lion", "Warm Audio", "Heritage Audio", "Lewitt", "sE Electronics", "Audient", "Lexicon",
    "AIR Music Tech", "Analogue Solutions", "Future Retro", "Isla Electronics", "Fangtooth",
    "Hologram", "Chase Bliss", "Meris", "Empress", "Sonicware", "Dreadbox", "Soundmachines",
]
DAWS = [
    ("Logic Pro", r"\blogic pro\b|\bapple logic\b|\blogic (?:x|11|12)\b|\bmainstage\b"),
    ("Pro Tools", r"\bpro tools\b(?! computer)"),  # Production Expert files Macs under "Pro Tools computer"
    ("Ableton Live", r"\bableton live\b|\bmax for live\b"),
    ("FL Studio", r"\bfl studio\b"),
    ("Cubase", r"\bcubase\b"),
    ("Bitwig Studio", r"\bbitwig studio\b"),
    ("Studio One", r"\bstudio one\b"),
    ("Reaper", r"\breaper\b"),
]


def _tag_re(entry):
    name, alias = entry if isinstance(entry, tuple) else (entry, None)
    rx = rf"(?<![\w-]){re.escape(name)}(?![\w-])" + (f"|{alias}" if alias else "")
    return name, re.compile(rx, re.I)


TAG_RES = [_tag_re(e) for e in COMPANIES] + [(name, re.compile(rx, re.I)) for name, rx in DAWS]

# Catches developers not in COMPANIES from headlines like "Pulsar Audio releases DigiTape".
EVENT_PREFIX = re.compile(r"^[\w ]+\d{2}:\s*")  # "Beijing 26: ", "MusicChina26: "
# ...and from "Melted Sounds Spectral Warp…" / "Beijing 26: Error Instruments - Smart Lab".
COMPANY_SUFFIX = re.compile(
    r"^((?:[A-Z0-9][\w-]*\s+){1,3}(?:Audio|Instruments|Synths|Music|Electronics|Sampling|Sounds|Software|Labs|DSP|Modular))\b"
)
NOT_A_COMPANY = {"build", "get", "win", "how", "inside", "new", "free", "the", "why", "what"}
LAUNCH_HEADLINE = re.compile(
    r"^(?:the\s+)?([A-Za-z0-9][\w’'.+-]*(?:\s+(?:[A-Z0-9][\w’'.+-]*|and|&)){0,4}?)(?:’s|'s)?\s+"
    r"(?i:launch(?:es)?|releases?|introduces?|intros|announces?|unveils?|debuts?|reveals?|offers|ships|teases|previews)\b"
)

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}

STOPWORDS = set("""
a an and are as at be by for from has have in into is it its new of on or the to with
launches launched introduces introduced announces announced releases released unveils
unveiled now available out free update updated gets get adds review hands first look
you your this that but how what why more lets can will just here our we
until limited time today week weekend sale deal deals price off exclusive coupon
january february march april may june july august september october november december
""".split())

# A title word seen in more items than this is too generic to identify a story.
RARE_MAX_DF = 6

SUMMARY_CHARS = 300


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ProAudioRSS/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.URLError:
        # macOS system Python's LibreSSL can't handshake with some hosts (e.g. cdm.link); curl can.
        return subprocess.run(
            ["curl", "-sSfL", "-m", "20", "-A", "Mozilla/5.0 ProAudioRSS/1.0", url],
            check=True, capture_output=True,
        ).stdout


def text_of(el, path):
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None else ""


def strip_html(raw):
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    # WordPress boilerplate: "The post X appeared first on Y."
    text = re.sub(r"The post .{0,300}? appeared first on .{0,80}?\.\s*$", "", text)
    text = re.sub(r"\s*…?\s*Read More\b.*$", " …", text)  # Synthtopia
    text = re.sub(r"\s+", " ", text)
    return re.sub(r" ([,.;:!?])", r"\1", text).strip()


def summarize(text):
    truncated = bool(re.search(r"(\[(?:…|\.\.\.)\]|\.\.\.|…)$", text))
    text = re.sub(r"\s*(\[(?:…|\.\.\.)\]|\.\.\.|…)$", "", text).strip()
    if len(text) <= SUMMARY_CHARS:
        if truncated and not text.endswith((".", "!", "?")):
            text = text.rsplit(" ", 1)[0].rstrip(",;:") + "…"  # feed cut mid-word
        return text
    cut = text[:SUMMARY_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > SUMMARY_CHARS * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def find_image(item, body_html):
    for path in ("media:thumbnail", "media:content"):
        el = item.find(path, NS)
        if el is not None and el.get("url"):
            return el.get("url")
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return enc.get("url")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', body_html or "")
    return html.unescape(m.group(1)) if m else ""


def parse_date(raw):
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_url(url):
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.startswith("utm_")]
    )
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def parse_feed(source, data):
    root = ET.fromstring(data)
    items = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out = []
    for it in items:
        title = strip_html(text_of(it, "title"))
        link = text_of(it, "link")
        if not link:
            link_el = it.find("atom:link", NS)
            link = link_el.get("href", "") if link_el is not None else ""
        body = text_of(it, "content:encoded") or text_of(it, "description") or text_of(it, "atom:summary")
        date = parse_date(text_of(it, "pubDate") or text_of(it, "dc:date") or text_of(it, "atom:updated"))
        if not title or not link:
            continue
        summary = summarize(strip_html(text_of(it, "description") or body))
        all_cats = [c.text.strip() for c in it.findall("category") if c.text]
        out.append({
            "title": title,
            "link": link,
            "source": source,
            "date": date,
            "summary": summary,
            "image": find_image(it, body),
            "categories": all_cats[:4],
            "tags": tag(title, all_cats),
        })
    return out


def tag(title, categories):
    head = " | ".join([title, *categories])
    tags = [name for name, rx in TAG_RES if rx.search(head)]
    bare = EVENT_PREFIX.sub("", title)
    found = []
    m = LAUNCH_HEADLINE.match(bare)
    if m:
        found = re.split(r"\s+(?:and|&)\s+", m.group(1))
    else:
        m = COMPANY_SUFFIX.match(bare)
        if m and m.group(1).split()[0].lower() not in NOT_A_COMPANY:
            found = [m.group(1)]
    for name in found:
        if not any(rx.search(name) for _, rx in TAG_RES) and name not in tags:
            tags.insert(0, name)
    return tags


def title_tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def headline(item):
    """SonicState titles are "Headline: Subtitle"; only the headline matches other sites."""
    title = EVENT_PREFIX.sub("", item["title"])
    return title.rsplit(": ", 1)[0] if item["source"] == "SonicState" else title


def same_story(a, b):
    if normalize_url(a["link"]) == normalize_url(b["link"]):
        return True
    if a["source"] == b["source"]:
        return False
    if a["date"] and b["date"] and abs(a["date"] - b["date"]) > timedelta(days=4):
        return False
    if SequenceMatcher(None, a["title"].lower(), b["title"].lower()).ratio() > 0.8:
        return True
    # Two shared rare words (brand + product name, typically) mark the same story.
    return len(a["_rare"] & b["_rare"]) >= 2


def dedupe(items):
    """Group items that describe the same story; the earliest/richest becomes the card."""
    items.sort(key=lambda i: i["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    df = Counter()
    for it in items:
        it["_tokens"] = title_tokens(headline(it))
        df.update(it["_tokens"])
    for it in items:
        it["_rare"] = {t for t in it["_tokens"] if df[t] <= RARE_MAX_DF}
    groups = []
    for it in items:
        for g in groups:
            if any(same_story(it, other) for other in g):
                g.append(it)
                break
        else:
            groups.append([it])

    cards = []
    for g in groups:
        primary = max(g, key=lambda i: (bool(i["image"]), len(i["summary"])))
        seen_sources = {primary["source"]}
        also = []
        for i in g:
            if i is not primary and i["source"] not in seen_sources:
                seen_sources.add(i["source"])
                also.append({"source": i["source"], "link": i["link"]})
        dates = [i["date"] for i in g if i["date"]]
        cards.append({
            "title": primary["title"],
            "link": primary["link"],
            "source": primary["source"],
            "summary": primary["summary"],
            "image": primary["image"],
            "categories": primary["categories"],
            "tags": list(dict.fromkeys(t for i in g for t in i["tags"])),
            "date": max(dates).isoformat() if dates else None,
            "also": also,
            "dupes": len(g) - 1,
        })
    return cards


def load_all():
    items, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        futures = {pool.submit(fetch, url): name for name, url in FEEDS}
        for fut in concurrent.futures.as_completed(futures):
            name = futures[fut]
            try:
                items.extend(parse_feed(name, fut.result()))
            except Exception as exc:  # one broken feed shouldn't sink the dashboard
                errors.append(f"{name}: {exc}")
    return items, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="how far back to keep articles")
    parser.add_argument("--open", action="store_true", help="open the dashboard when done")
    args = parser.parse_args()

    items, errors = load_all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    items = [i for i in items if i["date"] is None or i["date"] >= cutoff]
    cards = dedupe(items)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "sources": [name for name, _ in FEEDS],
        "tags": sorted({t for c in cards for t in c["tags"]}),
        "fetched": len(items),
        "cards": cards,
        "errors": errors,
    }
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    out = ROOT / "dashboard.html"
    out.write_text(template.replace("/*__DATA__*/null", data_json), encoding="utf-8")

    print(f"{len(items)} articles -> {len(cards)} cards ({len(items) - len(cards)} duplicates merged)")
    for e in errors:
        print(f"  feed error: {e}", file=sys.stderr)
    if args.open:
        subprocess.run(["open", str(out)], check=False)


if __name__ == "__main__":
    main()
