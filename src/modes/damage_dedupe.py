"""Value tiebreaker for the Guild Duel Damage scan · a read that did not resolve to a
member but carries exactly the damage of one that did, and has a similar name, is a
duplicate misread of that member (a name cut by the pinned bar, an M drawn as IvI).
The damage value alone is never enough: values are rounded to billions and collide,
so a merge also needs a clearly similar name and a clear margin over the runner-up."""
import re
import unicodedata
from difflib import SequenceMatcher

from db import resolve_names, names_for_ids

FLOOR = 0.7
MARGIN = 0.15

_LOOKALIKE = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "и": "n", "з": "e", "э": "e",
})
_CONFUSABLE = str.maketrans({"d": "b", "0": "o"})
_M_GLYPHS = re.compile(r"[il|1]v[il|1]")
_TOKEN_SPLIT = re.compile(r"[\s「」\[\]()<>]+")
_KEEP = re.compile(r"[^a-z0-9一-鿿]")


def fold_name(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = _M_GLYPHS.sub("m", s)
    return _KEEP.sub("", s.translate(_LOOKALIKE).translate(_CONFUSABLE))


def _variants(read: str) -> set[str]:
    pieces = {read} | {t for t in _TOKEN_SPLIT.split(read) if t}
    for p in list(pieces):
        for k in (1, 2):
            if len(p) - k >= 3:
                pieces.add(p[k:])
    return {f for f in map(fold_name, pieces) if f}


def name_similarity(read: str, canonical: str) -> float:
    target = fold_name(canonical)
    return max((SequenceMatcher(None, v, target).ratio() for v in _variants(read)), default=0.0)


def find_duplicate_reads(entries: list[dict], resolved: dict[str, int], canonical: dict[int, str],
                         floor: float = FLOOR, margin: float = MARGIN) -> list[tuple[dict, str, float]]:
    by_damage: dict[int, set[str]] = {}
    for e in entries:
        if e["name"] in resolved:
            by_damage.setdefault(e["damage"], set()).add(canonical[resolved[e["name"]]])
    found = []
    for e in entries:
        if e["name"] in resolved:
            continue
        scored = sorted(((name_similarity(e["name"], c), c) for c in by_damage.get(e["damage"], ())),
                        reverse=True)
        if not scored:
            continue
        best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best[0] >= floor and best[0] - runner_up >= margin:
            found.append((e, best[1], best[0]))
    return found


def drop_duplicate_reads(entries: list[dict], resolve_fn=resolve_names, names_fn=names_for_ids,
                         fmt_damage=str) -> tuple[list[dict], list[str]]:
    """Entries with the merged duplicates removed, plus one printable line per merge.
    Runs before resolve_entries so a merged name never reaches its REVIEW_NAMES line."""
    resolved, _ = resolve_fn([e["name"] for e in entries])
    canonical = names_fn(sorted(set(resolved.values())))
    merges = find_duplicate_reads(entries, resolved, canonical)
    dropped = {id(e) for e, _, _ in merges}
    lines = [f"GUILD_DUEL_DAMAGE: merged '{e['name']}' into '{target}' (same {fmt_damage(e['damage'])})"
             for e, target, _ in merges]
    return [e for e in entries if id(e) not in dropped], lines
