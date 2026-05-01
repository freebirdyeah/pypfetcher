import json
import re
from pathlib import Path

ROMAN_NUMERALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
}

PROGRAM_ALIASES = {
    "btech": "btech",
    "be": "btech",
    "bacheloroftechnology": "btech",
    "mtech": "mtech",
    "masteroftechnology": "mtech",
    "msc": "msc",
    "masterofscience": "msc",
    "mca": "mca",
    "mcis": "mcis",
    "msis": "msis",
    "sois": "sois",
    "icas": "icas",
    "ug": "ug",
    "pg": "pg",
}

SEMESTER_ROMAN = {
    "1": "I",
    "2": "II",
    "3": "III",
    "4": "IV",
    "5": "V",
    "6": "VI",
    "7": "VII",
    "8": "VIII",
    "9": "IX",
    "10": "X",
    "11": "XI",
    "12": "XII",
}


def compact(value):
    if value is None:
        return ""
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def normalize_program(value):
    normalized = compact(value)
    return PROGRAM_ALIASES.get(normalized, normalized)


def normalize_semester(value):
    normalized = str(value or "").strip().lower()
    match = re.search(r"\b([ivx]+|\d{1,2})(?:st|nd|rd|th)?\s*(?:sem|semester)\b", normalized)
    if not match:
        return compact(value)

    token = match.group(1)
    semester = ROMAN_NUMERALS.get(token, token)
    return f"{semester}sem"


def normalize_filter_value(key, value):
    if key == "program":
        return normalize_program(value)
    if key == "semester":
        return normalize_semester(value)
    return compact(value)


def semester_label(value):
    normalized = normalize_semester(value)
    match = re.fullmatch(r"(\d{1,2})sem", normalized)
    if not match:
        return str(value or "")

    number = match.group(1)
    return f"{SEMESTER_ROMAN.get(number, number)} Sem"

class PaperIndex:
    def __init__(self, index_path):
        self.path = Path(index_path)
        self.data = {}
        self.papers = []
        self.meta = {}
        self.index_exists = False
        self.load_error = None
        self.load()

    def load(self):
        self.index_exists = self.path.exists()
        self.load_error = None

        if not self.path.exists():
            # missing index — leave empty but usable
            self.data = {}
            self.papers = []
            self.meta = {}
            return

        with open(self.path, 'r', encoding='utf-8') as f:
            try:
                self.data = json.load(f)
            except Exception as exc:
                self.data = {}
                self.papers = []
                self.meta = {}
                self.load_error = str(exc)
                return
        
        self.meta = self.data.get("meta", {})
        # Use the flat papers list we added to the schema
        papers = self.data.get("papers", [])
        self.papers = papers if isinstance(papers, list) else []
        
    def search(self, query, filters=None):
        query = query.lower().strip()
        results = self.papers
        
        if filters:
            # Year range filtering
            yr_start = filters.get("year_start")
            yr_end = filters.get("year_end")

            # Category filters
            f_semester = filters.get("semester")
            f_program = filters.get("program")

            if f_program:
                wanted = normalize_program(f_program)
                results = [
                    p for p in results
                    if not p.get("attributes", {}).get("program")
                    or wanted == normalize_program(p.get("attributes", {}).get("program"))
                ]

            if f_semester:
                wanted = normalize_semester(f_semester)
                results = [
                    p for p in results
                    if wanted == normalize_semester(p.get("attributes", {}).get("semester"))
                ]
            
            if yr_start or yr_end:
                new_results = []
                for p in results:
                    y_str = p.get("attributes", {}).get("year")
                    if not y_str: continue
                    try:
                        y = int(y_str)
                        if yr_start and y < yr_start: continue
                        if yr_end and y > yr_end: continue
                        new_results.append(p)
                    except (TypeError, ValueError):
                        continue
                results = new_results
        
        if not query:
            return results
        
        # Actual fuzzy search: split query and check if all words exist in search_text
        # and rank by how many words match at the start of stems
        search_words = query.split()
        matches = []
        for p in results:
            text = p.get("search_text", "").lower()
            if all(word in text for word in search_words):
                # Basic ranking: exact match in filename gets priority
                score = 0
                if query in p.get("filename", "").lower():
                    score += 100
                matches.append((score, p))
        
        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches]

    def get_filter_options(self):
        """Extract unique values for filters."""
        options = {
            "year": set(),
            "program": set(),
            "semester": {},
            "session": set()
        }
        for p in self.papers:
            attrs = p.get("attributes", {})
            for key in options:
                val = attrs.get(key)
                if val:
                    if key == "semester":
                        normalized = normalize_semester(val)
                        if normalized:
                            options["semester"][normalized] = semester_label(val)
                    else:
                        options[key].add(val)

        semesters = sorted(
            options["semester"].items(),
            key=lambda item: int(re.match(r"(\d+)", item[0]).group(1)) if re.match(r"(\d+)", item[0]) else 999,
        )

        return {
            "year": sorted(list(options["year"]), reverse=True),
            "program": sorted(list(options["program"])),
            "semester": [label for _, label in semesters],
            "session": sorted(list(options["session"])),
        }

    def get_year_bounds(self):
        years = set()
        for paper in self.papers:
            year = (paper.get("attributes") or {}).get("year")
            if not year:
                continue
            try:
                years.add(int(year))
            except (TypeError, ValueError):
                continue

        if not years:
            return None, None

        return min(years), max(years)
