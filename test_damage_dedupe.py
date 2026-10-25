"""Value tiebreaker for duplicate damage reads: folding, similarity, merge rules.
Run: venv/Scripts/python.exe test_damage_dedupe.py
Plain-script convention (no pytest in this repo). Fake resolver and names only.
"""
import sys
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from modes.damage_dedupe import (fold_name, name_similarity, find_duplicate_reads,
                                 drop_duplicate_reads)

failures = 0
total = 0


def check(desc, actual, expected):
    global failures, total
    total += 1
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {desc}: got {actual!r}, expected {expected!r}")
    if not ok:
        failures += 1


B = 1_000_000_000

check("fold reads IvI as m", fold_name("IvICShake"), "mcshake")
check("fold keeps l and 1 apart from i", fold_name("Lilith") != fold_name("XIII'th"), True)
check("fold maps Cyrillic look-alikes and strips the breve", fold_name("Найхэ"), "hanxe")
check("fold drops punctuation and case", fold_name("Tare_Ameli"), "tareameli")

check("a read with two leading tag characters matches the name", name_similarity("UTNaizab", "NaizaB") >= 0.99, True)
check("an IvI read of McShake clears the floor with the IvI rule", name_similarity("IvICSnaKe", "McShake") >= 0.8, True)
check("an unrelated name scores low", name_similarity("Kit", "Tare_Ameli") < 0.5, True)

resolved = {"NaizaB": 1, "Tare_Ameli": 2, "БоссМразей": 3, "McShake": 4,
            "Araleth": 5, "XIII'th": 6, "Ramz78k": 7, "Kit": 8}
canonical = {1: "NaizaB", 2: "Tare_Ameli", 3: "БоссМразей", 4: "McShake",
             5: "Araleth", 6: "XIII'th", 7: "Ramz78k", 8: "Kit"}


def known(name, dmg):
    return {"name": name, "damage": dmg}


base = [known("NaizaB", 311 * B), known("Tare_Ameli", 202 * B), known("БоссМразей", 244 * B),
        known("McShake", 19_000_000_000), known("Araleth", 166 * B), known("XIII'th", 166 * B),
        known("Ramz78k", 166 * B), known("Kit", 171 * B)]


def merges(*extra):
    found = find_duplicate_reads(base + list(extra), resolved, canonical)
    return [(e["name"], target) for e, target, _ in found]


check("UTNaizab merges into NaizaB", merges(known("UTNaizab", 311 * B)), [("UTNaizab", "NaizaB")])
check("INaizaD merges into NaizaB", merges(known("INaizaD", 311 * B)), [("INaizaD", "NaizaB")])
check("TareAimei merges into Tare_Ameli", merges(known("TareAimei", 202 * B)), [("TareAimei", "Tare_Ameli")])
check("EoccMpa3eñ merges into БоссМразей", merges(known("EoccMpa3eñ", 244 * B)), [("EoccMpa3eñ", "БоссМразей")])
check("IvICSnaKe merges into McShake", merges(known("IvICSnaKe", 19_000_000_000)), [("IvICSnaKe", "McShake")])
check("rarehiiici stays unmatched (similarity too low)", merges(known("rarehiiici", 202 * B)), [])
check("psi Kamz/ok stays unmatched against three 166B members", merges(known("ψ Kamz/ok", 166 * B)), [])
check("phiJ Kailiz/ ok is never merged into the wrong member", merges(known("φJ Kailiz/ ok", 166 * B)), [])
check("a similar name with a different value does not merge", merges(known("UTNaizab", 300 * B)), [])

amb_resolved = {"Katia": 1, "Katio": 2}
amb_canon = {1: "Katia", 2: "Katio"}
amb = [known("Katia", 50 * B), known("Katio", 50 * B), known("Katie", 50 * B)]
check("two same-value members both similar: no merge",
      find_duplicate_reads(amb, amb_resolved, amb_canon), [])

entries_in = [dict(e) for e in base] + [known("UTNaizab", 311 * B), known("Junk", 7 * B)]
snapshot = [dict(e) for e in entries_in]
kept, lines = drop_duplicate_reads(
    entries_in,
    resolve_fn=lambda names: ({n: resolved[n] for n in names if n in resolved},
                              [n for n in names if n not in resolved]),
    names_fn=lambda ids: {i: canonical[i] for i in ids},
    fmt_damage=lambda d: f"{d // B}B")
check("drop_duplicate_reads removes only the merged duplicate", [e["name"] for e in kept if e["name"] in ("UTNaizab", "Junk")], ["Junk"])
check("drop_duplicate_reads prints one visible line per merge",
      lines, ["GUILD_DUEL_DAMAGE: merged 'UTNaizab' into 'NaizaB' (same 311B)"])
check("drop_duplicate_reads leaves its input untouched", entries_in, snapshot)

if failures:
    print(f"\n{failures}/{total} cases failed.")
    sys.exit(1)
print(f"\nAll {total} cases passed.")
