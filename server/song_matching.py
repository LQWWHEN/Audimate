"""Compare player and catalog credits without changing their display text."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


_FEATURE = re.compile(r"\((?:feat(?:uring)?|ft)\.?\s+([^()]+)\)|\[(?:feat(?:uring)?|ft)\.?\s+([^\[\]]+)\]")
_SEPARATOR = re.compile(r"\s+(?:&|x|feat(?:uring)?\.?|ft\.?)\s+|[;、]|\s+/\s+")
_QUALIFIER = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]|\s+[-–—]\s+([^()\[\]]+?)(?=\s+[-–—]\s+|[\[(]|$)")
_BARE_VERSION = re.compile(r"\s+(live|remix|acoustic|instrumental|demo|karaoke|radio edit|unplugged|mono|stereo)$")
_VERSION = re.compile(r"\b(?:live|remix|mix|acoustic|instrumental|demo|karaoke|edit|unplugged|mono|stereo|remaster(?:ed)?|part|pt)\b|现场|現場|不插电|不插電|混音|伴奏|ライブ|リミックス")
_MASTER = re.compile(r"(?:[0-9]{4} )?remaster(?:ed)?(?: [0-9]{4})?")
_LOCAL_VERSIONS = {"ライブ": "live", "现场": "live", "现场版": "live", "現場版": "live",
                   "リミックス": "remix", "混音版": "remix", "不插电": "unplugged", "不插電": "unplugged",
                   "伴奏": "instrumental"}


def split_credits(track):
    title = normalize(track["title"])
    fields = [normalize(track["artist"])]
    for match in _FEATURE.finditer(title):
        fields.append(match[1] or match[2])
    names = {name.strip() for field in fields for name in _SEPARATOR.split(field) if name.strip()}
    return normalize(_FEATURE.sub(" ", title)), names


def expand_roster(names, other):
    """Resolve ambiguous compact lists only with corroborating credits."""
    result = set()
    for name in names:
        if name in other or not re.search(r"[/,]", name):
            result.add(name)
            continue
        # Preserve compound names such as AC/DC inside a longer list.
        fields = [part.strip() for part in re.split(r"[/,]", name)]
        if not all(fields) or (len(fields) == 2 and all(len(part) <= 2 for part in fields)):
            result.add(name)
            continue
        if any(sorted(fields) == sorted(p.strip() for p in re.split(r"[/,]", n)) for n in other):
            result.update(fields)
            continue
        rest = name
        found = []
        while rest:
            part = max((n for n in other if rest == n or rest.startswith((n + "/", n + ","))), key=len, default="")
            if not part:
                break
            found.append(part)
            rest = rest[len(part):].lstrip("/, ")
        result.update(found if found and not rest else [name])
    return result


def split_suffix(title, suffixes):
    suffix = suffixes.search(title)
    if not suffix:
        return title, ""
    year = re.search(r"[0-9]{4}", suffix[0])
    return title[:suffix.start()].rstrip(), year[0] if year else ""


def versions(title):
    marks = set()
    for group in _QUALIFIER.finditer(title):
        label = next(value for value in group.groups() if value is not None)
        if _VERSION.search(label):
            mark = label.strip()
            marks.add(_LOCAL_VERSIONS.get(mark, mark))
    bare = _BARE_VERSION.search(title)
    if bare and not any(bare[1] in mark for mark in marks):
        marks.add(bare[1])
    return marks


def folded(text):
    return "".join(c for c in text if c.isalnum())


def scripts(text):
    return {unicodedata.name(c, "").split(" ", 1)[0] for c in text if c.isalpha()}


def similarity(left, right):
    if not left or not right or scripts(left) != scripts(right):
        return 0.0
    if left == right:
        return 1.0
    # Use both directions because SequenceMatcher may break ties differently.
    return min(SequenceMatcher(None, a, b, autojunk=False).ratio() for a, b in ((left, right), (right, left)))


def artist_name_score(left, right):
    a, b = folded(left), folded(right)
    if a == b:
        return float(bool(a))
    score = similarity(a, b)
    # The shorter credit may be embedded in a series name, alias or transliteration.
    short, long = sorted((a, b), key=len)
    span = SequenceMatcher(None, short, long, autojunk=False).find_longest_match().size
    if span >= 4 and span == len(short):
        return max(score, 0.95)
    if span >= 6 and span >= 0.55 * len(long) and span >= 0.70 * len(short):
        return max(score, 0.90)
    return score


def artist_similarity(left, right):
    exact = left & right
    pairs = sorted(((artist_name_score(a, b), a, b) for a in left-exact for b in right-exact), reverse=True)
    used_left, used_right = set(), set()
    total = len(exact)
    for score, a, b in pairs:
        if a not in used_left and b not in used_right:
            total += score
            used_left.add(a)
            used_right.add(b)
    return total / max(len(left), len(right), 1)


@dataclass
class Credits:
    left_title: str
    right_title: str
    left_artists: set
    right_artists: set
    conflict: bool

    @classmethod
    def compare(cls, track, player, suffixes):
        left, artists = split_credits(track)
        right, others = split_credits(player)
        a, year_a = split_suffix(left, suffixes)
        b, year_b = split_suffix(right, suffixes)
        # A configured mastering suffix may disappear on one storefront. Keep all other versions.
        def protected(raw, stripped):
            return versions(stripped) | {m for m in versions(raw[len(stripped):]) if not _MASTER.fullmatch(m)}
        conflict = bool(year_a and year_b and year_a != year_b) or protected(left, a) != protected(right, b)
        return cls(a, b, expand_roster(artists, others), expand_roster(others, artists), conflict)

    def strong(self):
        return bool(self.left_title and self.left_title == self.right_title and self.left_artists
                    and self.left_artists == self.right_artists and not self.conflict)

    def fallback(self, policy):
        if self.conflict:
            return "", "录音版本冲突"
        if self.left_title == self.right_title and self.left_title and self.left_artists & self.right_artists:
            if len(self.left_artists ^ self.right_artists) == 1:
                return "partial", "合作署名相差一人"
        if not policy["recognition_fuzzy"]:
            return "", ""
        a, b = folded(self.left_title), folded(self.right_title)
        title = similarity(a, b)
        main_a, main_b = (folded(_QUALIFIER.sub("", name)) for name in (self.left_title, self.right_title))
        if main_a and main_b:
            title = min(title, similarity(main_a, main_b))
            if main_a != main_b and min(len(main_a), len(main_b)) <= 3:
                title = 0.0
        artists = artist_similarity(self.left_artists, self.right_artists)
        note = f"模糊核对 · 歌名 {title:.3f} · 艺人 {artists:.3f}"
        if title >= policy["recognition_title_min"] and artists >= policy["recognition_artist_min"]:
            return "fuzzy", note
        return "", note + " · 未达快速确认门槛"
