"""Shared OCR engines + preprocessing · used by the guild scraper and all
game-mode ranking scanners.

PP-OCRv5 (rapidocr) is the primary engine: `ocr_image()` tries v5 first and
falls back to PP-OCRv3 (rapidocr_onnxruntime) only if v5 is unavailable.
`_scan_rank_column` in modes/common.py uses v5 independently on the color rank
strip (no preprocessing) to read calligraphic rank badge fonts.
"""
import logging
import cv2
from rapidocr_onnxruntime import RapidOCR

engine = RapidOCR()

_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

_engine_v5 = None


def get_engine_v5():
    """Lazy-init PP-OCRv5. Returns the engine or None if unavailable."""
    global _engine_v5
    if _engine_v5 is None:
        try:
            logging.disable(logging.INFO)
            try:
                from rapidocr import LangDet, LangRec, OCRVersion, RapidOCR as _RapidOCR
                _engine_v5 = _RapidOCR(params={
                    "Det.ocr_version": OCRVersion.PPOCRV4,
                    "Det.lang_type": LangDet.CH,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": LangRec.CH,
                })
            finally:
                logging.disable(logging.NOTSET)
        except Exception:
            _engine_v5 = False
    return _engine_v5 if _engine_v5 else None


def preprocess(img):
    """CLAHE on luminance + unsharp mask · improves OCR on dark card backgrounds."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    blur = cv2.GaussianBlur(img, (0, 0), 1.5)
    return cv2.addWeighted(img, 1.4, blur, -0.4, 0)


def ocr_image(img):
    preprocessed = preprocess(img)
    v5 = get_engine_v5()
    if v5:
        result = v5(preprocessed)
        if result is not None:
            boxes = result.boxes if result.boxes is not None else []
            txts = result.txts if result.txts is not None else []
            scores_raw = getattr(result, 'scores', None)
            scores = scores_raw if scores_raw is not None else [1.0] * len(txts)
            return [
                (boxes[i], txts[i], scores[i] if i < len(scores) else 1.0)
                for i in range(min(len(boxes), len(txts)))
                if boxes[i] is not None and txts[i]
            ]
    results, _ = engine(preprocessed)
    return results or []


def block_center(box) -> tuple[int, int]:
    """Center of an OCR bounding box (4 corner points) · tappable coordinates."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return int(sum(xs) / 4), int(sum(ys) / 4)
