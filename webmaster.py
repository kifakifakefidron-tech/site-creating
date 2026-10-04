#!/usr/bin/env python3
"""
Яндекс Вебмастер для блога СТРЕЛЫ: сам добавляет сайт, подтверждает права мета-тегом,
отправляет sitemap и отдаёт в сводку показы и клики из поиска Яндекса (без cookie на сайте).

Токен: секрет WEBMASTER_TOKEN (права webmaster:hostinfo и webmaster:verify),
если его нет — пробуем METRIKA_TOKEN (если в том же приложении включены права Вебмастера).
Работает только для блога на своём домене (BASE_URL без пути): адрес github.io/site-creating подтвердить нельзя.

  python webmaster.py prepare   — до сборки: добавить сайт, получить код подтверждения (.webmaster.json)
  python webmaster.py verify    — после публикации: подтвердить права, отправить sitemap
Ошибки Вебмастера сборку не ломают.
"""
import datetime as dt, json, os, re, sys, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, ".webmaster.json")
API = "https://api.webmaster.yandex.net/v4"
TOKEN = (os.environ.get("WEBMASTER_TOKEN") or os.environ.get("METRIKA_TOKEN") or "").strip()
BASE = (os.environ.get("BASE_URL") or "https://blog.arrowsrealty.ru").rstrip("/")
MSK = dt.timezone(dt.timedelta(hours=3))
TODAY = dt.datetime.now(MSK).date()


def call(method, path, params=None, body=None):
    url = API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "OAuth " + TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def own_domain():
    """True, если блог на своём домене (подтверждать в Вебмастере можно только корень домена)."""
    u = urllib.parse.urlparse(BASE)
    return u.path in ("", "/") and not u.netloc.endswith("github.io")


def host_url():
    u = urllib.parse.urlparse(BASE)
    return f"{u.scheme}://{u.netloc}"


def user_id():
    st, d = call("GET", "/user")
    if st != 200:
        raise RuntimeError(f"нет доступа к Вебмастеру (HTTP {st}: {d.get('error_message', d)}) — нужен токен с правами webmaster:hostinfo и webmaster:verify")
    return d["user_id"]


def find_host(uid):
    """host_id блога: ищем среди сайтов пользователя, иначе добавляем."""
    want = urllib.parse.urlparse(host_url()).netloc.lower()
    st, d = call("GET", f"/user/{uid}/hosts")
    for h in d.get("hosts", []):
        if urllib.parse.urlparse(h.get("unicode_host_url") or h.get("ascii_host_url") or "").netloc.lower() == want:
            return h["host_id"], h.get("verified", False)
    st, d = call("POST", f"/user/{uid}/hosts", body={"host_url": host_url()})
    if st in (200, 201, 409) and d.get("host_id"):
        return d["host_id"], d.get("verified", False)
    raise RuntimeError(f"не удалось добавить сайт (HTTP {st}: {d.get('error_message', d)})")


def hid(host_id):
    return urllib.parse.quote(host_id, safe="")


def save(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def prepare():
    """До сборки: сайт добавлен, код подтверждения сохранён — build.py вставит мета-тег."""
    if not TOKEN or not own_domain():
        print("Вебмастер: пропуск —", "нет токена" if not TOKEN else f"блог не на своём домене ({BASE})")
        return
    uid = user_id()
    host_id, verified = find_host(uid)
    st, v = call("GET", f"/user/{uid}/hosts/{hid(host_id)}/verification")
    state = {"user_id": uid, "host_id": host_id, "verification_uin": v.get("verification_uin", ""),
             "verification_state": v.get("verification_state", "VERIFIED" if verified else "")}
    save(state)
    print(f"Вебмастер: сайт {host_id}, подтверждение: {state['verification_state'] or 'нет'}")


def verify():
    """После публикации: запросить проверку мета-тега и отправить sitemap."""
    if not TOKEN or not own_domain():
        return
    uid = user_id()
    host_id, verified = find_host(uid)
    st, v = call("GET", f"/user/{uid}/hosts/{hid(host_id)}/verification")
    state = v.get("verification_state")
    if state != "VERIFIED":
        if state != "IN_PROGRESS":
            st, v = call("POST", f"/user/{uid}/hosts/{hid(host_id)}/verification", {"verification_type": "META_TAG"})
            state = v.get("verification_state", f"HTTP {st}")
        print(f"Вебмастер: подтверждение прав — {state}" + (f" ({v.get('fail_info')})" if v.get("fail_info") else ""))
        if state != "VERIFIED":
            return
    sitemap = BASE + "/sitemap.xml"
    st, d = call("GET", f"/user/{uid}/hosts/{hid(host_id)}/user-added-sitemaps")
    if not any(s.get("sitemap_url") == sitemap for s in d.get("sitemaps", [])):
        st, d = call("POST", f"/user/{uid}/hosts/{hid(host_id)}/user-added-sitemaps", body={"url": sitemap})
        print(f"Вебмастер: sitemap {sitemap} — HTTP {st}")
    print("Вебмастер: права подтверждены")


def search_stats():
    """Для сводки: страницы в поиске, показы и клики из Яндекса, топ запросов. {} если Вебмастер не подключён."""
    if not TOKEN or not own_domain():
        return {"status": "Вебмастер не подключён: блог ещё не на своём домене" if TOKEN else
                "Вебмастер не подключён: добавьте секрет WEBMASTER_TOKEN"}
    try:
        uid = user_id()
        host_id, verified = find_host(uid)
    except Exception as e:
        return {"status": f"Вебмастер: {e}"}
    if not verified:
        st, v = call("GET", f"/user/{uid}/hosts/{hid(host_id)}/verification")
        if v.get("verification_state") != "VERIFIED":
            return {"status": "Вебмастер: сайт добавлен, права подтверждаются (обычно до суток)"}
    h = f"/user/{uid}/hosts/{hid(host_id)}"
    res = {"status": "ok"}
    st, s = call("GET", h + "/summary")
    if st == 200:
        res["Страниц в поиске Яндекса"] = s.get("searchable_pages_count")
        res["Исключено страниц"] = s.get("excluded_pages_count")
        res["ИКС"] = s.get("sqi")
        probs = s.get("site_problems") or {}
        res["Проблемы сайта"] = sum(v for k, v in probs.items() if k in ("FATAL", "CRITICAL"))
    else:
        res["status"] = "Вебмастер: данные ещё не загружены — сайт недавно добавлен"
        return res
    a, b = TODAY - dt.timedelta(days=8), TODAY - dt.timedelta(days=1)
    st, hist = call("GET", h + "/search-queries/all/history",
                    {"query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS"], "date_from": a.isoformat(), "date_to": b.isoformat()})
    if st == 200:
        ind = hist.get("indicators", {})
        def per(key, days):
            pts = ind.get(key, [])
            return int(sum(p["value"] for p in pts if (b - dt.timedelta(days=days - 1)).isoformat() <= p["date"][:10] <= b.isoformat()))
        last = max((p["date"][:10] for p in ind.get("TOTAL_SHOWS", [])), default="")
        res["Показы в поиске за 7 дней"] = per("TOTAL_SHOWS", 7)
        res["Клики из поиска за 7 дней"] = per("TOTAL_CLICKS", 7)
        res["Данные по"] = last
    st, top = call("GET", h + "/search-queries/popular",
                   {"order_by": "TOTAL_SHOWS", "query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION"],
                    "date_from": a.isoformat(), "date_to": b.isoformat(), "limit": 10})
    if st == 200:
        res["top_queries"] = [(q["query_text"], int(q["indicators"].get("TOTAL_SHOWS") or 0),
                               int(q["indicators"].get("TOTAL_CLICKS") or 0),
                               round(q["indicators"].get("AVG_SHOW_POSITION") or 0, 1)) for q in top.get("queries", [])]
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    try:
        {"prepare": prepare, "verify": verify}[cmd]()
    except Exception as e:
        print(f"Вебмастер: {e} — сборку продолжаем")
