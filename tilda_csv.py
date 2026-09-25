#!/usr/bin/env python3
"""
Статьи для Тильда Потоков: content/tilda-posts/*.md -> dist/tilda/posts.csv + обложки dist/covers/<slug>.jpg

Формат CSV — как в экспорте Тильды (import_example.csv):
Post ID;Alias;Title;Category;Media Type;Media;Description;Text;Date;Visibility;Thumb Image;Author Name
Text — JSON-массив блоков [{"ty":"text","te":"<html>"}].

Post ID постоянный (из slug), поэтому повторный импорт того же файла обновляет посты, а не дублирует.
Запуск: python3 tilda_csv.py  (после build.py, чтобы dist/ уже существовал)
"""
import csv, datetime as dt, hashlib, io, json, os, re, sys
import build  # общие функции: read_md, md_to_html, BASE, TODAY

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "content", "tilda-posts")
OUT = os.path.join(build.DIST, "tilda")
COVERS = os.path.join(build.DIST, "covers")
AUTHOR = "СТРЕЛЫ"
MAIN = "https://arrowsrealty.ru"
FEED_PATH = os.environ.get("TILDA_FEED_PATH", "news")  # адрес потока на arrowsrealty.ru

BG, INK, ACCENT, MUTED = (238, 230, 219), (20, 20, 20), (139, 107, 67), (110, 100, 87)


def post_id(slug):
    return hashlib.md5(("arrows-" + slug).encode()).hexdigest()[:10]


# ------------------------------------------------------------------ обложки
def find_font(bold=True):
    cands = [os.path.join(ROOT, "fonts", "Oswald.ttf"),
             "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else
             "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"]
    return next((c for c in cands if os.path.exists(c)), None)


def load_font(size, weight=700):
    from PIL import ImageFont
    path = find_font()
    f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    if path and "Oswald" in path:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def arrow(draw, x0, y, x1, color, w, head):
    """Фирменная стрела: двойное оперение слева, наконечник справа."""
    for dx in (0, head * 0.9):
        draw.line([(x0 + dx, y - head), (x0 + dx + head, y), (x0 + dx, y + head)], fill=color, width=w, joint="curve")
    draw.line([(x0 + head * 1.2, y), (x1, y)], fill=color, width=w)
    draw.line([(x1 - head * 1.5, y - head * 1.3), (x1, y), (x1 - head * 1.5, y + head * 1.3)], fill=color, width=w, joint="curve")


def make_cover(slug, title, kicker, path):
    from PIL import Image, ImageDraw
    W, H = 1200, 800
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    pad = 80
    small = load_font(26, 600)
    mark_path = os.path.join(ROOT, "static", "logo-arrows-full.png")
    if os.path.exists(mark_path):
        mark = Image.open(mark_path)
        mark.thumbnail((80, 48), Image.LANCZOS)
        im.paste(mark, (pad, 70 - mark.height // 2), mark)
        d.text((pad + mark.width + 18, 70), "С Т Р Е Л Ы", font=small, fill=INK, anchor="lm")
    else:
        arrow(d, pad, 70, pad + 58, INK, 2, 7)
        d.text((pad + 78, 70), "С Т Р Е Л Ы", font=small, fill=INK, anchor="lm")
    d.text((W - pad, 70), "ВТОРИЧКА · КРАСНОДАР", font=small, fill=MUTED, anchor="rm")
    # киккер
    kf = load_font(28, 700)
    arrow(d, pad, 205, pad + 50, ACCENT, 2, 6)
    d.text((pad + 70, 205), " ".join(kicker.upper()), font=kf, fill=ACCENT, anchor="lm")
    # заголовок — подбираем размер, чтобы влез в 4 строки
    t = title.upper()
    for size in (84, 76, 68, 60, 54, 48):
        tf = load_font(size, 700)
        lines = wrap(d, t, tf, W - 2 * pad)
        if len(lines) <= 4:
            break
    y = 250
    for ln in lines[:4]:
        d.text((pad, y), ln, font=tf, fill=INK)
        y += int(size * 1.12)
    # большая стрела
    arrow(d, pad, H - 95, W - pad, ACCENT, 9, 26)
    im.save(path, quality=88)


# ------------------------------------------------------------------ текст в блоки Тильды
def html_to_blocks(html):
    """Разбиваем HTML статьи на текстовые блоки Потоков. Заголовки — жирной строкой (отдельный блок)."""
    parts = re.findall(r"<(h2|h3|p|ul|ol|table|blockquote)[^>]*>(.*?)</\1>", html, re.S)
    blocks = []
    for tag, inner in parts:
        inner = inner.strip()
        if tag in ("h2", "h3"):
            te = f"<strong>{inner}</strong>"
        elif tag in ("ul", "ol"):
            items = re.findall(r"<li>(.*?)</li>", inner, re.S)
            mark = (lambda i: f"{i + 1}. ") if tag == "ol" else (lambda i: "— ")
            te = "<br />".join(mark(i) + it.strip() for i, it in enumerate(items))
        elif tag == "table":
            continue
        else:
            te = inner
        te = te.replace("\n", " ")
        # внутренние ссылки блога (/zhk/, /tip/) превращаем в абсолютные на блог
        te = re.sub(r'href="/(?!/)', f'href="{build.BASE}/', te)
        blocks.append({"ty": "text", "te": te})
    return blocks


def main():
    if not os.path.isdir(SRC):
        print("нет content/tilda-posts")
        return
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(COVERS, exist_ok=True)
    rows = []
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".md"):
            continue
        meta, body = build.read_md(os.path.join(SRC, fn))
        if meta.get("draft", "").lower() == "true":
            continue
        date = meta.get("date", build.TODAY.isoformat())
        if date > build.TODAY.isoformat():
            continue
        slug = meta.get("slug") or build.slugify(fn[:-3])
        html = build.md_to_html(body)
        cover = os.path.join(COVERS, slug + ".jpg")
        make_cover(slug, meta.get("title", slug), meta.get("category", "Статьи"), cover)
        cover_url = f"{build.BASE}/covers/{slug}.jpg"
        rows.append([
            post_id(slug), slug, meta.get("title", slug), meta.get("category", ""), "image", cover_url,
            meta.get("description", ""), json.dumps(html_to_blocks(html), ensure_ascii=False),
            f"{date} 09:00:00+00:00", "published", cover_url, AUTHOR,
        ])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    w.writerow(["Post ID", "Alias", "Title", "Category", "Media Type", "Media", "Description", "Text", "Date",
                "Visibility", "Thumb Image", "Author Name"])
    w.writerows(rows)
    with open(os.path.join(OUT, "posts.csv"), "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    # страница-подсказка со ссылкой на файл
    lst = "".join(f"<li>{build.esc(r[2])} <span class='muted'>— {MAIN}/{FEED_PATH}/{r[1]}</span></li>" for r in rows)
    page = build.layout("Статьи для Тильды — файл импорта", "", "/tilda/",
                        f"<h1>Статьи для Тильда Потоков</h1><p><a class='pill' href='/tilda/posts.csv' download>Скачать posts.csv</a></p>"
                        f"<p>Тильда → Потоки → поток → меню «…» → «Импортировать посты из CSV». Повторная загрузка обновляет посты, дублей не будет.</p><ol>{lst}</ol>",
                        noindex=True)
    build.write("/tilda/", page, index=False)
    print(f"tilda: {len(rows)} постов -> dist/tilda/posts.csv")


if __name__ == "__main__":
    main()
