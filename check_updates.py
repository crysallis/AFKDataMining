"""Weekly dependency check for the miner. READ-ONLY -- changes nothing.

Run:  .\\venv\\Scripts\\python.exe check_updates.py

Reports installed packages with newer versions available and any dependency
conflicts. Update deliberately afterward:
    .\\venv\\Scripts\\python.exe -m pip install -U <package>   (one at a time)

Caution: rapidocr-onnxruntime / opencv-python / numpy can subtly change OCR
output -- run a /scan and eyeball the results before trusting an upgrade.
"""
import subprocess
import sys


def run(label, args):
    print(f"\n=== {label} ===", flush=True)  # flush so the header precedes subprocess output
    subprocess.run([sys.executable, "-m", *args])


run("Outdated packages (Name / Version / Latest)", ["pip", "list", "--outdated"])
run("Dependency conflicts", ["pip", "check"])

print("\nDone (nothing was changed).")
print("- Upgrade one at a time, then run a scan to verify OCR still reads cleanly.")
