import urllib.request
from pathlib import Path

URL = "PASTE_SUPABASE_DOWNLOAD_URL_HERE"
DEST = Path("/var/data/warehouse.duckdb")

tmp = DEST.with_suffix(".tmp")
urllib.request.urlretrieve(URL, tmp)

if tmp.stat().st_size < 200_000_000:
    raise RuntimeError("Download too small, aborting")

tmp.replace(DEST)
print("Restored", DEST, DEST.stat().st_size)