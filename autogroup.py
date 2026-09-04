"""
autogroup.py

Bu script playlist.m3u dosyasını okuyup #EXTINF satırlarında eksik olan
`group-title` özniteliklerini basit heuristiklerle tahmin edip ekler.

Ayrıca tahmin edilen grup isimlerini normalize eder (tutarlı yazım). Bu
güncelleme "A seçeneği" isteğinize göre grup adlarını olabildiğince
mevcut bilinen gruplarla eşleştirir veya baş harfleri büyük biçime çevirir.

Kullanım:
    python autogroup.py

Çıktı:
    playlist_grouped.m3u  <- group-title eklenmiş versiyon

Notlar:
- Heuristikler isim ve URL içindeki anahtar kelimelere dayanır. Gerekirse
  `KEYWORD_MAP` ve `CANONICAL_GROUPS` sözlüklerini düzenleyerek özel eşlemeler
  ekleyebilirsiniz.
- Varsayılan olarak orijinal dosyaya dokunmaz; yeni dosya üretir.
"""

import re
from pathlib import Path
from collections import Counter

INPUT = "playlist.m3u"
OUTPUT = "playlist_grouped.m3u"

# Basit anahtar kelime -> grup eşlemeleri (büyütülmüş karşılaştırma yapılır)
KEYWORD_MAP = {
    "RADYO": "Radyo",
    "RADYO7": "Radyo",
    "RADIO": "Radyo",
    "BEACH": "Beach",
    "PLAŽA": "Beach",
    "PLAŻA": "Beach",
    "CAM": "Cam",
    "WEBCAM": "Cam",
    "CAMERA": "Cam",
    "IBB": "İBB",
    "TAKSIM": "İBB",
    "MARE": "Beach",
    "ULUSAL": "Ulusal",
    "4K": "4K",
    "HD": "HD",
}

# URL tabanlı anahtar kelimeler
URL_MAP = {
    "whatsupcams": "Beach",
    "webcamera": "Beach",
    "hoktastream": "Beach",
    "beachcam": "Beach",
    "hls.ibb.gov.tr": "İBB",
    "radyotvonline": "Radyo",
    "radyotv": "Radyo",
    "ipcamera": "Cam",
}

EXTINF_RE = re.compile(r'(#EXTINF:)([^,]*)(,)(.*)')
GROUP_RE = re.compile(r'group-title\s*=\s*"([^\"]*)"', re.IGNORECASE)


def collect_known_groups(lines):
    groups = []
    for l in lines:
        m = GROUP_RE.search(l)
        if m:
            groups.append(m.group(1))
    return Counter(groups)


def _most_common_case(name_upper: str, known_groups: Counter):
    """Bilinen gruplarda büyük harfe göre arama yapıp orijinal yazımı döndürür."""
    for g in known_groups:
        if g and g.upper() == name_upper:
            return g
    # eğer birebir yoksa, en çok kullanılan benzer (içerme) grup varsa döndür
    for g in known_groups:
        if g and g.upper() in name_upper:
            return g
    return None


def normalize_group(group: str, known_groups: Counter) -> str:
    """Group adı için tutarlı bir yazım döndürür.

    - Eğer known_groups içinde aynı (case-insensitive) bir grup varsa onun
      orijinal yazımını kullanır.
    - Aksi halde kısa kısaltmaları (HD, 4K, İBB vb.) korur, diğerlerini title()
      ile baş harf büyük yapar.
    """
    if not group:
        return "Uncategorized"

    g = group.strip()
    # Eğer bilinen gruplardan biriyle eşleşiyorsa, o yazımı kullan
    mc = _most_common_case(g.upper(), known_groups)
    if mc:
        return mc

    # Belli kısa kısaltmaları olduğu gibi koru
    tokens = g.split()
    normalized_tokens = []
    for t in tokens:
        up = t.upper()
        if up in ("HD", "4K", "UHD", "FHD"):
            normalized_tokens.append(up)
        elif up in ("İBB", "IBB"):
            normalized_tokens.append("İBB")
        elif len(t) <= 3 and up == t:
            # kısaltma gibi görünen kısa tokenları olduğu gibi bırak
            normalized_tokens.append(up)
        else:
            # Türkçe karakterlerin korunması için title yaparken lower() kullanıyoruz
            normalized_tokens.append(t.title())

    return " ".join(normalized_tokens)


def infer_group(name: str, url: str, known_groups: Counter) -> str:
    """Basitçe isim ve url üzerindeki anahtar kelimelere göre grup tahmini yapar."""
    if not name:
        name = ""
    u = name.upper()
    # 1) doğrudan KEYWORD_MAP içinde eşleme
    for k, v in KEYWORD_MAP.items():
        if k in u:
            return normalize_group(v, known_groups)

    # 2) bilinen gruplarla eşleştirme (örnek: 'RADYO' içeren bilinen bir grup varsa kullan)
    for g in known_groups:
        if g and g.upper() in u:
            return normalize_group(g, known_groups)

    # 3) URL tabanlı tahmin
    url_low = (url or "").lower()
    for k, v in URL_MAP.items():
        if k in url_low:
            return normalize_group(v, known_groups)

    # 4) isimde ülke/şehir/keyword anahtar eşlemeleri
    if any(token in u for token in ("SEA", "MORZE", "PLA", "PLAZA", "PLAGE", "BEACH")):
        return normalize_group("Beach", known_groups)

    # 5) fallback: eğer bilinen gruplardan birine benzerse kullan (örnek: "RADYO" -> "Radyo")
    for g in known_groups:
        if g and g.upper() in u:
            return normalize_group(g, known_groups)

    # 6) son çare
    return normalize_group("Uncategorized", known_groups)


def process(lines):
    out_lines = []
    known = collect_known_groups(lines)
    changed = 0
    total = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            total += 1
            m = EXTINF_RE.match(line)
            if m:
                attrs = m.group(2) or ""
                name = m.group(4) or ""
                # Eğer zaten group-title varsa olduğu gibi al
                if GROUP_RE.search(attrs):
                    # normalize existing group value as well
                    existing = GROUP_RE.search(attrs).group(1)
                    norm = normalize_group(existing, known)
                    if norm != existing:
                        # replace original group-title value
                        new_attrs = GROUP_RE.sub(f'group-title="{norm}"', attrs)
                        new_line = f"{m.group(1)}{new_attrs}{m.group(3)}{m.group(4)}"
                        out_lines.append(new_line)
                    else:
                        out_lines.append(line)
                else:
                    # url genelde bir sonraki satırda
                    url = lines[i+1] if i+1 < len(lines) else ""
                    group = infer_group(name, url, known)
                    # eklemeyi attrs'ın sonuna yap, temiz boşluklar koru
                    new_attrs = attrs.rstrip() + (" " if attrs and not attrs.endswith(" ") else "") + f'group-title="{group}"'
                    new_line = f"{m.group(1)}{new_attrs}{m.group(3)}{m.group(4)}"
                    out_lines.append(new_line)
                    changed += 1
                # sonraki satırı (URL) de ekle
                if i+1 < len(lines):
                    out_lines.append(lines[i+1])
                    i += 2
                    continue
            # eğer extinf regexi eşlemezse normal ekle
            out_lines.append(line)
            i += 1
        else:
            out_lines.append(line)
            i += 1

    return out_lines, total, changed


def main():
    p = Path(INPUT)
    if not p.exists():
        print(f"Girdi dosyası bulunamadı: {INPUT}")
        return

    text = p.read_text(encoding='utf-8', errors='ignore')
    lines = text.splitlines()

    # Eğer dosya başında #EXTM3U yoksa ekleyelim
    if lines and not lines[0].startswith("#EXTM3U"):
        lines.insert(0, "#EXTM3U")

    out_lines, total, changed = process(lines)

    Path(OUTPUT).write_text('\n'.join(out_lines) + '\n', encoding='utf-8')

    print(f"Toplam EXTINF: {total}, group-title eklenen: {changed}")
    print(f"Çıktı kaydedildi: {OUTPUT}")


if __name__ == '__main__':
    main()
