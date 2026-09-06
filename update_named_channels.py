#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_named_channels.py  (düzeltilmiş sürüm)

Amaç:
- data/kanal_listesi.json içindeki sabit kanal isimlerini korur.
- sources.txt içindeki M3U kaynaklarını indirir.
- Kaynaklardaki kanal adlarını sabit isimlerle eşleştirir.
- Eşleşen URL'yi HTTP olarak kontrol eder.
- Çalışan adayları data/kanal_kaynaklari.m3u dosyasına yazar.

Bu script internette rastgele yayın URL'si keşfetmez ve URL uydurmaz.
Yalnızca sources.txt içinde zaten tanımlı kaynakları kullanır.

Önceki sürüme göre düzeltmeler:
  1. #EXTVLCOPT / #KODIPROP satırı olan kanallar artık atlanmıyor.
  2. norm() parantez İÇİNİ ve 1080p/720p etiketlerini de temizliyor.
  3. Kalite eki silinince adı boşalan kanallar (ör. "TRT 4K HD") korunuyor.
  4. Token bazlı esnek eşleştirme eklendi.
  5. "eşleşme yok" ile "ölü link" ayrı raporlanıyor.
  6. Bu turda bulunamayan kanal için önceki çalışan link korunuyor.
  7. Stream kontrolleri paralel yapılıyor (çok daha hızlı).
"""

import re
import json
import concurrent.futures
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

# Bir kanal için en fazla kaç aday URL denensin
MAX_ATTEMPTS = 6
# Aynı anda kaç HTTP kontrolü yapılsın
MAX_WORKERS = 12

# Kalite etiketleri: isimden silinir ama sıralamada kullanılır
QUALITY_RE = re.compile(
    r'\b(FHD|UHD|HD|SD|4K|8K|HEVC|H265|H264|MULTI|VIP|BACKUP|YEDEK)\b'
)
RES_RE = re.compile(r'\b\d{3,4}[PI]\b')          # 1080p, 720P, 576i
BRACKET_RE = re.compile(r'[\[\(\{][^\]\)\}]*[\]\)\}]')  # (1080p), [Not 24/7]

TR_MAP = str.maketrans({
    "İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
    "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C",
    "Â": "A", "â": "A", "Î": "I", "î": "I", "Û": "U", "û": "U",
})


def _base(text):
    """Türkçe karakterleri sadeleştirip büyük harfe çevirir."""
    s = (text or "").translate(TR_MAP).upper()
    s = BRACKET_RE.sub(" ", s)
    s = RES_RE.sub(" ", s)
    return s


def norm(text):
    """Kalite etiketleri SİLİNMİŞ normalize ad. Eşleştirmede kullanılır."""
    s = QUALITY_RE.sub(" ", _base(text))
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def norm_keep(text):
    """Kalite etiketleri KORUNMUŞ normalize ad.

    'TRT 4K HD' gibi adlarda norm() sonucu 'TRT' kalır ve rastgele bir
    TRT kanalıyla eşleşir. Bu fonksiyon o durumda yedek olarak kullanılır.
    """
    s = re.sub(r'[^A-Z0-9]+', ' ', _base(text))
    return re.sub(r'\s+', ' ', s).strip()


ALIASES = {
    norm("TV 8"): {"TV8", "TV 8"},
    norm("TV 8.5"): {"TV8 5", "TV 8 5", "TV85"},
    norm("HABER TÜRK"): {"HABERTURK", "HABER TURK"},
    norm("NATIONAL GEOGRAPHIC"): {"NAT GEO", "NATGEO", "NATIONAL GEOGRAPHIC"},
    norm("NAT WILD"): {"NAT GEO WILD", "NATGEO WILD", "NATIONAL GEOGRAPHIC WILD"},
    norm("DISCOVERY ID"): {"ID DISCOVERY", "INVESTIGATION DISCOVERY"},
    norm("NR1"): {"NR1", "NR1 TV", "NUMBER ONE"},
    norm("NR1 TÜRK"): {"NR1 TURK", "NUMBER ONE TURK"},
    norm("DREAM TURK"): {"DREAM TURK", "DREAM TV TURK"},
    norm("TRT MUZIK"): {"TRT MUZIK", "TRT MUSIC"},
    norm("ÜLKE TV"): {"ULKE TV", "ULKETV"},
    norm("24"): {"24 TV", "TV 24", "KANAL 24"},
}
# Alias değerlerini de normalize et (elle yazılanlar tutarsız olabilir)
ALIASES = {k: {norm(v) for v in vals} for k, vals in ALIASES.items()}


def read_sources():
    if not SOURCES_FILE.exists():
        print(f"[UYARI] {SOURCES_FILE} bulunamadı.")
        return []
    seen, out = set(), []
    for line in SOURCES_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        u = line.strip()
        if not u or u.startswith("#") or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def fetch(url):
    if requests:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        return r.text
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_m3u(text, source):
    """M3U ayrıştırıcı.

    DÜZELTME: #EXTINF ile URL arasında #EXTVLCOPT, #KODIPROP, #EXTGRP gibi
    satırlar olabilir. Eski sürüm bu kanalları tamamen atlıyordu.
    """
    lines = text.splitlines()
    result = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("#EXTINF"):
            ext = line
            j = i + 1
            # boş satırları ve # ile başlayan yardımcı satırları geç
            while j < n and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
                # araya yeni bir #EXTINF girdiyse bu kaydın URL'si yok demektir
                if lines[j].startswith("#EXTINF"):
                    break
                j += 1
            if j < n and lines[j].strip() and not lines[j].lstrip().startswith("#"):
                url = lines[j].strip()
                display = ext.split(",", 1)[1].strip() if "," in ext else ""
                if not display:
                    m = re.search(r'tvg-name="([^"]*)"', ext)
                    display = m.group(1).strip() if m else "KANAL"
                result.append((display, url, ext, source))
                i = j + 1
            else:
                i = j
        else:
            i += 1
    return result


def channel_match(target, candidate):
    a, b = norm(target), norm(candidate)
    if not a or not b:
        return False

    # 'TRT 4K HD' gibi adlarda norm() geriye çok az şey bırakır.
    # Bu durumda kalite etiketleri korunmuş hâliyle karşılaştır.
    if len(a) < 4 or len(a.split()) < 2:
        ka, kb = norm_keep(target), norm_keep(candidate)
        if ka and (ka == kb or ka.replace(" ", "") == kb.replace(" ", "")):
            return True
        if len(a) < 3:
            return False

    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    if b in ALIASES.get(a, set()) or a in ALIASES.get(b, set()):
        return True

    # Token kapsama: hedefin tüm kelimeleri adayda geçiyorsa ve
    # adayda en fazla 1 fazla kelime varsa eşleşmiş say.
    ta, tb = a.split(), b.split()
    if len(a) >= 4 and set(ta).issubset(set(tb)) and len(set(tb) - set(ta)) <= 1:
        return True
    return False


def check_stream(url):
    try:
        if urlparse(url).scheme not in ("http", "https"):
            return False, "unsupported-scheme"
        if requests:
            r = requests.get(
                url, timeout=TIMEOUT, headers={"User-Agent": UA},
                stream=True, allow_redirects=True
            )
            code = r.status_code
            ok = 200 <= code < 400
            if ok:
                # Gerçekten veri geliyor mu? Bazı sunucular 200 dönüp boş bırakır.
                try:
                    chunk = next(r.iter_content(chunk_size=1024), b"")
                    if not chunk:
                        ok = False
                        code = "empty-body"
                except Exception:
                    pass
            r.close()
            return ok, f"http-{code}"
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=TIMEOUT) as r:
            code = getattr(r, "status", 200)
            return 200 <= code < 400, f"http-{code}"
    except Exception as e:
        return False, type(e).__name__


def quality_score(text):
    u = (text or "").upper()
    if "8K" in u:
        return 5
    if "4K" in u or "UHD" in u or "2160" in u:
        return 4
    if "FHD" in u or "FULL HD" in u or "1080" in u:
        return 3
    if "HD" in u or "720" in u:
        return 2
    if "SD" in u or "480" in u or "576" in u:
        return 1
    return 0


def load_previous():
    """Önceki çıktıdaki çalışan linkleri {normalize_ad: url} olarak döndürür."""
    prev = {}
    if not OUT_FILE.exists():
        return prev
    try:
        lines = OUT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return prev
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        name = line.split(",", 1)[1].strip() if "," in line else ""
        if not name:
            continue
        for j in range(i + 1, len(lines)):
            s = lines[j].strip()
            if not s or s.startswith("#"):
                continue
            prev[norm_keep(name)] = s
            break
    return prev


def resolve_channel(ch, candidates, previous):
    """Tek bir kanal için en iyi çalışan URL'yi bulur."""
    name = ch.get("name", "")
    category = ch.get("category", "Diger")
    logo = ch.get("logo") or ch.get("tvg-logo") or ""

    matches = [x for x in candidates if channel_match(name, x[0])]
    matches.sort(key=lambda x: quality_score(x[0]), reverse=True)

    # Aynı URL birden fazla kaynakta olabilir, tekrarı at
    seen, uniq = set(), []
    for m in matches:
        if m[1] in seen:
            continue
        seen.add(m[1])
        uniq.append(m)
    uniq = uniq[:MAX_ATTEMPTS]

    attempts = []
    chosen = None
    for display, url, ext, source in uniq:
        ok, reason = check_stream(url)
        attempts.append({"url": url, "source": source, "ok": ok, "reason": reason})
        if ok:
            chosen = (display, url, source)
            break

    if chosen:
        display, url, source = chosen
        return {
            "name": name, "category": category, "logo": logo,
            "status": "working", "url": url, "source": source,
            "candidate_count": len(uniq), "attempts": attempts,
        }

    # Bu turda çalışan bulunamadı: önceki turdaki linki koru
    old = previous.get(norm_keep(name))
    if old:
        ok, reason = check_stream(old)
        if ok:
            return {
                "name": name, "category": category, "logo": logo,
                "status": "previous", "url": old, "source": "onceki-cikti",
                "candidate_count": len(uniq), "attempts": attempts,
            }

    return {
        "name": name, "category": category, "logo": logo,
        "status": "no_match" if not uniq else "dead_link",
        "url": None, "source": None,
        "candidate_count": len(uniq), "attempts": attempts,
    }


def main():
    if not CHANNELS_FILE.exists():
        raise SystemExit(f"[HATA] {CHANNELS_FILE} yok. Script durduruldu.")

    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    sources = read_sources()
    previous = load_previous()

    candidates = []
    source_errors = []
    for src in sources:
        try:
            text = fetch(src)
            found = parse_m3u(text, src)
            candidates.extend(found)
            print(f"[OK] kaynak ({len(found):>5} kanal): {src}")
        except Exception as e:
            source_errors.append({"source": src, "error": str(e)})
            print(f"[HATA] kaynak: {src} -> {e}")

    print(f"\nToplam aday kanal: {len(candidates)}")
    print(f"Kontrol edilecek sabit kanal: {len(channels)}\n")

    # Güvenlik freni: tüm kaynaklar çökerse eski dosyayı silme
    if not candidates and previous:
        raise SystemExit(
            "[HATA] Hiçbir kaynak okunamadı. Mevcut playlist korunuyor."
        )

    results = [None] * len(channels)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(resolve_channel, ch, candidates, previous): idx
            for idx, ch in enumerate(channels)
        }
        for fut in concurrent.futures.as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                ch = channels[idx]
                results[idx] = {
                    "name": ch.get("name", "?"),
                    "category": ch.get("category", "Diger"),
                    "logo": "", "status": "error", "url": None, "source": None,
                    "candidate_count": 0,
                    "attempts": [{"error": f"{type(e).__name__}: {e}"}],
                }

    out = ["#EXTM3U"]
    counts = {"working": 0, "previous": 0, "dead_link": 0, "no_match": 0, "error": 0}

    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["url"]:
            logo_attr = f' tvg-logo="{r["logo"]}"' if r["logo"] else ""
            out.append(
                f'#EXTINF:-1 tvg-name="{r["name"]}"{logo_attr} '
                f'group-title="{r["category"]}",{r["name"]}'
            )
            out.append(r["url"])

        if r["status"] == "working":
            print(f'[ÇALIŞIYOR]    {r["name"]} <- {r["url"]}')
        elif r["status"] == "previous":
            print(f'[ESKİ LİNK]    {r["name"]} <- {r["url"]}')
        elif r["status"] == "dead_link":
            print(f'[ÖLÜ LİNK]     {r["name"]} ({r["candidate_count"]} aday denendi)')
        elif r["status"] == "no_match":
            print(f'[EŞLEŞME YOK]  {r["name"]} (kaynaklarda bu isim yok)')
        else:
            print(f'[HATA]         {r["name"]}')

    report = {
        "total_channels": len(channels),
        "candidate_pool": len(candidates),
        "working": counts["working"],
        "previous": counts["previous"],
        "dead_link": counts["dead_link"],
        "no_match": counts["no_match"],
        "error": counts["error"],
        "source_errors": source_errors,
        "channels": results,
    }

    DATA.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    REPORT_FILE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"Toplam        : {report['total_channels']}")
    print(f"Çalışan       : {counts['working']}")
    print(f"Eski linkle   : {counts['previous']}")
    print(f"Ölü link      : {counts['dead_link']}")
    print(f"Eşleşme yok   : {counts['no_match']}")
    print(f"Hata          : {counts['error']}")
    print(f"Çıktı         : {OUT_FILE}")


if __name__ == "__main__":
    main()
