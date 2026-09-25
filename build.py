#!/usr/bin/env python3
"""
Генератор blog.arrowsrealty.ru (СТРЕЛЫ).

Что делает:
  1. Забирает объекты из YML-фида Тильды (FEED_URL) и, если задано, из Google-таблицы (SHEET_CSV_URL).
  2. Строит страницы: ЖК, районы, тип квартиры, ЖК×тип, район×тип, тип×бюджет.
  3. Добавляет статьи из content/articles/*.md и авторские описания ЖК/районов из content/zhk, content/rayon.
  4. Пишет sitemap.xml, robots.txt, микроразметку. Результат — папка dist/.

Запуск локально:  FEED_FILE=tests/sample_feed.yml python3 build.py
На GitHub:        FEED_URL=... SHEET_CSV_URL=... python3 build.py
"""
import csv, datetime as dt, hashlib, html, io, json, os, re, shutil, sys, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
BASE = (os.environ.get("BASE_URL") or "https://blog.arrowsrealty.ru").rstrip("/")
PREFIX = re.sub(r"^https?://[^/]+", "", BASE)   # для адреса вида github.io/site-creating
MAIN = "https://arrowsrealty.ru"
BRAND = "СТРЕЛЫ"
PHONE = "+7 (961) 857-17-72"
TG = "https://t.me/ArtemRielty"
WA = "https://wa.me/message/5MQUMAFEHPL3B1"
MIN_COMBO = int(os.environ.get("MIN_COMBO", "2"))   # минимум объектов для страницы-комбинации
TODAY = dt.date.today()

ARROW_SVG = ('<svg class="arrow" viewBox="0 0 64 12" aria-hidden="true"><path d="M1 2l4 4-4 4M5 2l4 4-4 4M6 6h56M56 1.5l6 4.5-6 4.5" '
             'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')

def logo_html():
    for fn in ["logo.svg", "logo.png", "logo.webp", "logo.jpg"]:
        if os.path.exists(os.path.join(ROOT, "static", fn)):
            return f'<img src="/{fn}" alt="СТРЕЛЫ — вторичная недвижимость Краснодара" height="40">'
    return ARROW_SVG + '<b>СТРЕЛЫ</b><span class="sub">блог</span>'


# ----------------------------------------------------------------- утилиты
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              ["a","b","v","g","d","e","e","zh","z","i","i","k","l","m","n","o","p","r","s","t",
               "u","f","h","ts","ch","sh","sch","","i","","e","yu","ya"]))

def slugify(s):
    s = (s or "").lower().strip()
    s = "".join(TR.get(c, c) for c in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"

def esc(s):
    return html.escape(str(s or ""), quote=True)

def nice_case(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return s
    out = s.lower()
    out = re.sub(r"(^|[\s\-«\"(/])(\S)", lambda m: m.group(1) + m.group(2).upper(), out)
    for abbr in ["ЖК", "ФМР", "ЧМР", "ЦМР", "ЮМР", "КМР", "ГМР", "СМР", "ККБ", "РИП", "МЖК"]:
        out = re.sub(r"\b" + abbr.capitalize() + r"\b", abbr, out)
    return out

def rub(v):
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"

def mln(v):
    return (f"{v/1e6:.2f}".rstrip("0").rstrip(".")).replace(".", ",") + " млн ₽"

def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many

def pick(key, options):
    """Детерминированный выбор формулировки, чтобы страницы не были шаблонными копиями."""
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]

# ----------------------------------------------------------------- типы
TYPES = {
    "studiya": ("Студии", "студия", "студию", "студий"),
    "1k": ("Однокомнатные квартиры", "1-комнатная квартира", "1-комнатную квартиру", "1-комнатных квартир"),
    "2k": ("Двухкомнатные квартиры", "2-комнатная квартира", "2-комнатную квартиру", "2-комнатных квартир"),
    "3k": ("Трёхкомнатные квартиры", "3-комнатная квартира", "3-комнатную квартиру", "3-комнатных квартир"),
    "4k": ("Многокомнатные квартиры", "4+ комнатная квартира", "многокомнатную квартиру", "многокомнатных квартир"),
    "dom": ("Дома", "дом", "дом", "домов"),
}
BUDGETS = [3_000_000, 4_000_000, 5_000_000, 7_000_000, 10_000_000]

def detect_type(text):
    t = (text or "").upper()
    if re.search(r"СТУДИ|STUDIO", t):
        return "studiya"
    if re.search(r"\bДОМ\b|КОТТЕДЖ|ТАУНХАУС|ДОМОВЛАДЕН", t):
        return "dom"
    m = re.search(r"(?<![\dА-ЯЁ])Е\s*(\d)\b", t)          # «Е2 КВ», «Е3 КВ» — евро-формат
    if m:
        n = int(m.group(1))
        return {1: "studiya", 2: "2k", 3: "3k"}.get(n, "4k")
    m = re.search(r"(\d)\s*[-–]?\s*(?:К\b|КК|КВ\b|КОМН|-?КОМНАТ|ККВ|Е?\s*КОМНАТ)", t)
    if m:
        n = int(m.group(1))
        return {1: "1k", 2: "2k", 3: "3k"}.get(n, "4k")
    for word, k in [("ОДНОКОМН", "1k"), ("ДВУХКОМН", "2k"), ("ТРЁХКОМН", "3k"), ("ТРЕХКОМН", "3k"), ("ЕВРОДВУШ", "2k"), ("ЕВРОТР", "3k")]:
        if word in t:
            return k
    return None

# ----------------------------------------------------------------- разбор объекта
CLEAN = re.compile(r"[♟➳➵જ⁀➴📞✅🔥⭐️•☀-➿\U0001F300-\U0001FAFF]")

def parse_description(desc):
    raw = html.unescape(re.sub(r"<br\s*/?>", "\n", desc or "", flags=re.I))
    raw = re.sub(r"<[^>]+>", "\n", raw)
    lines = [CLEAN.sub("", l).strip(" -–:\t") for l in raw.split("\n")]
    lines = [l for l in lines if l]
    o = {"lines": lines}
    full = "\n".join(lines)
    for l in lines:
        u = l.upper()
        if u.startswith("РАЙОН") and "district" not in o:
            o["district"] = l[5:].strip(" :")
        elif u.startswith("ЖК") and "zhk" not in o:
            o["zhk"] = l
        elif re.match(r"^(УЛ\.|УЛИЦА|ПР\.|ПРОСП|ПЕР\.|ПРОЕЗД|Ш\.|Б-Р)", u) and "address" not in o:
            o["address"] = l
    m = re.search(r"(\d+[.,]?\d*)\s*м\s*2|(\d+[.,]?\d*)\s*м²|(\d+[.,]?\d*)\s*кв\.?\s*м", full, re.I)
    if m:
        o["area"] = float(next(g for g in m.groups() if g).replace(",", "."))
    m = re.search(r"Этаж\s*:?\s*(\d+)\s*/\s*(\d+)", full, re.I)
    if m:
        o["floor"], o["floors"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Состояние\s*[:\-–]?\s*(.+)", full, re.I)
    if m:
        o["condition"] = m.group(1).strip()
    m = re.search(r"В\s*ДКП\s*:?\s*(.+)", full, re.I)
    if m:
        o["dkp"] = m.group(1).strip()
    m = re.search(r"ОБРЕМЕНЕНИ[ЯЕ]\s*[:\-–]?\s*(.+)", full, re.I)
    if m:
        o["encumbrance"] = m.group(1).strip()
    m = re.search(r"Цена\s*[-–:]?\s*([\d\s]+)", full, re.I)
    if m:
        o["price_desc"] = int(re.sub(r"\D", "", m.group(1)) or 0)
    if lines:
        o["type"] = detect_type(lines[0]) or detect_type(full[:120])
    return o

def normalize_price(p):
    if not p:
        return None
    p = float(p)
    if p < 100_000:          # на сайте цены в тысячах: 2 700 = 2 700 000 ₽
        p *= 1000
    return p

def load_feed():
    src = os.environ.get("FEED_FILE")
    url = os.environ.get("FEED_URL") or "https://arrowsrealty.ru/tstore/yml/a950e007bd575604e7517f13082cfeba.yml"
    if src:
        data = open(os.path.join(ROOT, src), "rb").read()
    elif url:
        req = urllib.request.Request(url, headers={"User-Agent": "arrows-blog-builder"})
        data = urllib.request.urlopen(req, timeout=60).read()
    else:
        print("! FEED_URL / FEED_FILE не задан — объекты не загружены", file=sys.stderr)
        return []
    root = ET.fromstring(data)
    cats = {c.get("id"): (c.text or "").strip() for c in root.iter("category")}
    items = []
    for off in root.iter("offer"):
        g = lambda tag: (off.findtext(tag) or "").strip()
        params = {(p.get("name") or "").strip(): (p.text or "").strip() for p in off.findall("param")}
        name, desc = g("name"), g("description")
        o = parse_description(desc)
        vtype = detect_type(g("vendor"))          # Тильда кладёт «♟2К КВ♟» в vendor
        if vtype:
            o["type"] = vtype
        o.update({
            "id": off.get("id"),
            "available": off.get("available", "true") != "false",
            "name": name,
            "url": g("url"),
            "pictures": [p.text.strip() for p in off.findall("picture") if p.text],
            "category": cats.get(g("categoryId"), ""),
            "params": params,
        })
        if params.get("Жилой комплекс"):
            o["zhk"] = params["Жилой комплекс"]
        if not o.get("zhk") and name.upper().startswith("ЖК"):
            o["zhk"] = name
        if not o.get("district") and name.upper().startswith("РАЙОН"):
            o["district"] = name[5:].strip()
        if params.get("Год постройки дома"):
            o["year"] = params["Год постройки дома"]
        if params.get("Материал стен"):
            o["walls"] = params["Материал стен"]
        if params.get("Ключи у объекта/Быстрый показ", "").lower() == "да":
            o["keys"] = True
        if params.get("Ремонт"):
            o["repair"] = params["Ремонт"]
        coords = params.get("Координаты", "")
        m = re.match(r"\s*([\d.]+)\s*,\s*([\d.]+)", coords)
        if m:
            o["lat"], o["lng"] = float(m.group(1)), float(m.group(2))
        o["price"] = normalize_price(g("price") or o.get("price_desc"))
        if not o.get("type"):
            o["type"] = detect_type(name + " " + o.get("category", "")) or detect_type(desc)
        items.append(o)
    print(f"фид: {len(items)} объектов")
    return items

SHEET_COLS = {
    "zhk": ["жк", "жилой комплекс", "комплекс"],
    "district": ["район", "микрорайон"],
    "type": ["тип", "комнат", "комнаты", "кол-во комнат"],
    "area": ["площадь", "м2", "кв.м"],
    "floor": ["этаж"],
    "price": ["цена", "стоимость"],
    "url": ["ссылка", "url", "карточка"],
    "address": ["адрес"],
    "condition": ["состояние", "ремонт"],
}

def load_sheet():
    url = os.environ.get("SHEET_CSV_URL")
    src = os.environ.get("SHEET_FILE")
    if not (url or src):
        return []
    try:
        if src:
            text = open(os.path.join(ROOT, src), encoding="utf-8").read()
        else:
            text = urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
    except Exception as e:  # таблица — дополнительный источник, сборку не валим
        print(f"! таблица не загрузилась: {e}", file=sys.stderr)
        return []
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    idx = {}
    for key, names in SHEET_COLS.items():
        for i, h in enumerate(head):
            if any(n == h or n in h for n in names):
                idx.setdefault(key, i)
    items = []
    for r in rows[1:]:
        get = lambda k: r[idx[k]].strip() if k in idx and idx[k] < len(r) else ""
        if not (get("zhk") or get("district")):
            continue
        o = {"source": "sheet", "available": True, "pictures": [], "params": {}, "lines": []}
        for k in ["zhk", "district", "url", "address", "condition"]:
            if get(k):
                o[k] = get(k)
        o["type"] = detect_type(get("type")) or detect_type(get("type") + "к")
        a = re.search(r"\d+[.,]?\d*", get("area"))
        if a:
            o["area"] = float(a.group(0).replace(",", "."))
        f = re.search(r"(\d+)\s*/\s*(\d+)", get("floor"))
        if f:
            o["floor"], o["floors"] = int(f.group(1)), int(f.group(2))
        p = re.sub(r"[^\d]", "", get("price"))
        o["price"] = normalize_price(p) if p else None
        items.append(o)
    print(f"таблица: {len(items)} строк, распознаны колонки: {sorted(idx)}")
    return items

def merge(feed, sheet):
    """Фид — основной источник. Из таблицы добавляем только то, чего нет в фиде (по ссылке)."""
    seen = {o.get("url") for o in feed if o.get("url")}
    extra = [o for o in sheet if not o.get("url") or o["url"] not in seen]
    return feed + extra

def canon_zhk(s):
    s = (s or "").replace("ё", "е").replace("Ё", "Е")
    # очереди («Самолёт 6», «Акварели 2») оставляем — это разные дома с разными ценами
    s = re.sub(r"^\s*ЖК\s*", "", s or "", flags=re.I).strip(" «»\"")
    return nice_case(s)

def canon_district(s):
    s = (s or "").replace("ё", "е").replace("Ё", "Е")
    s = re.sub(r"^\s*(район|р-н|мкр\.?|микрорайон)\s*", "", s or "", flags=re.I).strip()
    s = s.replace("П.", "п. ").replace("Х.", "х. ")
    return nice_case(s)

def prepare(items):
    out = []
    for o in items:
        if not o.get("available", True):
            continue
        o["zhk_name"] = canon_zhk(o.get("zhk", "")) if o.get("zhk") else ""
        o["district_name"] = canon_district(o.get("district", "")) if o.get("district") else ""
        if o.get("price") and o.get("area"):
            o["ppm"] = o["price"] / o["area"]
        out.append(o)
    return out

# ----------------------------------------------------------------- контент (markdown)
def read_md(path):
    text = open(path, encoding="utf-8").read()
    meta, body = {}, text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
    return meta, body.strip()

def md_to_html(body):
    if md_lib:
        return md_lib.markdown(body, extensions=["tables", "sane_lists"])
    parts = []
    for block in re.split(r"\n\s*\n", body):
        b = block.strip()
        if b.startswith("### "):
            parts.append(f"<h3>{esc(b[4:])}</h3>")
        elif b.startswith("## "):
            parts.append(f"<h2>{esc(b[3:])}</h2>")
        elif b.startswith("- "):
            parts.append("<ul>" + "".join(f"<li>{esc(l[2:])}</li>" for l in b.splitlines()) + "</ul>")
        else:
            parts.append(f"<p>{esc(b)}</p>")
    return "\n".join(parts)

def extract_faq(body_html):
    """Вопросы из раздела «Частые вопросы» (h3 + следующий абзац) для FAQPage."""
    m = re.search(r"<h2[^>]*>\s*Частые вопросы\s*</h2>(.*?)(?=<h2|\Z)", body_html, re.S | re.I)
    if not m:
        return []
    qa = re.findall(r"<h3[^>]*>(.*?)</h3>\s*<p>(.*?)</p>", m.group(1), re.S)
    strip = lambda s: re.sub(r"<[^>]+>", "", s).strip()
    return [(strip(q), strip(a)) for q, a in qa]

def load_articles():
    arts = []
    folder = os.path.join(ROOT, "content", "articles")
    for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not fn.endswith(".md"):
            continue
        meta, body = read_md(os.path.join(folder, fn))
        if meta.get("draft", "").lower() == "true":
            continue
        date = meta.get("date", TODAY.isoformat())
        if date > TODAY.isoformat():       # отложенная публикация
            continue
        slug = meta.get("slug") or slugify(fn[:-3])
        arts.append({
            "slug": slug, "title": meta.get("title", slug), "description": meta.get("description", ""),
            "date": date, "updated": meta.get("updated", date), "category": meta.get("category", "Покупателям"),
            "keyword": meta.get("keyword", ""), "zhk": meta.get("zhk", ""), "district": meta.get("district", ""),
            "html": md_to_html(body),
        })
    arts.sort(key=lambda a: a["date"], reverse=True)
    return arts

def load_editorial(kind):
    folder = os.path.join(ROOT, "content", kind)
    res = {}
    for fn in os.listdir(folder) if os.path.isdir(folder) else []:
        if fn.endswith(".md"):
            meta, body = read_md(os.path.join(folder, fn))
            res[fn[:-3]] = {"meta": meta, "html": md_to_html(body)}
    return res

# ----------------------------------------------------------------- шаблон
def layout(title, description, path, body, crumbs=None, jsonld=None, noindex=False, og_image=None):
    url = BASE + path
    crumbs = crumbs or []
    graph = [{
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, **({"item": BASE + p} if p else {})}
                            for i, (n, p) in enumerate([("Блог СТРЕЛЫ", "/")] + crumbs)],
    }] if crumbs else []
    graph += jsonld or []
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False) if graph else ""
    crumb_html = ""
    if crumbs:
        trail = [f'<a href="/">Блог</a>'] + [f'<a href="{p}">{esc(n)}</a>' if p else f"<span>{esc(n)}</span>" for n, p in crumbs]
        crumb_html = '<nav class="crumbs" aria-label="Хлебные крошки">' + ' <i>→</i> '.join(trail) + "</nav>"
    LOGO_HTML = logo_html()
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{url}">
{'<meta name="robots" content="noindex, follow">' if noindex else ''}
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{url}">
<meta property="og:locale" content="ru_RU">
{f'<meta property="og:image" content="{esc(og_image)}">' if og_image else ''}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%23EEE6DB'/><path d='M14 38l12 12-12 12M28 38l12 12-12 12M30 50h56M72 36l14 14-14 14' fill='none' stroke='%23141414' stroke-width='6' stroke-linecap='round' stroke-linejoin='round'/></svg>">
{f'<script type="application/ld+json">{ld}</script>' if ld else ''}
</head>
<body>
<header class="top">
  <a class="logo" href="/">{LOGO_HTML}</a>
  <nav>
    <a href="/stati/">Статьи</a>
    <a href="/zhk/">ЖК</a>
    <a href="/rayony/">Районы</a>
    <a class="pill" href="{MAIN}/#CATALOG">Каталог объектов</a>
  </nav>
</header>
<main class="wrap">
{crumb_html}
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <p><b>СТРЕЛЫ</b> — сервис поиска вторичной недвижимости Краснодара для покупателей и риелторов.</p>
    <p><a href="{MAIN}">arrowsrealty.ru</a> · <a href="{MAIN}/sotrudnichestvo">Сотрудничество</a> · <a href="{MAIN}/calculator">Ипотечный калькулятор</a> · <a href="tel:{re.sub(r'[^+0-9]', '', PHONE)}">{PHONE}</a> · <a href="{TG}">Telegram</a> · <a href="{WA}">WhatsApp</a></p>
    <p class="muted">Цены и наличие объектов меняются ежедневно — уточняйте актуальность перед показом. Обновлено {TODAY.strftime('%d.%m.%Y')}.</p>
  </div>
</footer>
<!-- Yandex.Metrika: вставьте тот же счётчик 99759284, что на arrowsrealty.ru, если нужно общее отслеживание -->
</body>
</html>"""

def obj_card(o):
    t = TYPES.get(o.get("type"), (None, "объект"))[1]
    title = t.capitalize() + (f", {o['area']:g} м²" if o.get("area") else "")
    facts = []
    if o.get("floor"):
        facts.append(f"этаж {o['floor']}/{o.get('floors', '?')}")
    if o.get("condition"):
        facts.append(o["condition"].lower())
    if o.get("year"):
        facts.append(f"дом {o['year']} г.")
    badge = '<span class="badge">Быстрый показ</span>' if o.get("keys") else ""
    where = " · ".join(x for x in [("ЖК " + o["zhk_name"]) if o.get("zhk_name") else "", o.get("district_name", "")] if x)
    img = f'<img src="{esc(o["pictures"][0])}" alt="{esc(title + (", " + where if where else ""))}" loading="lazy" width="400" height="300">' if o.get("pictures") else '<div class="noimg">' + ARROW_SVG + '</div>'
    price = f'<b class="price">{mln(o["price"])}</b>' if o.get("price") else ""
    ppm = f'<span class="muted">{rub(o["ppm"])} за м²</span>' if o.get("ppm") else ""
    href = o.get("url") or f"{MAIN}/#CATALOG"
    return f"""<a class="card" href="{esc(href)}">
  {img}
  <div class="card-b">
    {badge}<div class="card-t">{esc(title)}</div>
    <div class="muted">{esc(where)}</div>
    <div class="muted">{esc(' · '.join(facts))}</div>
    <div class="card-p">{price}{ppm}</div>
    <span class="more">Смотреть объект&nbsp;→</span>
  </div>
</a>"""

def cards(objs, limit=60):
    objs = sorted(objs, key=lambda o: (o.get("price") or 9e12))
    more = ""
    if len(objs) > limit:
        more = f'<p class="center"><a class="pill" href="{MAIN}/#CATALOG">Ещё {len(objs) - limit} {plural(len(objs) - limit, "объект", "объекта", "объектов")} в каталоге&nbsp;→</a></p>'
    return '<div class="grid">' + "".join(obj_card(o) for o in objs[:limit]) + "</div>" + more

# ----------------------------------------------------------------- статистика и авто-текст
def stats(objs):
    prices = sorted(o["price"] for o in objs if o.get("price"))
    areas = sorted(o["area"] for o in objs if o.get("area"))
    ppms = sorted(o["ppm"] for o in objs if o.get("ppm"))
    med = lambda a: a[len(a) // 2] if a else None
    by_type = defaultdict(int)
    for o in objs:
        by_type[o.get("type")] += 1
    repaired = sum(1 for o in objs if re.search(r"рем|евро|дизайн", (o.get("condition") or "") + (o.get("repair") or ""), re.I)
                   and not re.search(r"без\s*рем|предчист|черн", (o.get("condition") or ""), re.I))
    furnished = sum(1 for o in objs if re.search(r"меб", o.get("condition") or "", re.I))
    full_dkp = sum(1 for o in objs if re.search(r"вся|полн", o.get("dkp") or "", re.I))
    years = sorted(int(o["year"]) for o in objs if str(o.get("year", "")).isdigit())
    return {
        "n": len(objs), "pmin": prices[0] if prices else None, "pmax": prices[-1] if prices else None,
        "pmed": med(prices), "amin": areas[0] if areas else None, "amax": areas[-1] if areas else None,
        "ppm": med(ppms), "by_type": dict(by_type), "repaired": repaired, "furnished": furnished,
        "full_dkp": full_dkp, "ymin": years[0] if years else None, "ymax": years[-1] if years else None,
    }

def auto_text(key, place, s, tname=None):
    """Абзацы из реальных данных базы. Формулировки варьируются по ключу страницы."""
    n = s["n"]
    what = tname or "квартир и домов"
    p = []
    p.append(pick(key + "a", [
        f"Сейчас в базе СТРЕЛЫ {n} {plural(n, 'предложение', 'предложения', 'предложений')} {what} — {place}. Все объекты — вторичный рынок: готовое жильё с оформленной собственностью.",
        f"{place[:1].upper() + place[1:]}: {n} {plural(n, 'актуальный объект', 'актуальных объекта', 'актуальных объектов')} ({what}) на вторичном рынке Краснодара по данным каталога СТРЕЛЫ.",
        f"Подобрали {n} {plural(n, 'вариант', 'варианта', 'вариантов')} {what} — {place}. Список собран из каталога СТРЕЛЫ и обновляется каждый день.",
    ]))
    if s["pmin"]:
        if s["pmin"] == s["pmax"]:
            p.append(f"Цена — {mln(s['pmin'])}.")
        else:
            p.append(pick(key + "b", [
                f"Цены — от {mln(s['pmin'])} до {mln(s['pmax'])}, медиана {mln(s['pmed'])}.",
                f"Бюджет входа — {mln(s['pmin'])}, самый дорогой вариант — {mln(s['pmax'])}; типичная цена около {mln(s['pmed'])}.",
            ]))
    if s["ppm"]:
        p.append(f"Средняя (медианная) стоимость квадратного метра по этим объектам — {rub(s['ppm'])}.")
    if s["amin"] and s["amax"] and s["amin"] != s["amax"]:
        p.append(f"Площади — от {s['amin']:g} до {s['amax']:g} м².")
    extra = []
    if s["repaired"]:
        extra.append(f"{s['repaired']} {plural(s['repaired'], 'объект', 'объекта', 'объектов')} с ремонтом")
    if s["furnished"]:
        extra.append(f"{s['furnished']} с мебелью")
    if s["full_dkp"]:
        extra.append(f"{s['full_dkp']} со всей суммой в ДКП")
    if extra:
        p.append("Среди них " + ", ".join(extra) + ".")
    if s["ymin"]:
        p.append(f"Год постройки домов — {s['ymin']}{'–' + str(s['ymax']) if s['ymax'] != s['ymin'] else ''}.")
    return "<p>" + " ".join(p) + "</p>"

def type_breakdown(s, link_base=None):
    rows = []
    for k in TYPES:
        c = s["by_type"].get(k, 0)
        if c:
            label = TYPES[k][0]
            if link_base:
                label = f'<a href="{link_base}{k}/">{label}</a>' if c >= MIN_COMBO else label
            rows.append(f"<li>{label} — {c}</li>")
    return "<ul class='chips'>" + "".join(rows) + "</ul>" if rows else ""

def auto_faq(place, s, key):
    qa = []
    if s["pmin"]:
        qa.append((f"Сколько стоит квартира {place} на вторичке?",
                   f"По данным каталога СТРЕЛЫ на {TODAY.strftime('%d.%m.%Y')} — от {mln(s['pmin'])} до {mln(s['pmax'])}, медианная цена {mln(s['pmed'])}."))
    if s["ppm"]:
        qa.append((f"Какая цена квадратного метра {place}?", f"Медианная цена за м² по объектам в базе — {rub(s['ppm'])}."))
    qa.append(("Как посмотреть квартиру или получить презентацию для клиента?",
               f"Откройте карточку объекта на arrowsrealty.ru — там можно скачать PDF-презентацию, в том числе со своими контактами. Для показа напишите в Telegram или позвоните {PHONE}."))
    qa.append(("Можно ли купить с ипотекой?",
               "Да, большинство объектов на вторичке продаются с рыночной ипотекой и материнским капиталом. Условия по конкретной квартире уточняйте у нас."))
    return qa

def faq_html(qa):
    if not qa:
        return ""
    return "<h2>Частые вопросы</h2>" + "".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in qa)

def faq_ld(qa):
    return [{"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa]}] if qa else []

def itemlist_ld(objs, name):
    els = [o for o in objs if o.get("url")][:30]
    return [{"@type": "ItemList", "name": name, "numberOfItems": len(objs),
             "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": o["url"]} for i, o in enumerate(els)]}]

CTA = f"""<div class="cta">
  <div><b>Риелтор с покупателем?</b><br>Соберите подборку и скачайте PDF-презентации со своими контактами — за пару минут.</div>
  <a class="pill" href="{MAIN}/#CATALOG">Открыть каталог&nbsp;→</a>
</div>"""

# ----------------------------------------------------------------- запись
PAGES = []  # (path, lastmod, priority)

def write(path, content, lastmod=None, priority="0.6", index=True):
    full = os.path.join(DIST, path.strip("/"), "index.html") if path.endswith("/") else os.path.join(DIST, path.strip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if PREFIX:
        content = re.sub(r'(href|src)="/(?!/)', lambda m: f'{m.group(1)}="{PREFIX}/', content)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    if index:
        PAGES.append((path, lastmod or TODAY.isoformat(), priority))

def related_articles(arts, zhk=None, district=None, n=3):
    rel = [a for a in arts if (zhk and a["zhk"].lower() == zhk.lower()) or (district and a["district"].lower() == district.lower())]
    rel += [a for a in arts if a not in rel]
    rel = rel[:n]
    if not rel:
        return ""
    return "<h2>Полезно почитать</h2><ul class='links'>" + "".join(f'<li><a href="/stati/{a["slug"]}/">{esc(a["title"])}</a></li>' for a in rel) + "</ul>"

# ----------------------------------------------------------------- страницы
def build_group_pages(objs, arts, field, kind, list_path, list_title, place_fmt, editorial):
    groups = defaultdict(list)
    for o in objs:
        if o.get(field):
            groups[o[field]].append(o)
    index_rows = []
    near = {}  # для перелинковки: соседние группы по координатам
    centers = {}
    for name, g in groups.items():
        pts = [(o["lat"], o["lng"]) for o in g if o.get("lat")]
        if pts:
            centers[name] = (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))
    for name, c in centers.items():
        others = sorted((((c[0] - c2[0]) ** 2 + (c[1] - c2[1]) ** 2, n2) for n2, c2 in centers.items() if n2 != name))
        near[name] = [n2 for _, n2 in others[:6]]

    for name, g in sorted(groups.items(), key=lambda x: -len(x[1])):
        slug = slugify(name)
        base = f"/{kind}/{slug}/"
        s = stats(g)
        place = place_fmt(name)
        ed = editorial.get(slug)
        noindex = s["n"] < MIN_COMBO and not ed
        title = f"{'Квартиры в ЖК ' + name if kind == 'zhk' else 'Квартиры: ' + place} на вторичке — {s['n']} {plural(s['n'], 'предложение', 'предложения', 'предложений')}{', от ' + mln(s['pmin']) if s['pmin'] else ''} | СТРЕЛЫ"
        h1 = f"Квартиры в ЖК {name} на вторичном рынке" if kind == "zhk" else f"Вторичка: {place}"
        desc = (f"{s['n']} {plural(s['n'], 'объект', 'объекта', 'объектов')} на вторичном рынке — {place}, Краснодар. "
                f"{'Цены от ' + mln(s['pmin']) + '. ' if s['pmin'] else ''}Фото, этажи, состояние, PDF-презентации для клиентов.")
        districts = sorted({o["district_name"] for o in g if o.get("district_name")}) if kind == "zhk" else []
        zhks = sorted({o["zhk_name"] for o in g if o.get("zhk_name")}) if kind == "rayony" else []
        rel_links = ""
        if districts:
            rel_links += "<p>Район: " + ", ".join(f'<a href="/rayony/{slugify(d)}/">{esc(d)}</a>' for d in districts) + "</p>"
        if zhks:
            rel_links += "<h2>ЖК в районе</h2><ul class='chips'>" + "".join(f'<li><a href="/zhk/{slugify(z)}/">ЖК {esc(z)}</a></li>' for z in zhks) + "</ul>"
        if near.get(name):
            label = "Рядом другие ЖК" if kind == "zhk" else "Соседние районы"
            rel_links += f"<h2>{label}</h2><ul class='chips'>" + "".join(f'<li><a href="/{kind}/{slugify(n2)}/">{"ЖК " if kind == "zhk" else ""}{esc(n2)}</a></li>' for n2 in near[name]) + "</ul>"
        qa = auto_faq(place, s, base)
        kick = "Жилой комплекс · вторичка" if kind == "zhk" else "Район · вторичка"
        body = f"""<p class="kicker">{ARROW_SVG}{kick}</p>
<h1>{esc(h1)}</h1>
<div class="lead">{auto_text(base, place, s)}</div>
{type_breakdown(s, base)}
{('<section class="editorial">' + ed['html'] + '</section>') if ed else ''}
<h2>Объекты</h2>
{cards(g)}
{CTA}
{rel_links}
{faq_html(qa)}
{related_articles(arts, zhk=name if kind == 'zhk' else None, district=name if kind == 'rayony' else None)}"""
        ld = itemlist_ld(g, h1) + faq_ld(qa)
        img = next((o["pictures"][0] for o in g if o.get("pictures")), None)
        write(base, layout(title, desc, base, body, [(list_title, list_path), (name if kind != "zhk" else "ЖК " + name, None)], ld, noindex, img),
              priority="0.8", index=not noindex)
        index_rows.append((name, base, s["n"], s["pmin"]))

        # комбинации: ЖК/район × тип
        by_t = defaultdict(list)
        for o in g:
            if o.get("type"):
                by_t[o["type"]].append(o)
        for t, tg in by_t.items():
            if len(tg) < MIN_COMBO:
                continue
            ts = stats(tg)
            tname = TYPES[t]
            p = f"{base}{t}/"
            ttl = f"{tname[0]} в {'ЖК ' + name if kind == 'zhk' else place} — {ts['n']} на вторичке{', от ' + mln(ts['pmin']) if ts['pmin'] else ''} | СТРЕЛЫ"
            h = f"{tname[0]} в {'ЖК ' + name if kind == 'zhk' else place} на вторичке"
            d = f"{ts['n']} {tname[3] if ts['n'] >= 5 else plural(ts['n'], tname[1], tname[1], tname[3])} — {('ЖК ' + name) if kind == 'zhk' else place}, Краснодар. {'От ' + mln(ts['pmin']) + '. ' if ts['pmin'] else ''}Фото, этаж, состояние, презентации."
            qa2 = auto_faq(place, ts, p)[:2]
            b = f"""<h1>{esc(h)}</h1>
<div class="lead">{auto_text(p, ('в ЖК ' + name) if kind == 'zhk' else place, ts, tname[3])}</div>
{cards(tg)}
{CTA}
<p>Все объекты — <a href="{base}">{'ЖК ' + esc(name) if kind == 'zhk' else esc(name)}</a> · <a href="/tip/{t}/">{tname[0].lower()} во всём Краснодаре</a></p>
{faq_html(qa2)}"""
            write(p, layout(ttl, d, p, b, [(list_title, list_path), ("ЖК " + name if kind == "zhk" else name, base), (tname[0], None)],
                            itemlist_ld(tg, h) + faq_ld(qa2)), priority="0.7")

    rows = "".join(f'<li><a href="{p}">{"ЖК " if kind == "zhk" else ""}{esc(n)}</a> <span class="muted">{c} · от {mln(pm) if pm else "—"}</span></li>'
                   for n, p, c, pm in sorted(index_rows, key=lambda r: r[0]))
    intro = ("Жилые комплексы Краснодара, в которых сейчас есть предложения на вторичном рынке в каталоге СТРЕЛЫ."
             if kind == "zhk" else "Районы и микрорайоны Краснодара с актуальными предложениями вторичного жилья.")
    write(list_path, layout(f"{list_title} Краснодара — вторичная недвижимость | СТРЕЛЫ", intro, list_path,
                            f"<h1>{list_title} Краснодара</h1><p class='lead'>{intro}</p><ul class='cols'>{rows}</ul>", [(list_title, None)]),
          priority="0.7")
    return index_rows

def build_type_pages(objs, arts):
    for t, names in TYPES.items():
        g = [o for o in objs if o.get("type") == t]
        if len(g) < MIN_COMBO:
            continue
        s = stats(g)
        p = f"/tip/{t}/"
        h = f"{names[0]} на вторичном рынке Краснодара"
        budget_links = []
        for b in BUDGETS:
            bg = [o for o in g if o.get("price") and o["price"] <= b]
            if len(bg) >= MIN_COMBO and len(bg) < len(g):
                bp = f"{p}do-{b // 1_000_000}-mln/"
                budget_links.append(f'<li><a href="{bp}">до {b // 1_000_000} млн — {len(bg)}</a></li>')
                bs = stats(bg)
                bh = f"{names[0]} до {b // 1_000_000} млн ₽ в Краснодаре — вторичка"
                write(bp, layout(f"{bh}: {bs['n']} вариантов | СТРЕЛЫ",
                                 f"{bs['n']} {plural(bs['n'], names[1], names[1], names[3])} до {b // 1_000_000} млн ₽ на вторичке Краснодара. Районы, ЖК, фото, презентации.",
                                 bp, f"<h1>{esc(bh)}</h1><div class='lead'>{auto_text(bp, f'в бюджете до {b // 1_000_000} млн ₽', bs, names[3])}</div>{cards(bg)}{CTA}<p><a href='{p}'>Все {names[0].lower()}</a></p>",
                                 [(names[0], p), (f"до {b // 1_000_000} млн", None)], itemlist_ld(bg, bh)), priority="0.7")
        by_z = defaultdict(int)
        for o in g:
            if o.get("zhk_name"):
                by_z[o["zhk_name"]] += 1
        top = sorted(by_z.items(), key=lambda x: -x[1])[:15]
        zl = "".join(f'<li><a href="/zhk/{slugify(z)}/{t if c >= MIN_COMBO else ""}{"/" if c >= MIN_COMBO else ""}">ЖК {esc(z)} — {c}</a></li>' for z, c in top)
        qa = auto_faq("в Краснодаре", s, p)[:2]
        body = f"""<h1>{esc(h)}</h1>
<div class="lead">{auto_text(p, 'в Краснодаре', s, names[3])}</div>
{('<h2>По бюджету</h2><ul class="chips">' + ''.join(budget_links) + '</ul>') if budget_links else ''}
{('<h2>Где больше всего предложений</h2><ul class="chips">' + zl + '</ul>') if zl else ''}
<h2>Объекты</h2>
{cards(g)}
{CTA}
{faq_html(qa)}
{related_articles(arts)}"""
        write(p, layout(f"{h} — {s['n']} предложений{', от ' + mln(s['pmin']) if s['pmin'] else ''} | СТРЕЛЫ",
                        f"{s['n']} {plural(s['n'], names[1], names[1], names[3])} на вторичке Краснодара{', от ' + mln(s['pmin']) if s['pmin'] else ''}. Фильтр по ЖК и районам, PDF-презентации для клиентов.",
                        p, body, [(names[0], None)], itemlist_ld(g, h) + faq_ld(qa)), priority="0.8")

def build_articles(arts):
    for a in arts:
        p = f"/stati/{a['slug']}/"
        faq = extract_faq(a["html"])
        others = [x for x in arts if x is not a][:4]
        ld = [{"@type": "Article", "headline": a["title"], "description": a["description"], "datePublished": a["date"],
               "dateModified": a["updated"], "inLanguage": "ru-RU", "mainEntityOfPage": BASE + p,
               "author": {"@type": "Person", "name": "Артём"},
               "publisher": {"@type": "Organization", "name": BRAND, "url": MAIN}}] + faq_ld(faq)
        body = f"""<article class="article">
<p class="kicker">{ARROW_SVG}{esc(a['category'])}</p>
<h1>{esc(a['title'])}</h1>
<p class="muted">{dt.date.fromisoformat(a['date']).strftime('%d.%m.%Y')} · СТРЕЛЫ</p>
{a['html']}
</article>
{CTA}
{('<h2>Ещё статьи</h2><ul class="links">' + ''.join(f'<li><a href="/stati/{x["slug"]}/">{esc(x["title"])}</a></li>' for x in others) + '</ul>') if others else ''}"""
        write(p, layout(a["title"] + " | СТРЕЛЫ", a["description"], p, body, [("Статьи", "/stati/"), (a["title"], None)], ld),
              lastmod=a["updated"], priority="0.7")
    rows = "".join(f'<li><a href="/stati/{a["slug"]}/">{esc(a["title"])}</a><br><span class="muted">{esc(a["description"])}</span></li>' for a in arts)
    write("/stati/", layout("Статьи о вторичной недвижимости Краснодара | СТРЕЛЫ",
                            "Ипотека, проверка квартир, сделки на вторичном рынке Краснодара — статьи для покупателей и риелторов.",
                            "/stati/", f"<h1>Статьи</h1><ul class='arts'>{rows or '<li>Скоро здесь появятся статьи.</li>'}</ul>", [("Статьи", None)]),
          priority="0.8")

def build_home(objs, arts, zhk_rows, district_rows):
    s = stats(objs) if objs else None
    top_z = sorted(zhk_rows, key=lambda r: -r[2])[:12]
    top_d = sorted(district_rows, key=lambda r: -r[2])[:12]
    types = "".join(f'<li><a href="/tip/{t}/">{TYPES[t][0]}</a></li>' for t in TYPES if s and s["by_type"].get(t, 0) >= MIN_COMBO)
    latest = "".join(f'<li><a href="/stati/{a["slug"]}/">{esc(a["title"])}</a></li>' for a in arts[:6])
    body = f"""<section class="hero">
  <p class="kicker">{ARROW_SVG}Вторичка · Краснодар</p>
  <h1>Вторичная недвижимость Краснодара — база объектов и гид по ЖК</h1>
  <p class="lead">{'Сейчас в базе ' + str(s['n']) + ' ' + plural(s['n'], 'объект', 'объекта', 'объектов') + ' в ' + str(len(zhk_rows)) + ' ЖК. ' if s else ''}Цены, фото, этажи и PDF-презентации для клиентов — для покупателей и риелторов.</p>
  <a class="pill" href="{MAIN}/#CATALOG">Каталог объектов&nbsp;→</a>
</section>
{('<h2>По типу</h2><ul class="chips">' + types + '</ul>') if types else ''}
{('<h2>Популярные ЖК</h2><ul class="chips">' + ''.join(f'<li><a href="{p}">ЖК {esc(n)} — {c}</a></li>' for n, p, c, _ in top_z) + '</ul><p><a href="/zhk/">Все ЖК&nbsp;→</a></p>') if top_z else ''}
{('<h2>Районы</h2><ul class="chips">' + ''.join(f'<li><a href="{p}">{esc(n)} — {c}</a></li>' for n, p, c, _ in top_d) + '</ul><p><a href="/rayony/">Все районы&nbsp;→</a></p>') if top_d else ''}
{('<h2>Свежие статьи</h2><ul class="links">' + latest + '</ul>') if latest else ''}
{CTA}"""
    ld = [{"@type": "RealEstateAgent", "name": f"{BRAND} — сервис поиска недвижимости Краснодара", "url": MAIN,
           "telephone": PHONE, "areaServed": {"@type": "City", "name": "Краснодар"}, "sameAs": [TG, WA, BASE]}]
    write("/", layout("Вторичка Краснодара: квартиры по ЖК и районам, статьи для покупателей | СТРЕЛЫ",
                      "Гид по вторичному рынку Краснодара: предложения по ЖК и районам с ценами, статьи об ипотеке и сделках. База СТРЕЛЫ для покупателей и риелторов.",
                      "/", body, None, ld), priority="1.0")

def build_meta():
    urls = "".join(f"<url><loc>{BASE}{p}</loc><lastmod>{lm}</lastmod><priority>{pr}</priority></url>" for p, lm, pr in PAGES)
    with open(os.path.join(DIST, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    with open(os.path.join(DIST, "robots.txt"), "w") as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
    host = re.sub(r"^https?://", "", BASE)
    if "/" not in host and not host.endswith("github.io"):
        with open(os.path.join(DIST, "CNAME"), "w") as f:
            f.write(host + "\n")
    write("/404.html", layout("Страница не найдена | СТРЕЛЫ", "", "/404.html",
                              f"<h1>Страница не найдена</h1><p>Возможно, объект уже продан. <a href='/'>На главную блога</a> или <a href='{MAIN}/#CATALOG'>в каталог</a>.</p>"),
          index=False)

def main():
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST)
    for fn in os.listdir(os.path.join(ROOT, "static")):
        shutil.copy(os.path.join(ROOT, "static", fn), os.path.join(DIST, fn))
    objs = prepare(merge(load_feed(), load_sheet()))
    arts = load_articles()
    zhk_rows = build_group_pages(objs, arts, "zhk_name", "zhk", "/zhk/", "ЖК",
                                 lambda n: f"в ЖК {n}", load_editorial("zhk"))
    d_rows = build_group_pages(objs, arts, "district_name", "rayony", "/rayony/", "Районы",
                               lambda n: f"район {n}", load_editorial("rayon"))
    build_type_pages(objs, arts)
    build_articles(arts)
    build_home(objs, arts, zhk_rows, d_rows)
    build_meta()
    no_type = sum(1 for o in objs if not o.get("type"))
    report = {"date": TODAY.isoformat(), "objects": len(objs), "zhk": len(zhk_rows), "districts": len(d_rows),
              "articles": len(arts), "indexed_pages": len(PAGES), "objects_without_type": no_type,
              "objects_without_zhk_or_district": sum(1 for o in objs if not o.get("zhk_name") and not o.get("district_name"))}
    with open(os.path.join(DIST, "build-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
