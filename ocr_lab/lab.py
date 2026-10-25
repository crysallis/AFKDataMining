"""OCR variant lab · replays saved frames through several engine/preprocess variants and
compares what each reads, using the real Guild Duel Damage parser and RosterResolver
(learn=False, read-only). Nothing here touches production code or the database.

    venv/Scripts/python.exe ocr_lab/lab.py --data <dir> [--variants prod,raw,...] [--truth truth.json]

Reads <dir>/frames/*.png. Writes <dir>/results.json and <dir>/report.txt.
truth.json (optional) maps a damage string like "313B" to the true roster name; a read
is then scored correct / wrong-member / unmatched against it.
"""
import argparse
import json
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ocr  # noqa: E402
from db import RosterResolver, _get_active_roster  # noqa: E402
from modes.guild_duel_damage import rows_from_blocks, rows_to_entries  # noqa: E402

_LOOKALIKE = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "и": "n", "з": "3", "э": "e",
    "ǎ": "a", "ñ": "n",
})
_CONFUSABLE = str.maketrans({"d": "b", "l": "i", "1": "i", "|": "i", "0": "o"})


def fold(name: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFD", name.lower())
                       if unicodedata.category(c) != "Mn")
    return stripped.translate(_LOOKALIKE).translate(_CONFUSABLE)


def fold_resolve(name: str, roster: set[str], strip_lead: int = 2, thr: float = 0.85) -> str | None:
    """Lab-only resolver: look-alike fold + optional leading-tag strip, nearest roster name."""
    folded_roster = {fold(r): r for r in roster}
    for cand in [name] + [name[k:] for k in range(1, strip_lead + 1) if len(name) - k >= 3]:
        key = fold(cand)
        if key in folded_roster:
            return folded_roster[key]
    best, score = None, 0.0
    for cand in [name] + [name[k:] for k in range(1, strip_lead + 1) if len(name) - k >= 3]:
        key = fold(cand)
        for fr, orig in folded_roster.items():
            r = SequenceMatcher(None, key, fr).ratio()
            if r > score:
                best, score = orig, r
    return best if score >= thr else None


def _engine(det_ver: str, det_type: str, rec_ver: str, rec_lang: str, rec_type: str):
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR
    return RapidOCR(params={
        "Det.ocr_version": getattr(OCRVersion, det_ver),
        "Det.model_type": getattr(ModelType, det_type),
        "Det.lang_type": LangDet.CH,
        "Rec.ocr_version": getattr(OCRVersion, rec_ver),
        "Rec.model_type": getattr(ModelType, rec_type),
        "Rec.lang_type": getattr(LangRec, rec_lang),
    })


# name -> (engine spec or None for production, preprocess mode, upscale)
VARIANTS = {
    "prod":        (None, "prod", 1.0),
    "raw":         (("PPOCRV4", "MOBILE", "PPOCRV5", "CH", "MOBILE"), "none", 1.0),
    "clahe_only":  (("PPOCRV4", "MOBILE", "PPOCRV5", "CH", "MOBILE"), "prod", 1.0),
    "rec_server":  (("PPOCRV4", "MOBILE", "PPOCRV5", "CH", "SERVER"), "prod", 1.0),
    "det_server":  (("PPOCRV4", "SERVER", "PPOCRV5", "CH", "MOBILE"), "prod", 1.0),
    "both_server": (("PPOCRV4", "SERVER", "PPOCRV5", "CH", "SERVER"), "prod", 1.0),
    "v5det_server": (("PPOCRV5", "SERVER", "PPOCRV5", "CH", "SERVER"), "prod", 1.0),
    "eslav":       (("PPOCRV4", "MOBILE", "PPOCRV5", "ESLAV", "MOBILE"), "prod", 1.0),
    "cyrillic":    (("PPOCRV4", "MOBILE", "PPOCRV5", "CYRILLIC", "MOBILE"), "prod", 1.0),
    "latin":       (("PPOCRV4", "MOBILE", "PPOCRV5", "LATIN", "MOBILE"), "prod", 1.0),
    "up2":         (("PPOCRV4", "MOBILE", "PPOCRV5", "CH", "MOBILE"), "prod", 2.0),
    "up3":         (("PPOCRV4", "MOBILE", "PPOCRV5", "CH", "MOBILE"), "prod", 3.0),
    "up2_server":  (("PPOCRV4", "SERVER", "PPOCRV5", "CH", "SERVER"), "prod", 2.0),
}

_engines: dict = {}


def run_ocr(img: np.ndarray, spec, pre: str, scale: float):
    """Returns [(box, text, conf)] in original-frame coordinates."""
    work = img
    if scale != 1.0:
        work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if spec is None:
        res = ocr.ocr_image(work)
    else:
        if spec not in _engines:
            _engines[spec] = _engine(*spec)
        src = ocr.preprocess(work) if pre == "prod" else work
        r = _engines[spec](src)
        boxes = r.boxes if r.boxes is not None else []
        txts = r.txts if r.txts is not None else []
        scores = r.scores if getattr(r, "scores", None) is not None else [1.0] * len(txts)
        res = [(boxes[i], txts[i], float(scores[i])) for i in range(min(len(boxes), len(txts)))
               if boxes[i] is not None and txts[i]]
    if scale != 1.0:
        res = [(np.asarray(b, dtype=float) / scale, t, c) for b, t, c in res]
    return res


def name_conf(ocr_results, name: str) -> float | None:
    for _box, text, conf in ocr_results:
        if text.strip() == name:
            return float(conf)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--truth", default=None)
    args = ap.parse_args()

    data = Path(args.data)
    frames = sorted((data / "frames").glob("frame_*.png"))
    if not frames:
        sys.exit(f"no frames in {data / 'frames'}")
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8")) if args.truth else {}
    roster = _get_active_roster()
    resolver = RosterResolver(learn=False)

    results: dict = {}
    for vname in args.variants.split(","):
        spec, pre, scale = VARIANTS[vname]
        rows_out, total_ms = [], 0.0
        try:
            for fpath in frames:
                img = cv2.imread(str(fpath))
                t0 = time.perf_counter()
                res = run_ocr(img, spec, pre, scale)
                total_ms += (time.perf_counter() - t0) * 1000
                for e in rows_to_entries(rows_from_blocks(res)):
                    read = e["name"]
                    key = f"{e['damage'] // 10**9}B"
                    rows_out.append({
                        "frame": fpath.name, "damage": key, "read": read,
                        "conf": name_conf(res, read),
                        "prod_match": resolver.resolve(read),
                        "fold_match": fold_resolve(read, roster),
                    })
        except Exception as exc:
            print(f"[{vname}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            results[vname] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        results[vname] = {"ms_per_frame": total_ms / len(frames), "rows": rows_out}
        print(f"[{vname}] done · {total_ms / len(frames):.0f} ms/frame · {len(rows_out)} rows", flush=True)

    (data / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(results, truth, data / "report.txt")
    print(f"\nwrote {data / 'report.txt'}")


def score(row: dict, truth: dict, field: str) -> str:
    m = row[field]
    t = truth.get(row["damage"])
    if t is None:
        return "matched" if m else "unmatched"
    if m is None:
        return "unmatched"
    return "correct" if m == t else "WRONG"


def write_report(results: dict, truth: dict, path: Path) -> None:
    lines = ["VARIANT SUMMARY", "-" * 100]
    hdr = f"{'variant':<14}{'ms/frm':>8}{'rows':>6}{'prodMatch':>10}{'foldMatch':>10}{'wrong(p)':>9}{'wrong(f)':>9}{'avgConf':>9}{'confUnmatched':>14}"
    lines.append(hdr)
    for v, r in results.items():
        if "error" in r:
            lines.append(f"{v:<14}ERROR {r['error']}")
            continue
        rows = r["rows"]
        n = len(rows)
        pm = sum(1 for x in rows if x["prod_match"])
        fm = sum(1 for x in rows if x["fold_match"])
        wp = sum(1 for x in rows if score(x, truth, "prod_match") == "WRONG")
        wf = sum(1 for x in rows if score(x, truth, "fold_match") == "WRONG")
        confs = [x["conf"] for x in rows if x["conf"] is not None]
        unm = [x["conf"] for x in rows if x["conf"] is not None and not x["prod_match"]]
        avg = f"{sum(confs) / len(confs):.3f}" if confs else "-"
        avgu = f"{sum(unm) / len(unm):.3f}" if unm else "-"
        lines.append(f"{v:<14}{r['ms_per_frame']:>8.0f}{n:>6}{pm:>10}{fm:>10}{wp:>9}{wf:>9}{avg:>9}{avgu:>14}")

    lines += ["", "ROWS WHERE VARIANTS DISAGREE OR ANYTHING FAILED TO MATCH (keyed by frame + damage)", "-" * 100]
    keys = sorted({(x["frame"], x["damage"]) for r in results.values() if "rows" in r for x in r["rows"]})
    for frame, dmg in keys:
        cells, interesting = [], False
        reads = set()
        for v, r in results.items():
            if "rows" not in r:
                continue
            hit = next((x for x in r["rows"] if x["frame"] == frame and x["damage"] == dmg), None)
            if hit is None:
                cells.append(f"    {v:<14} (row not found)")
                interesting = True
                continue
            reads.add(hit["read"])
            conf = f"{hit['conf']:.2f}" if hit["conf"] is not None else "?"
            flag = "" if hit["prod_match"] else "  <-- prod unmatched"
            fm = f" fold->{hit['fold_match']}" if (hit["fold_match"] and not hit["prod_match"]) else ""
            cells.append(f"    {v:<14} {hit['read']!r:<22} conf {conf}  prod->{hit['prod_match']}{fm}{flag}")
            if not hit["prod_match"]:
                interesting = True
        if interesting or len(reads) > 1:
            t = truth.get(dmg)
            lines.append(f"{frame}  {dmg}" + (f"  (truth: {t})" if t else ""))
            lines += cells
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
