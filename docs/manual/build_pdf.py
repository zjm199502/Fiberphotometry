"""Render the manuals to PDF (needs `pip install markdown` and a Chromium/Chrome binary).

    python docs/manual/build_pdf.py [path/to/chromium]

PDFs are written next to the Markdown and copied into the package so the app's Help menu can open them.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
PKG = HERE.parents[1] / "src" / "fiberphoto" / "gui" / "manual"
CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
body { font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", sans-serif;
       font-size: 10.5pt; line-height: 1.6; color: #1f2328; }
h1 { font-size: 22pt; color: #1d5fb0; border-bottom: 3px solid #2a78d6; padding-bottom: 6px; }
h2 { font-size: 15pt; color: #1d5fb0; margin-top: 26px; border-bottom: 1px solid #d0d7de; padding-bottom: 3px;
     break-after: avoid; }
h2:not(:first-of-type) { break-before: page; }
h3 { font-size: 12pt; margin-top: 18px; break-after: avoid; }
img { width: 100%; border: 1px solid #d0d7de; border-radius: 4px; margin: 6px 0 2px; }
p:has(> img) { break-inside: avoid; text-align: center; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 4px 7px; vertical-align: top; }
th { background: #eef4fc; }
code { background: #f3f4f6; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
blockquote { margin: 10px 0; padding: 6px 12px; background: #fff8e6; border-left: 4px solid #e8a33d; }
hr { display: none; }
"""


def build(md_name, pdf_name, chromium):
    src = HERE / md_name
    body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables"])
    html = HERE / (src.stem + ".html")
    html.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
                    f"<body>{body}</body></html>", encoding="utf-8")
    pdf = HERE / pdf_name
    subprocess.run([chromium, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", html.as_uri()], check=True, capture_output=True)
    html.unlink()
    PKG.mkdir(parents=True, exist_ok=True)
    shutil.copy(pdf, PKG / pdf_name)
    print(pdf)


if __name__ == "__main__":
    chromium = sys.argv[1] if len(sys.argv) > 1 else "chromium"
    build("用户手册.md", "manual_zh.pdf", chromium)
    build("User_Manual.md", "manual_en.pdf", chromium)
