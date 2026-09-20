#!/usr/bin/env python3
"""把 tools/poster.html 渲染成定长海报：PNG / JPG / PDF。

为什么不用 Chrome 直接 --print-to-pdf：无头 Chrome 会忽略 @page 的纸张尺寸，
改用 Letter 并把 1587px 宽的画面整体缩放，导致分页与比例都不受控。
所以改成「固定视口截图 → 按内容自动裁掉底部空白 → 由图片生成 PDF」，
输出尺寸完全由我们决定。

    python render_poster.py <poster.html> <out_dir>
"""
import os
import subprocess
import sys

from PIL import Image
import pymupdf

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

WIDTH = 1587          # A2 @96dpi
VIEW_H = 3400         # 视口高度（大于内容，最后裁掉）
BG_LUM = 100          # 高于此亮度视为"有内容"


def find_browser():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Chrome / Edge")


def crop_to_content(img):
    """从底部往上找第一条含高亮像素的行，作为内容下边界"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    step = 2
    last = 0
    for y in range(h - 1, 0, -step):
        hot = False
        for x in range(0, w, 6):
            if px[x, y] > BG_LUM:
                hot = True
                break
        if hot:
            last = y
            break
    bottom = min(h, last + 40)
    return img.crop((0, 0, w, bottom))


def main():
    html = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2])
    os.makedirs(out, exist_ok=True)
    browser = find_browser()

    shot = os.path.join(out, "_poster_raw.png")
    url = "file:///" + html.replace("\\", "/")
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={WIDTH},{VIEW_H}",
                    f"--screenshot={shot}", url],
                   check=True, capture_output=True)

    img = Image.open(shot).convert("RGB")
    img = crop_to_content(img)

    # 缩放到 A2 比例（420 x 594 mm）；内容实际比例可能略长，按实际比例出图
    a2_w_mm = 420.0
    h_mm = a2_w_mm * img.height / img.width

    png = os.path.join(out, "WristClaw-海报.png")
    jpg = os.path.join(out, "WristClaw-海报.jpg")
    pdf = os.path.join(out, "WristClaw-海报.pdf")

    img.save(png)
    img.save(jpg, quality=93, optimize=True, subsampling=0)

    doc = pymupdf.open()
    page = doc.new_page(width=a2_w_mm / 25.4 * 72, height=h_mm / 25.4 * 72)
    page.insert_image(page.rect, filename=jpg)
    doc.save(pdf, deflate=True)
    doc.close()

    print("内容尺寸 %dx%d px  ->  %.0f x %.0f mm" % (img.width, img.height,
                                                    a2_w_mm, h_mm))
    for f in (png, jpg, pdf):
        print("  %-28s %8.1f KB" % (os.path.basename(f),
                                    os.path.getsize(f) / 1024))


if __name__ == "__main__":
    sys.exit(main())
