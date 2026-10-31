import sys
import subprocess
import os
from PIL import Image, ImageFilter

def preprocess_and_solve(img_path):
    try:
        img = Image.open(img_path).convert('RGB')
        w, h = img.size

        # Create clean binary image
        clean = Image.new('L', (w, h), 255)
        for x in range(w):
            for y in range(h):
                r, g, b = img.getpixel((x, y))
                # Red text filter:
                if r > 90 and (r - g) > 15 and (r - b) > 15:
                    clean.putpixel((x, y), 0)

        # Remove isolated single pixel noise
        clean = clean.filter(ImageFilter.MedianFilter(size=3))

        # Scale up 3x for OCR clarity
        scaled = clean.resize((w * 3, h * 3), Image.Resampling.NEAREST)
        temp_scaled = f"/tmp/clean_{os.getpid()}_{os.path.basename(img_path)}"
        scaled.save(temp_scaled)

        # Try PSM 8 (single word)
        cmd = [
            'tesseract', temp_scaled, 'stdout',
            '--psm', '8',
            '-c', 'tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        text = res.stdout.strip().replace(' ', '')

        if not text or len(text) < 4:
            # Try PSM 7 (single line)
            cmd[4] = '7'
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            text2 = res.stdout.strip().replace(' ', '')
            if len(text2) > len(text):
                text = text2

        try:
            if os.path.exists(temp_scaled):
                os.remove(temp_scaled)
        except Exception:
            pass

        return text
    except Exception as e:
        return ""

if __name__ == '__main__':
    if len(sys.argv) > 1:
        print(preprocess_and_solve(sys.argv[1]))
    else:
        print("")
