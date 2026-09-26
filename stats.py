#!/usr/bin/env python3
"""
Сводка по блогу СТРЕЛЫ: dist/stats/index.html + dist/stats/stats.json

Контент — по истории git (дата появления каждого файла), страницы — по отчёту сборки.
Посещения — из Яндекс Метрики, если заданы:
  METRIKA_TOKEN   — OAuth-токен (секрет репозитория)
  METRIKA_COUNTER — номер счётчика блога (переменная репозитория)
  MAIN_COUNTER    — счётчик arrowsrealty.ru (по умолчанию 99759284)
Переходы на основной сайт = цель «to_main» (клик по ссылке на arrowsrealty.ru на блоге).
Запуск: после build.py и tilda_csv.py. Ошибки Метрики сборку не ломают.
"""
import datetime as dt, json, os, subprocess, urllib.parse, urllib.request
import build

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(build.DIST, "stats")
TOKEN = os.environ.get("METRIKA_TOKEN", "").strip()
COUNTER = build.metrika_id()
MAIN_COUNTER = os.environ.get("MAIN_COUNTER", "99759284").strip()
MSK = dt.timezone(dt.timedelta(hours=3))
TODAY = dt.datetime.now(MSK).date()

KINDS = {
    "content/articles": "Статьи блога",
    "content/tilda-posts": "Статьи для Тильды (Потоки)",
    "content/pages": "Страницы, доработанные под запросы",
    "content/zhk": "Описания ЖК",
}


# ------------------------------------------------------------------ контент
def added_dates(folder):
    """{файл: дата первого появления в git} для *.md в папке."""
    res = {}
    path = os.path.join(ROOT, folder)
    if not os.path.isdir(path):
        return res
    for fn in os.listdir(path):
        if not fn.endswith(".md"):
            continue
        rel = f"{folder}/{fn}"
        try:
            out = subprocess.run(["git", "log", "--diff-filter=A", "--follow", "--format=%ad", "--date=short", "--", rel],
                                 cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split()
            d = out[-1] if out else ""
        except Exception:
            d = ""
        res[rel] = dt.date.fromisoformat(d) if d else TODAY  # ещё не в git — значит новое
    return res


def periods():
    first_this = TODAY.replace(day=1)
    last_prev = first_this - dt.timedelta(days=1)
    return {
        "today": (TODAY, TODAY),
        "yesterday": (TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=1)),
        "last7": (TODAY - dt.timedelta(days=7), TODAY - dt.timedelta(days=1)),
        "week": (TODAY - dt.timedelta(days=6), TODAY),
        "month_to_date": (first_this, TODAY),
        "prev_month": (last_prev.replace(day=1), last_prev),
    }


PERIOD_NAMES = {"week": "Неделя (с сегодня)", "today": "Сегодня", "yesterday": "Вчера", "last7": "7 дней", "month_to_date": "С начала месяца", "prev_month": "Прошлый месяц"}


def content_stats():
    per = periods()
    res = {"total": {}, "by_period": {p: {} for p in per}}
    for folder, name in KINDS.items():
        dates = added_dates(folder)
        res["total"][name] = len(dates)
        for p, (a, b) in per.items():
            res["by_period"][p][name] = sum(1 for d in dates.values() if a <= d <= b)
    try:
        rep = json.load(open(os.path.join(build.DIST, "build-report.json"), encoding="utf-8"))
    except Exception:
        rep = {}
    res["site"] = {
        "Страниц в поиске (всего)": rep.get("indexed_pages"),
        "Объектов в каталоге": rep.get("objects"),
        "Страниц ЖК": rep.get("zhk"),
        "Страниц районов": rep.get("districts"),
    }
    return res


# ------------------------------------------------------------------ Метрика
def api(path, params):
    url = "https://api-metrika.yandex.net" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "OAuth " + TOKEN})
    return json.load(urllib.request.urlopen(req, timeout=60))


def total(counter, metrics, a, b, filters=None):
    p = {"ids": counter, "metrics": metrics, "date1": a.isoformat(), "date2": b.isoformat(), "accuracy": "full"}
    if filters:
        p["filters"] = filters
    d = api("/stat/v1/data", p)
    t = d.get("totals") or []
    return [int(round(x)) for x in t]


def goal_id(counter, identifier="to_main"):
    goals = api(f"/management/v1/counter/{counter}/goals", {}).get("goals", [])
    for g in goals:
        conds = g.get("conditions") or []
        if any(c.get("url") == identifier for c in conds) or g.get("name") == "Переход на arrowsrealty.ru":
            return g["id"]
    # пробуем создать цель (нужен токен с правом записи)
    try:
        body = json.dumps({"goal": {"name": "Переход на arrowsrealty.ru", "type": "action",
                                    "conditions": [{"type": "exact", "url": identifier}]}}).encode()
        req = urllib.request.Request(f"https://api-metrika.yandex.net/management/v1/counter/{counter}/goals",
                                     data=body, method="POST",
                                     headers={"Authorization": "OAuth " + TOKEN, "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=60))["goal"]["id"]
    except Exception:
        return None


def blog_host():
    return urllib.parse.urlparse(build.BASE).netloc


def traffic_stats():
    if not (TOKEN and COUNTER):
        return {"status": "Метрика не подключена: добавьте секрет METRIKA_TOKEN и переменную METRIKA_COUNTER"}
    res = {"status": "ok", "by_period": {}, "top_articles": [], "errors": []}
    gid = None
    try:
        gid = goal_id(COUNTER)
    except Exception as e:
        res["errors"].append(f"цели: {e}")
    for p, (a, b) in periods().items():
        if p == "week":
            continue
        row = {}
        try:
            v, pv, u = total(COUNTER, "ym:s:visits,ym:s:pageviews,ym:s:users", a, b)
            row.update({"Визиты блога": v, "Просмотры страниц": pv, "Посетители": u})
        except Exception as e:
            res["errors"].append(f"визиты {p}: {e}")
        try:
            (apv,) = total(COUNTER, "ym:pv:pageviews", a, b, "ym:pv:URLPath=@'/stati/'")
            row["Просмотры статей"] = apv
        except Exception as e:
            res["errors"].append(f"статьи {p}: {e}")
        if gid:
            try:
                (g,) = total(COUNTER, f"ym:s:goal{gid}reaches", a, b)
                row["Переходы на arrowsrealty.ru (клики)"] = g
            except Exception as e:
                res["errors"].append(f"цель {p}: {e}")
        try:
            (m,) = total(MAIN_COUNTER, "ym:s:visits", a, b, f"ym:s:referer=@'{blog_host()}'")
            row["Визиты на arrowsrealty.ru из блога"] = m
        except Exception as e:
            res["errors"].append(f"основной сайт {p}: {e}")
        try:
            (tp,) = total(MAIN_COUNTER, "ym:pv:pageviews", a, b, "ym:pv:URLPath=@'/news/'")
            row["Просмотры статей в Потоках (arrowsrealty.ru/news)"] = tp
        except Exception as e:
            res["errors"].append(f"потоки {p}: {e}")
        res["by_period"][p] = row
    try:
        a, b = periods()["last7"]
        d = api("/stat/v1/data", {"ids": COUNTER, "metrics": "ym:pv:pageviews", "dimensions": "ym:pv:URLPath",
                                  "filters": "ym:pv:URLPath=@'/stati/'", "date1": a.isoformat(), "date2": b.isoformat(),
                                  "sort": "-ym:pv:pageviews", "limit": 10})
        res["top_articles"] = [(r["dimensions"][0]["name"], int(r["metrics"][0])) for r in d.get("data", [])]
    except Exception as e:
        res["errors"].append(f"топ статей: {e}")
    return res


# ------------------------------------------------------------------ вывод
def text_summary(c, t, kind):
    """kind: day | week | month"""
    p = {"day": "yesterday", "week": "last7", "month": "prev_month"}[kind]       # посещения
    pc = {"day": "today", "week": "week", "month": "prev_month"}[kind]         # сделанный контент
    head = {"day": "Ежедневная сводка", "week": "Сводка за неделю", "month": "Сводка за прошлый месяц"}[kind]
    made = {"day": "Сделано сегодня:", "week": "Сделано за 7 дней:", "month": "Сделано за месяц:"}[kind]
    lines = [f"📊 {head} ({TODAY.strftime('%d.%m.%Y')})", "", made]
    for k, v in c["by_period"][pc].items():
        lines.append(f"• {k}: {v}")
    lines.append("")
    lines.append("Всего: " + ", ".join(f"{k.lower()} — {v}" for k, v in c["total"].items()))
    s = c["site"]
    lines.append(f"Страниц блога в поиске: {s['Страниц в поиске (всего)']}, объектов: {s['Объектов в каталоге']}")
    lines.append("")
    if t.get("status") != "ok":
        lines.append("Посещения: " + t.get("status", "нет данных"))
    else:
        lines.append({"day": "Посещения за вчера:", "week": "Посещения за 7 дней:", "month": "Посещения за прошлый месяц:"}[kind])
        for k, v in t["by_period"].get(p, {}).items():
            lines.append(f"• {k}: {v}")
        if kind != "day" and t.get("top_articles"):
            lines.append("")
            lines.append("Топ статей за 7 дней:")
            for path, n in t["top_articles"][:5]:
                lines.append(f"• {path} — {n}")
    lines.append("")
    lines.append(f"🔗 Блог: {build.BASE}/")
    lines.append(f"📈 Страница отчёта: {build.BASE}/stats/")
    return "\n".join(lines)


def html_table(title, data, cols):
    head = "".join(f"<th>{build.esc(PERIOD_NAMES.get(c, c))}</th>" for c in cols)
    keys = []
    for c in cols:
        for k in data.get(c, {}):
            if k not in keys:
                keys.append(k)
    rows = "".join("<tr><td>" + build.esc(k) + "</td>" + "".join(f"<td>{data.get(c, {}).get(k, '—')}</td>" for c in cols) + "</tr>" for k in keys)
    return f"<h2>{build.esc(title)}</h2><div style='overflow-x:auto'><table class='stats'><tr><th></th>{head}</tr>{rows}</table></div>"


def main():
    os.makedirs(OUT, exist_ok=True)
    c = content_stats()
    t = traffic_stats()
    data = {"date": TODAY.isoformat(), "content": c, "traffic": t,
            "summary_day": text_summary(c, t, "day"), "summary_week": text_summary(c, t, "week"),
            "summary_month": text_summary(c, t, "month")}
    with open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    cols = ["today", "yesterday", "last7", "month_to_date", "prev_month"]
    total_rows = "".join(f"<tr><td>{build.esc(k)}</td><td><b>{v}</b></td></tr>" for k, v in list(c["total"].items()) + list(c["site"].items()))
    body = f"""<p class="kicker">{build.ARROW_SVG}Внутренняя страница · не индексируется</p>
<h1>Сводка по блогу</h1>
<p class="muted">Обновлено {TODAY.strftime('%d.%m.%Y')}. Контент — по дате появления в репозитории, посещения — Яндекс Метрика.</p>
<h2>Всего</h2><table class="stats">{total_rows}</table>
{html_table('Сделано за период', c['by_period'], cols)}
{html_table('Посещения', t['by_period'], cols) if t.get('status') == 'ok' else '<h2>Посещения</h2><p>' + build.esc(t.get('status', '')) + '</p>'}
{('<h2>Топ статей за 7 дней</h2><ol>' + ''.join(f'<li>{build.esc(p)} — {n}</li>' for p, n in t.get('top_articles', [])) + '</ol>') if t.get('top_articles') else ''}
<style>.stats{{border-collapse:collapse;min-width:420px}}.stats td,.stats th{{border:1px solid var(--line);padding:8px 12px;text-align:left}}.stats th{{font-family:Oswald,sans-serif;text-transform:uppercase;font-size:13px;letter-spacing:.08em}}</style>"""
    build.write("/stats/", build.layout("Сводка по блогу | СТРЕЛЫ", "", "/stats/", body, noindex=True), index=False)
    print(data["summary_day"])
    if t.get("errors"):
        print("Метрика, ошибки:", t["errors"][:5])


if __name__ == "__main__":
    main()
