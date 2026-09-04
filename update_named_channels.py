#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_named_channels.py

Amaç:
- data/kanal_listesi.json içindeki sabit kanal isimlerini korur.
- sources.txt içindeki M3U kaynaklarını indirir.
- Kaynaklardaki kanal adlarını sabit isimlerle eşleştirir.
- Eşleşen URL'yi HTTP olarak kontrol eder.
- Çalışan adayları data/kanal_kaynaklari.m3u dosyasına yazar.
- Böylece ana playlist üreticisine eklenebilecek ayrı bir "kanal katmanı" oluşur.

Bu script internette rastgele yayın URL'si keşfetmez ve URL uydurmaz.
Yalnızca sources.txt içinde zaten tanımlı kaynakları kullanır.
"""

import re
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:
    requests = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CHANNELS_FILE = DATA / "kanal_listesi.json"
SOURCES_FILE = ROOT / "sources.txt"
OUT_FILE = DATA / "kanal_kaynaklari.m3u"
REPORT_FILE = DATA / "kanal_raporu.json"

TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; CAN-TV-Channel-Matcher/1.0)"

def norm(text):
    s = (text or "").upper()
    s = s.replace("İ", "I").replace("Ş", "S").replace("Ğ", "G")
    s = s.replace("Ü", "U").replace("Ö", "O").replace("Ç", "C")
    s = re.sub(r'[\[\]\(\)\{\}]', ' ', s)
    s = re.sub(r'\b(FHD|UHD|HD|SD|4K|HEVC|H265|H264)\b', ' ', s)
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

ALIASES = {
    norm("TV 8"): {norm("TV8"), norm("TV 8")},
    norm("TV 8.5"): {norm("TV8.5"), norm("TV 8.5"), norm("TV8 5")},
    norm("HABER TÜRK"): {norm("HABERTURK"), norm("HABER TURK"), norm("HABER TÜRK")},
    norm("NATIONAL GEOGRAPHIC"): {norm("NAT GEO"), norm("NATIONAL GEOGRAPHIC"), norm("NAT GEO HD")},
    norm("NAT WILD"): {norm("NAT WILD"), norm("NATIONAL WILD")},
    norm("DISCOVERY ID"): {norm("DISCOVERY ID"), norm("ID DISCOVERY")},
    norm("NR1"): {norm("NR1"), norm("NR1 TV")},
    norm("NR1 TÜRK"): {norm("NR1 TURK"), norm("NR1 TÜRK")},
    norm("DREAM TURK"): {norm("DREAM TURK"), norm("DREAM TÜRK")},
    norm("TRT MUZIK"): {norm("TRT MUZIK"), norm("TRT MÜZIK"), norm("TRT MÜZİK")},
}

def read_sources():
    if not SOURCES_FILE.exists():
        return []
    return [
        x.strip() for x in SOURCES_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        if x.strip() and not x.lstrip().startswith("#")
    ]

def fetch(url):
    if requests:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        return r.text
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")

def parse_m3u(text, source):
    lines = text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            ext = lines[i]
            url = ""
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and not lines[j].startswith("#"):
                url = lines[j].strip()
            if url:
                m = ext.split(",", 1)
                display = m[1].strip() if len(m) == 2 else "KANAL"
                result.append((display, url, ext, source))
            i = j + 1
        else:
            i += 1
    return result

def channel_match(target, candidate):
    a, b = norm(target), norm(candidate)
    if not a or not b:
        return False
    if b == a:
        return True
    if b in ALIASES.get(a, set()) or a in ALIASES.get(b, set()):
        return True
    # Exact token sequence after removing quality suffixes.
    if re.sub(r'\s+', '', a) == re.sub(r'\s+', '', b):
        return True
    return False

def check_stream(url):
    try:
        if not urlparse(url).scheme in ("http", "https"):
            return False, "unsupported-scheme"
        if requests:
            r = requests.get(
                url, timeout=TIMEOUT, headers={"User-Agent": UA},
                stream=True, allow_redirects=True
            )
            ok = 200 <= r.status_code < 400
            r.close()
            return ok, f"http-{r.status_code}"
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= getattr(r, "status", 200) < 400, f"http-{getattr(r, 'status', 200)}"
    except Exception as e:
        return False, type(e).__name__

def quality_score(text):
    u = (text or "").upper()
    if "4K" in u: return 4
    if "UHD" in u: return 4
    if "FHD" in u or "FULL HD" in u: return 3
    if "HD" in u: return 2
    if "SD" in u: return 1
    return 0

def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    sources = read_sources()

    candidates = []
    source_errors = []
    for src in sources:
        try:
            text = fetch(src)
            candidates.extend(parse_m3u(text, src))
            print(f"[OK] kaynak: {src}")
        except Exception as e:
            source_errors.append({"source": src, "error": str(e)})
            print(f"[HATA] kaynak: {src} -> {e}")

    out = ["#EXTM3U"]
    report = {
        "total_channels": len(channels),
        "matched": 0,
        "not_found": 0,
        "source_errors": source_errors,
        "channels": []
    }

    for ch in channels:
        name = ch["name"]
        category = ch["category"]
        matches = [x for x in candidates if channel_match(name, x[0])]
        # Önce daha yüksek kaliteyi dene, sonra diğer adayları.
        matches.sort(key=lambda x: quality_score(x[0]), reverse=True)

        chosen = None
        attempts = []
        for display, url, ext, source in matches:
            ok, reason = check_stream(url)
            attempts.append({"url": url, "source": source, "ok": ok, "reason": reason})
            if ok:
                chosen = (display, url, ext, source)
                break

        if chosen:
            display, url, ext, source = chosen
            # Kaynaktaki metadata yerine bizim sabit kanal adımızı ve kategorimizi kullan.
            out.append(f'#EXTINF:-1 tvg-name="{name}" group-title="{category}",{name}')
            out.append(url)
            report["matched"] += 1
            report["channels"].append({
                "name": name, "category": category, "status": "working",
                "url": url, "source": source, "attempts": attempts
            })
            print(f"[ÇALIŞIYOR] {name} <- {url}")
        else:
            report["not_found"] += 1
            report["channels"].append({
                "name": name, "category": category, "status": "not_found",
                "attempts": attempts
            })
            print(f"[BULUNAMADI] {name}")

    OUT_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"Toplam : {report['total_channels']}")
    print(f"Eşleşen/çalışan : {report['matched']}")
    print(f"Bulunamayan : {report['not_found']}")
    print(f"Çıktı : {OUT_FILE}")

if __name__ == "__main__":
    main()
