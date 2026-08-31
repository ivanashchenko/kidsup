# -*- coding: utf-8 -*-
"""HTML -> PDF через Chromium (Playwright)."""
import sys, pathlib
from playwright.sync_api import sync_playwright
src, dst = sys.argv[1], sys.argv[2]
html = pathlib.Path(src).read_text(encoding="utf-8")
full = ("<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "</head><body>" + html + "</body></html>")
tmp = "/tmp/_print.html"; pathlib.Path(tmp).write_text(full, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                          args=["--no-sandbox"])
    pg = b.new_page()
    pg.goto("file://" + tmp, wait_until="networkidle")
    pg.pdf(path=dst, format="A4", print_background=True,
           margin={"top":"10mm","bottom":"10mm","left":"8mm","right":"8mm"})
    b.close()
print("PDF:", dst, pathlib.Path(dst).stat().st_size, "байт")
