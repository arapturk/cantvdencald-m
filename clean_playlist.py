import re
from pathlib import Path

SRC = "playlist.m3u"
OUT = "playlist_clean.m3u"

def norm(name: str) -> str:
    name = name.upper()
    name = re.sub(r"\b(4K|UHD|FHD|FULL ?HD|HD|SD)\b", "", name)
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

priority = {"4K":4, "UHD":4, "FHD":3, "FULL HD":3, "HD":2, "SD":1}

def score(name):
    u = name.upper()
    s = 0
    for k,v in priority.items():
        if k in u: s = max(s,v)
    return s

lines = Path(SRC).read_text(encoding="utf-8", errors="ignore").splitlines()

seen_url = set()
best = {}

i=0
while i < len(lines)-1:
    if lines[i].startswith("#EXTINF"):
        extinf = lines[i]
        url = lines[i+1].strip()
        if not url.startswith(("http://","https://","rtmp://","rtsp://")):
            i += 2; continue
        name = extinf.split(",",1)[1].strip() if "," in extinf else "KANAL"
        if url in seen_url:
            i += 2; continue
        seen_url.add(url)

        key = norm(name)
        item = (score(name), extinf, url)

        if key not in best or item[0] > best[key][0]:
            best[key] = item
        i += 2
    else:
        i += 1

with open(OUT, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for _, extinf, url in best.values():
        f.write(extinf + "\n" + url + "\n")

print(f"Yazılan kanal: {len(best)}")
