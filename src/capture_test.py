from device import screenshot
import cv2

img = screenshot()
cv2.imwrite("test_capture.png", img)
print(f"Captured: {img.shape[1]}x{img.shape[0]}")
