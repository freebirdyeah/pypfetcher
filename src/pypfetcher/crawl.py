#!/usr/bin/env python3
"""
crawl.py — one-shot scraper for libportal.manipal.edu question papers
outputs raw index.json with full tree structure

Usage:
    python crawl.py            # full crawl
    python crawl.py --year 2023  # single year for testing
"""

import requests
from bs4 import BeautifulSoup
import json, re, argparse, sys
from datetime import datetime, timezone
from urllib.parse import urljoin

BASE_URL = "https://libportal.manipal.edu/mit/Question%20Paper.aspx"
PDF_BASE = "https://libportal.manipal.edu/mit/"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer": BASE_URL,
})

YEAR_RE = re.compile(r"^(19|20)\d{2}$")
MONTH_RE = re.compile(
    r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
    re.IGNORECASE,
)
SEM_RE = re.compile(
    r"\b(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|1|2|3|4|5|6|7|8|9|10|11|12)(?:st|nd|rd|th)?\s*(?:sem|semester)\b",
    re.IGNORECASE,
)
PROGRAM_RE = re.compile(
    r"\b(?:b\.?tech|m\.?tech|m\.?sc|mca|mcis|msis|sois|icas|ug|pg)\b",
    re.IGNORECASE,
)


# ── ASP.NET helpers ──────────────────────────────────────────────────────────

def extract_hidden(soup):
    """Pull all the __VIEWSTATE* and __EVENTVALIDATION fields."""
    names = ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]
    return {n: (soup.find("input", {"name": n}) or {}).get("value", "") for n in names}


def do_postback(event_target, event_arg, hidden):
    """Fire a __doPostBack and return the resulting soup."""
    payload = {
        "__EVENTTARGET": event_target,
        "__EVENTARGUMENT": event_arg,
        **hidden,
    }
    r = session.post(BASE_URL, data=payload, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def normalize_segment(segment):
    segment = segment.strip()
    return re.sub(r"\s+", " ", segment)


def segment_kind(segment):
    normalized = normalize_segment(segment)
    lowered = normalized.lower()

    if YEAR_RE.fullmatch(normalized):
        return "year"
    if MONTH_RE.search(normalized) and YEAR_RE.search(normalized):
        return "session"
    if SEM_RE.search(normalized):
        return "semester"
    if PROGRAM_RE.search(normalized):
        return "program"
    if lowered in {"icas", "b.tech", "btech", "m.tech", "mtech", "m.sc", "msc", "mca", "mcis", "msis", "sois"}:
        return "program"
    return "folder"


def build_hierarchy(path):
    segments = [normalize_segment(part) for part in path]
    kinds = [segment_kind(part) for part in segments]

    attributes = {
        "year": next((part for part, kind in zip(segments, kinds) if kind == "year"), None),
        "session": next((part for part, kind in zip(segments, kinds) if kind == "session"), None),
        "program": next((part for part, kind in zip(segments, kinds) if kind == "program"), None),
        "semester": next((part for part, kind in zip(segments, kinds) if kind == "semester"), None),
    }

    return {
        "path": "/" + "/".join(segments) if segments else "/",
        "name": segments[-1] if segments else "/",
        "depth": len(segments),
        "kind": "root" if not segments else "folder",
        "segments": segments,
        "segment_kinds": kinds,
        "attributes": attributes,
    }


def build_paper_record(path, name, url):
    base = build_hierarchy(path)
    filename = name.strip()
    stem, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")

    record = {
        **base,
        "kind": "paper",
        "filename": filename,
        "stem": stem,
        "extension": ext.lower(),
        "title": stem,
        "url": url,
        "search_text": " ".join([*base["segments"], filename, stem]).strip(),
    }
    record["attributes"] = {
        **base["attributes"],
        "filename": filename,
        "stem": stem,
        "extension": ext.lower(),
    }
    return record


# ── Page parsing ─────────────────────────────────────────────────────────────

def parse_page(soup):
    folders, pdfs, up = [], [], None

    table = soup.find("table", id=re.compile(r"gvFiles"))
    if not table:
        return folders, pdfs, up

    for row in table.find_all("tr"):
        for link in row.find_all("a"):
            href = link.get("href", "")
            text = link.text.strip()

            if "__doPostBack" in href:
                if not text:          # empty = PDF row decoration, skip
                    continue
                m = re.search(r"__doPostBack\(['\"]?([^'\"&,]+)['\"]?,\s*['\"]?([^'\"&)]*)['\"]?\)", href)
                # also handle HTML-entity encoded version
                if not m:
                    m = re.search(r"__doPostBack\(&#39;([^&]+)&#39;,&#39;([^&]*)&#39;\)", href)
                if not m:
                    continue
                evt, arg = m.group(1), m.group(2)
                if ".." in text:
                    up = (evt, arg)
                else:
                    folders.append((text, evt, arg))

            elif href and "javascript" not in href and href not in ("#", ""):
                # direct file link — resolve relative to site root
                full_url = urljoin("https://libportal.manipal.edu/mit/", href)
                # normalize ../RootFolder → correct absolute
                if href.lower().endswith(".pdf"):
                    pdfs.append((text, full_url))

    return folders, pdfs, up

# ── DFS crawler ──────────────────────────────────────────────────────────────

def crawl_node(soup, hidden, path, papers, depth=0):
    """
    Recursively crawl the current listing.
    Returns a dict representing this node in the tree.
    """
    indent = "  " * depth
    folders, pdfs, up = parse_page(soup)

    node = build_hierarchy(path)
    node.update({
        "folders": {},
        "pdfs": [],
    })

    # collect pdfs at this level
    for name, url in pdfs:
        paper = build_paper_record(path, name, url)
        node["pdfs"].append(paper)
        papers.append(paper)
        print(f"{indent}  📄 {name}")

    # recurse into subfolders
    for name, evt, arg in folders:
        print(f"{indent}📁 {'/'.join(path + [name])}")
        child_soup = do_postback(evt, arg, hidden)
        child_hidden = extract_hidden(child_soup)
        child_node = crawl_node(child_soup, child_hidden, path + [name], papers, depth + 1)
        node["folders"][name] = child_node

        # go back up using the .. link
        if up:
            parent_soup = do_postback(up[0], up[1], child_hidden)
            # refresh hidden from the parent page we just navigated back to
            hidden.update(extract_hidden(parent_soup))
        else:
            # no .. found — shouldn't happen past root, but fallback: re-GET root
            print(f"{indent}  ⚠ no .. link found at {path}, re-GETting root", file=sys.stderr)
            root_soup = session.get(BASE_URL, timeout=30)
            hidden.update(extract_hidden(BeautifulSoup(root_soup.text, "html.parser")))

    return node


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", help="Crawl a single year only (for testing)")
    parser.add_argument("--start-year", type=int, help="Start year (inclusive)")
    parser.add_argument("--end-year", type=int, help="End year (inclusive)")
    parser.add_argument("--out", default="index.json", help="Output file")
    args = parser.parse_args()

    print("Fetching root page...")
    r = session.get(BASE_URL, timeout=30)
    r.raise_for_status()
    root_soup = BeautifulSoup(r.text, "html.parser")
    hidden = extract_hidden(root_soup)

    folders, pdfs, _ = parse_page(root_soup)
    print(f"Found {len(folders)} year folders at root: {[f[0] for f in folders]}\n")

    if args.start_year is not None and args.end_year is not None and args.start_year > args.end_year:
        parser.error("--start-year cannot be greater than --end-year")

    if args.start_year is not None or args.end_year is not None:
        start_year = args.start_year if args.start_year is not None else -sys.maxsize
        end_year = args.end_year if args.end_year is not None else sys.maxsize
        filtered_folders = []

        for name, evt, arg in folders:
            match = YEAR_RE.search(name)
            if not match:
                continue

            year = int(match.group(0))
            if start_year <= year <= end_year:
                filtered_folders.append((name, evt, arg))

        folders = filtered_folders
        if not folders:
            range_label = f"{args.start_year or ''}-{args.end_year or ''}"
            print(f"No year folders found in range {range_label}")
            sys.exit(1)

    if args.year:
        # filter to a single year for quick testing
        folders = [f for f in folders if f[0] == args.year]
        if not folders:
            print(f"Year {args.year} not found.")
            sys.exit(1)

    tree = {
        "meta": {
            "schema_version": 2,
            "base_url": BASE_URL,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "root": {},
        "papers": [],
    }

    for name, evt, arg in folders:
        print(f"\n📁 {name}")
        child_soup = do_postback(evt, arg, hidden)
        child_hidden = extract_hidden(child_soup)

        # after entering a year, re-parse root up-link context
        _, _, up = parse_page(child_soup)

        node = crawl_node(child_soup, child_hidden, [name], tree["papers"], depth=1)
        tree["root"][name] = node

        # go back to root after each year
        if up:
            parent_soup = do_postback(up[0], up[1], child_hidden)
            hidden.update(extract_hidden(parent_soup))
        else:
            r2 = session.get(BASE_URL, timeout=30)
            hidden.update(extract_hidden(BeautifulSoup(r2.text, "html.parser")))

    with open(args.out, "w") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Written to {args.out}")


if __name__ == "__main__":
    main()
