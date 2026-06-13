import sys
import cv2
from rapidocr_onnxruntime import RapidOCR
from ocr import preprocess
from parser import parse_members

engine = RapidOCR()

img_path = sys.argv[1] if len(sys.argv) > 1 else None
if not img_path:
    print("Usage: python test_ocr.py <path_to_image>")
    sys.exit(1)

img = cv2.imread(img_path)
results, _ = engine(preprocess(img))
results = results or []

print("=== Raw OCR blocks ===")
for box, text, conf in results:
    x = int(box[0][0])
    y = int(box[0][1])
    print(f"  ({x:4d}, {y:4d})  {float(conf):.2f}  {text!r}")

print("\n=== Parsed members ===")
members = parse_members(results)
for m in members:
    print(f"  name={m.name!r}  last_active={m.last_active!r}  power={m.combat_power!r}  activeness={m.activeness}  warband={m.warband!r}")
