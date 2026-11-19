"""Download the Eventus Security logo + favicon into app/static/brand/.

Run once on the server (it has internet access):  python scripts/fetch_brand.py
The Docker build runs it automatically. To use your own file instead, drop it at
app/static/brand/logo.webp (white logo on transparent background works best).
"""
import pathlib
import urllib.request

ASSETS = {
    "logo.webp": "https://eventussecurity.com/wp-content/uploads/2023/09/Eventus-TM-white-copy-2.webp",
    "favicon.ico": "https://eventussecurity.com/wp-content/uploads/2025/09/favicon-2.ico",
}
dest = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "brand"
dest.mkdir(parents=True, exist_ok=True)
ok = True
for name, url in ASSETS.items():
    target = dest / name
    if target.exists():
        print(f"{name}: already present, skipping")
        continue
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (brand-fetch)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            target.write_bytes(r.read())
        print(f"{name}: downloaded")
    except Exception as exc:
        ok = False
        print(f"{name}: could not download ({exc}); the dashboard will show a text wordmark instead")
if not ok:
    print("Tip: copy the logo manually to app/static/brand/logo.webp")
