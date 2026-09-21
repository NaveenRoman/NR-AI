import os

try:
    import pyautogui
except ImportError:
    pyautogui = None


from rapidocr_onnxruntime import RapidOCR


print("========================================")
print("        NR AI POPUP DEBUG")
print("========================================")

print("\n📸 Capturing full screen...")

os.makedirs(
    "data/screenshots/debug",
    exist_ok=True
)

path = "data/screenshots/debug/full_screen.png"

image = pyautogui.screenshot()

image.save(path)

print(f"✅ Screenshot saved: {path}")

print("\n👁️ Running full-screen OCR...")

ocr = RapidOCR()

result, _ = ocr(
    path
)

if not result:

    print("❌ No OCR results.")

    raise SystemExit

print(
    f"\n🔎 Detected {len(result)} text elements."
)

print("\n----------------------------------------")

for item in result:

    box = item[0]
    text = item[1]
    confidence = item[2]

    x_values = [
        point[0]
        for point in box
    ]

    y_values = [
        point[1]
        for point in box
    ]

    center = (
        int(
            (
                min(x_values)
                + max(x_values)
            ) / 2
        ),
        int(
            (
                min(y_values)
                + max(y_values)
            ) / 2
        )
    )

    print(
        f'Text: "{text}"'
    )

    print(
        f"Confidence: {confidence:.2f}"
    )

    print(
        f"Center: {center}"
    )

    print(
        f"Box: {box}"
    )

    print("----------------------------------------")

print("\n🟢 Popup debug completed.")