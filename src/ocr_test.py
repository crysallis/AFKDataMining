from rapidocr_onnxruntime import RapidOCR
import cv2

engine = RapidOCR()

img = cv2.imread("test_capture.png")
results, _ = engine(img)

if results:
    for box, text, score in results:
        x = int(box[0][0])
        y = int(box[0][1])
        print(f"  [{x:4d}, {y:4d}]  {score}  {text}")
else:
    print("No text detected")
