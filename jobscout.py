#!/usr/bin/env python3
"""JobScout: ежедневная подборка вакансий (вайбкодинг / AI-first / React) в Telegram.

Только стандартная библиотека Python 3.8+. Источники:
  - hh.ru (страница поиска + страница вакансии, т.к. анонимный API закрыт)
  - Habr Career
  - hireseeker.ru (агрегатор вайбкодинг-вакансий)
  - публичные Telegram-каналы (t.me/s/<канал>)

Секреты: TG_TOKEN и TG_CHAT (или TG_CHATID) из env-файла:
  $JOBSCOUT_ENV, затем ./.env рядом со скриптом.

Запуск:
  python3 jobscout.py            # собрать, отправить в Telegram, запомнить показанное
  python3 jobscout.py --dry-run  # только напечатать подборку, ничего не отправлять и не запоминать
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "seen.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# ---------------------------------------------------------------- настройки поиска

HH_QUERIES = [
    'вайбкодинг OR вайб-кодинг OR "vibe coding" OR "vibe coder" OR вайбкодер',
    'React AND (Cursor OR "Claude Code" OR "AI-first" OR "AI-native" OR Codex OR "AI-assisted")',
    'frontend AND ("Claude Code" OR Cursor OR "AI-first" OR "AI-native")',
]
HABR_QUERIES = ["Claude Code", "вайбкодинг", "Cursor", "AI-first", "AI-native"]
# LinkedIn: гостевой поиск без логина, удалёнка (f_WT=2), весь мир (geoId 92000000)
LINKEDIN_QUERIES = ["vibe coding", "React Claude Code", "frontend AI-native", "React Cursor AI"]
# фронтенд-каналы: читаем последние посты целиком + ищем по TG_QUERIES
TG_CHANNELS = ["rabotafrontend", "job_react", "forfrontend", "frontend_rabota",
               "JScript_jobs", "javascript_jobs_feed", "runello_rus_frontend"]
# общие IT-каналы: только поиск по TG_QUERIES (иначе утонем в нерелевантном)
TG_SEARCH_ONLY = ["getitrussia", "it_vakansii_jobs", "remotejobss", "remotegeekjob", "Remoteit", "devjobs",
                  "geekjobs", "jobscode_infull", "job_webdev", "over100", "partnerkin_job", "projects_jobs_feed",
                  "vacanciesrus", "ai_rabota", "jobforphp", "dnative_job"]
TG_QUERIES = ["Claude", "Cursor", "вайбкод", "vibe", "AI-first", "AI-native"]
# закрытые чаты/группы — читаются от вашего аккаунта через Telethon (нужны TG_API_ID/TG_API_HASH и --tg-login)
TG_PRIVATE = ["vakansii_chatgpt"]
TG_SESSION = BASE / "tg_user"  # файл tg_user.session = доступ к аккаунту, никому не передавать
LOOKBACK_DAYS = 3        # сколько дней назад смотреть (дубли отсекает seen.json)
MIN_SCORE = 9            # порог «подходит»
MIN_SALARY_RUB = 100_000  # если верх вилки известен и ниже — вакансию не показываем
RATES = {"₽": 1, "$": 85, "€": 95, "₸": 0.17}
MAX_ITEMS = 15           # максимум вакансий в одном сообщении
SEEN_TTL_DAYS = 60

# профиль: Senior Frontend, React/JS, Cursor/Claude Code, удалёнка, РФ
AI_TERMS = [
    (r"вайб[- ]?код|vibe[- ]?cod", 4),
    (r"claude", 3),
    (r"\bcursor\b", 2),
    (r"ai[- ]?(first|native|assisted|driven|augmented)", 2),
    (r"\bcodex\b|copilot|windsurf", 1),
    (r"llm|агент|agent", 1),
]
STACK_TERMS = [
    (r"\breact\b", 4),
    (r"front[- ]?end|фронтенд|фронт-енд", 2),
    (r"full[- ]?stack|фулстек|фуллстек", 1),
    (r"typescript|\bts\b", 1),
    (r"next\.?js", 1),
    (r"tailwind", 1),
    (r"javascript|\bjs\b", 1),
    (r"\bphp\b|jquery|figma", 1),
]
# роли, которые не подходят (смотрим в заголовке)
BAD_ROLE = re.compile(
    r"дизайнер|designer|маркетолог|marketing|продаж|sales|менеджер|manager|аналитик|analyst|"
    r"\bqa\b|тестировщ|devops|\bsre\b|mlops|ml[- ]engineer|data (scien|engineer)|\b1с\b|\b1c\b|"
    r"стаж[её]р|intern|junior|младш|начинающ|ассистент|помощник|рекрутер|recruiter|\bhr\b|контент|copywrit|smm|seo|"
    r"техлид|tech lead|team ?lead|тимлид|head of|\bcto\b|архитектор|architect|ios|android|"
    r"продюсер|producer|креатор|креатив|presale|пресейл|автоматизац|automation|интеграц",
    re.I,
)
# чужой основной язык — отсекаем, только если в заголовке нет фронта/фулстека
BAD_LANG = re.compile(r"\bjava\b(?!script)|\.net|\bc#|golang|\bgo\b|python|kotlin|swift|\bphp\b|laravel|backend|бэкенд", re.I)
FRONT_TITLE = re.compile(r"front|фронт|react|full|фул|vibe|вайб", re.I)


class _BadTitle:
    @staticmethod
    def search(title):
        return BAD_ROLE.search(title) or (BAD_LANG.search(title) and not FRONT_TITLE.search(title))


BAD_TITLE = _BadTitle
# заголовок должен намекать на разработку
DEV_TITLE = re.compile(
    r"разработ|developer|engineer|инженер|программист|вайб|vibe|coder|кодер|front|фронт|full|фул|react|web|веб",
    re.I,
)
NOT_REMOTE = re.compile(r"только офис|офис в |работа в офисе|office only|on-?site", re.I)
GEO_BAN = re.compile(
    r"не рассматрива\w* (кандидат\w* )?(из )?(рф|росси)|кроме (рф|росси)|вне (рф|росси)|"
    r"outside (of )?russia|not (based )?in russia|релокац|relocat",
    re.I,
)


def score(title, text, remote):
    t = f"{title}\n{text}".lower()
    if BAD_TITLE.search(title) or not DEV_TITLE.search(title):
        return -100, []
    if remote is False:  # только офис/гибрид — из Аксая не подходит
        return -100, ["офис"]
    t0 = f"{title}\n{text}".lower()
    if remote is None and re.search(r"офис|гибрид|hybrid|office|on-?site", t0) and \
            not re.search(r"удал[её]н|удаленк|remote|из любой точки", t0):
        return -100, ["офис"]
    s, why = 0, []
    ai = 0
    for pat, w in AI_TERMS:
        m = re.search(pat, t)
        if m:
            ai += w
            why.append(m.group(0))
    s += min(ai, 8)
    for pat, w in STACK_TERMS:
        m = re.search(pat, t)
        if m:
            s += w
            why.append(m.group(0))
    if remote is True or re.search(r"удал[её]н|remote|из любой точки", t):
        s += 2
    elif remote is False or NOT_REMOTE.search(t):
        s -= 5
    # профиль фронтендовый: заголовок важнее упоминаний в тексте
    if re.search(r"front|фронт|react", title, re.I):
        s += 4
    elif re.search(r"вайб|vibe", title, re.I):
        s += 3
    if BAD_LANG.search(title):
        s -= 3
    if GEO_BAN.search(t):
        s -= 4
        why.append("⚠️ гео/релокация")
    if ai == 0:          # без AI-составляющей это не «вайбкодинг»-подборка
        s -= 3
    seen, uniq = set(), []
    for w in why:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(w)
    return s, uniq


# ---------------------------------------------------------------- http

def fetch(url, params=None, data=None, timeout=30, retries=2):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — сеть ненадёжна, источник просто пропускаем
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def strip_tags(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def fmt_money(lo, hi, cur):
    cur = {"RUR": "₽", "rur": "₽", "RUB": "₽", "USD": "$", "usd": "$", "EUR": "€", "eur": "€", "KZT": "₸", "kzt": "₸"}.get(cur or "", cur or "")
    f = lambda x: f"{int(x):,}".replace(",", " ")
    if lo and hi:
        return f"{f(lo)}–{f(hi)} {cur}"
    if lo:
        return f"от {f(lo)} {cur}"
    if hi:
        return f"до {f(hi)} {cur}"
    return ""


# ---------------------------------------------------------------- источники

def hh_state(page):
    m = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', page, re.S)
    return json.loads(html.unescape(m.group(1))) if m else {}


def salary_too_low(s):
    """'до 50 000 ₽' / '800–1 200 $' → True, если вилка явно ниже MIN_SALARY_RUB."""
    cur = next((c for c in RATES if c in (s or "")), None)
    nums = [int(n.replace(" ", "").replace("\xa0", "")) for n in re.findall(r"\d[\d \xa0]*\d|\d", s or "")]
    if not cur or not nums:
        return False
    top = max(nums) * RATES[cur]
    if s.strip().startswith("от"):  # «от X» — верх неизвестен, режем только совсем низкое
        return top < MIN_SALARY_RUB * 0.6
    return top < MIN_SALARY_RUB


def hh_money(c):
    if not isinstance(c, dict) or c.get("noCompensation"):
        return ""
    return fmt_money(c.get("from"), c.get("to"), c.get("currencyCode") or c.get("currency"))


def src_hh(seen):
    out, ids = [], {}
    for q in HH_QUERIES:
        page = fetch("https://hh.ru/search/vacancy", {
            "text": q, "schedule": "remote", "order_by": "publication_time",
            "search_period": LOOKBACK_DAYS, "items_on_page": 50,
        })
        for v in hh_state(page).get("vacancySearchResult", {}).get("vacancies", []):
            ids[str(v.get("vacancyId"))] = v
        time.sleep(1.5)
    for vid, v in ids.items():
        key = f"hh:{vid}"
        if key in seen:
            continue
        title = v.get("name", "")
        if BAD_TITLE.search(title):
            seen[key] = time.time()  # заведомо мимо — не тратим запрос на детали
            continue
        comp = v.get("company")
        it = {
            "key": key, "source": "hh", "title": title,
            "company": comp.get("name", "") if isinstance(comp, dict) else "",
            "salary": hh_money(v.get("compensation")), "url": f"https://hh.ru/vacancy/{vid}",
            "remote": hh_is_remote(v), "text": "",
        }
        hh_enrich(it, vid)
        out.append(it)
    return out


def hh_is_remote(v):
    """True — можно удалённо, False — только офис/гибрид, None — неизвестно."""
    formats = json.dumps(v.get("workFormats") or [])
    if "REMOTE" in formats or v.get("@workSchedule") == "remote":
        return True
    if "ON_SITE" in formats or "HYBRID" in formats or v.get("@workSchedule"):
        return False
    return None


def hh_enrich(it, vid):
    """Дочитывает со страницы вакансии описание, навыки, зарплату и формат работы."""
    try:
        full = hh_state(fetch(f"https://hh.ru/vacancy/{vid}"))["vacancyView"]["vacancyFull"]["vacancy"]
    except Exception:  # noqa: BLE001
        return
    skills = full.get("keySkills") or []
    it["text"] = strip_tags(full.get("description", "")) + "\n" + " ".join(skills if isinstance(skills, list) else [])
    it["salary"] = hh_money(full.get("compensation")) or it["salary"]
    comp = full.get("company")
    if not it["company"] and isinstance(comp, dict):
        it["company"] = comp.get("name", "")
    remote = hh_is_remote(full)
    if remote is not None:
        it["remote"] = remote
    time.sleep(1.0)


def src_habr(seen):
    out, got = [], {}
    for q in HABR_QUERIES:
        page = fetch("https://career.habr.com/vacancies", {"q": q, "remote": "true", "type": "all", "sort": "date"})
        m = re.search(r"<script[^>]*data-ssr-state[^>]*>(.*?)</script>", page, re.S) or \
            re.search(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S)
        if not m:
            continue
        for v in json.loads(m.group(1)).get("vacancies", {}).get("list", []):
            got[str(v.get("id"))] = v
        time.sleep(1.5)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)
    for vid, v in got.items():
        key = f"habr:{vid}"
        if key in seen or v.get("archived"):
            continue
        try:
            if dt.datetime.fromisoformat(v["publishedDate"]["date"]) < cutoff:
                continue
        except Exception:  # noqa: BLE001
            pass
        sal = v.get("salary") or {}
        out.append({
            "key": key, "source": "Habr Career", "title": v.get("title", ""),
            "company": (v.get("company") or {}).get("title", ""),
            "salary": sal.get("formatted") or fmt_money(sal.get("from"), sal.get("to"), sal.get("currency")),
            "url": "https://career.habr.com" + v.get("href", ""),
            "remote": bool(v.get("remoteWork")),
            "text": " ".join(s.get("title", "") for s in v.get("skills") or []),
        })
    return out


def src_hireseeker(seen):
    page = fetch("https://hireseeker.ru/vacancy-list/vibe_coding")
    out, prev = [], 0
    for m in re.finditer(r'<a href="([^"]+)"[^>]*aria-label="Открыть на ([^:"]+): ([^"]+)"', page):
        url, title = html.unescape(m.group(1)), html.unescape(m.group(3))
        card = strip_tags(page[max(prev, m.start() - 6000):m.start()])[-1500:]
        prev = m.end()
        hh_id = re.search(r"hh\.ru/vacancy/(\d+)", url)
        key = f"hh:{hh_id.group(1)}" if hh_id else f"url:{url}"
        if key in seen:
            continue
        it = {
            "key": key, "source": "hireseeker", "title": title, "company": "",
            "salary": "", "url": url, "remote": None, "text": card,
        }
        if hh_id and not BAD_TITLE.search(title):  # карточка короткая — формат работы смотрим на hh
            hh_enrich(it, hh_id.group(1))
        out.append(it)
    return out


def src_telegram(seen):
    out, got = [], set()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)
    pages = [(ch, f"https://t.me/s/{ch}") for ch in TG_CHANNELS]
    pages += [(ch, f"https://t.me/s/{ch}?q={urllib.parse.quote(q)}") for ch in TG_CHANNELS + TG_SEARCH_ONLY for q in TG_QUERIES]
    for ch, url in pages:
        try:
            page = fetch(url, retries=1)
        except Exception as e:  # noqa: BLE001
            print(f"[tg] {url}: {e}", file=sys.stderr)
            continue
        time.sleep(0.7)
        for block in page.split('class="tgme_widget_message_wrap')[1:]:
            post = re.search(r'data-post="([^"]+)"', block)
            body = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
            when = re.search(r'<time datetime="([^"]+)"', block)
            if not (post and body):
                continue
            key = f"tg:{post.group(1).lower()}"
            if key in seen or key in got:
                continue
            try:
                if when and dt.datetime.fromisoformat(when.group(1)) < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                pass
            text = strip_tags(body.group(1))
            lines = [l.strip(" #*•-") for l in text.split("\n") if l.strip(" #*•-")]
            if not lines or re.search(r"резюме|resume|\bcv\b|ищу работу|обзор за|вакансии за|лучшие .* за", lines[0], re.I):
                continue  # резюме кандидатов и дайджесты — не вакансии
            # первая строка часто из хэштегов («senior #удаленка») — берём строку, похожую на должность
            title = next((l for l in lines[:6] if DEV_TITLE.search(l) and not l.startswith("#")), lines[0])[:120]
            got.add(key)
            out.append({
                "key": key, "source": f"t.me/{ch}", "title": title, "company": "",
                "salary": "", "url": f"https://t.me/{post.group(1)}", "remote": None, "text": text,
            })
    return out


def src_linkedin(seen):
    got = {}
    for q in LINKEDIN_QUERIES:
        page = fetch("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", {
            "keywords": q, "f_WT": 2, "geoId": 92000000, "f_TPR": f"r{LOOKBACK_DAYS * 86400}", "start": 0,
        })
        for card in page.split("<li>")[1:]:
            g = lambda p: html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if (m := re.search(p, card, re.S)) else ""
            url = g(r'href="(https://[^"?]+/jobs/view/[^"?]+)')
            jid = re.search(r"(\d{6,})/?$", url)
            if jid:
                got[jid.group(1)] = {
                    "title": g(r'base-search-card__title">(.*?)<'),
                    "company": g(r'base-search-card__subtitle">.*?>(.*?)<'),
                    "location": g(r'job-search-card__location">(.*?)<'),
                    "url": url,
                }
        time.sleep(2)
    out = []
    for jid, v in got.items():
        key = f"li:{jid}"
        if key in seen:
            continue
        text = v["location"]
        if not BAD_TITLE.search(v["title"]) and DEV_TITLE.search(v["title"]):
            try:  # описание — только для потенциально подходящих
                text += "\n" + strip_tags(fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"))[:6000]
                time.sleep(1.5)
            except Exception:  # noqa: BLE001
                pass
        out.append({
            "key": key, "source": "LinkedIn", "title": v["title"], "company": v["company"],
            "salary": "", "url": v["url"], "remote": True, "text": text,
        })
    return out


def tg_user_client():
    """Telethon-клиент от аккаунта пользователя или None, если не настроен."""
    load_env()
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not (api_id and api_hash):
        return None
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("[tg-private] telethon не установлен: py -m pip install telethon", file=sys.stderr)
        return None
    return TelegramClient(str(TG_SESSION), int(api_id), api_hash)


def src_tg_private(seen):
    client = tg_user_client()
    if client is None or not TG_SESSION.with_suffix(".session").exists():
        return []
    out = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)
    client.connect()
    try:
        if not client.is_user_authorized():
            raise RuntimeError("сессия не авторизована — запустите с --tg-login")
        for chat in TG_PRIVATE:
            for msg in client.iter_messages(chat, limit=300):
                if msg.date < cutoff:
                    break
                text = (msg.message or "").strip()
                key = f"tg:{chat}/{msg.id}"
                if len(text) < 120 or key in seen:  # короткие реплики в чате — не вакансии
                    continue
                lines = [l.strip(" #*•-") for l in text.split("\n") if l.strip(" #*•-")]
                out.append({
                    "key": key, "source": f"чат {chat}", "title": lines[0][:120] if lines else "",
                    "company": "", "salary": "", "url": f"https://t.me/{chat}/{msg.id}",
                    "remote": None, "text": text,
                })
    finally:
        client.disconnect()
    return out


SOURCES = [("hh", src_hh), ("habr", src_habr), ("hireseeker", src_hireseeker),
           ("telegram", src_telegram), ("linkedin", src_linkedin), ("tg-private", src_tg_private)]

# ---------------------------------------------------------------- state / telegram


def load_seen():
    try:
        seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        seen = {}
    horizon = time.time() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in seen.items() if v >= horizon}


def save_seen(seen):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_env():
    paths = [os.environ.get("JOBSCOUT_ENV"), str(BASE / ".env")]
    for p in filter(None, paths):
        try:
            for line in Path(p).read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)$", line)
                if m and m.group(1) not in os.environ:
                    os.environ[m.group(1)] = m.group(2).strip().strip('"\'')
        except OSError:
            continue
    token = os.environ.get("TG_TOKEN", "")
    chat = os.environ.get("TG_CHAT") or os.environ.get("TG_CHATID", "")
    return token, chat


def tg_send(token, chat, text):
    fetch(f"https://api.telegram.org/bot{token}/sendMessage", data={
        "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
    })


def render(items, errors, total):
    e = lambda s: html.escape(s or "", quote=False)
    today = dt.datetime.now().strftime("%d.%m")
    head = f"🔎 <b>Вакансии {today}</b> — {len(items)} подходящих из {total} новых"
    blocks = [head]
    for i, it in enumerate(items, 1):
        line2 = " · ".join(filter(None, [it["salary"] and "💰 " + e(it["salary"]), e(it["source"]), f"⭐ {it['score']}"]))
        why = ", ".join(w for w in it["why"][:6])
        blocks.append(
            f"<b>{i}. {e(it['title'])}</b>" + (f" — {e(it['company'])}" if it["company"] else "") +
            f"\n{line2}" + (f"\n<i>{e(why)}</i>" if why else "") +
            f"\n<a href=\"{html.escape(it['url'])}\">Открыть</a>"
        )
    if not items:
        blocks.append("Сегодня новых подходящих вакансий нет.")
    if errors:
        blocks.append("⚠️ Не ответили: " + e(", ".join(errors)))
    # Telegram: максимум 4096 символов на сообщение
    msgs, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 2 > 3900:
            msgs.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + b
    msgs.append(cur)
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="не отправлять и не запоминать")
    ap.add_argument("--log", action="store_true", help="писать вывод в log.txt (для запуска без консоли)")
    args = ap.parse_args()
    if args.log or sys.stderr is None:  # pythonw: консоли нет, stdout/stderr = None
        sys.stdout = sys.stderr = open(BASE / "log.txt", "a", encoding="utf-8", buffering=1)
        print(f"--- {dt.datetime.now():%Y-%m-%d %H:%M}", file=sys.stderr)

    seen = load_seen()
    found, errors = [], []
    for name, fn in SOURCES:
        try:
            got = fn(seen)
            print(f"[{name}] новых: {len(got)}", file=sys.stderr)
            found += got
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] ошибка: {e}", file=sys.stderr)
            errors.append(name)

    # дубли между источниками (hireseeker ссылается на hh)
    uniq = {}
    for it in found:
        if it["key"] not in uniq or (it["source"] == "hh"):
            uniq[it["key"]] = it
    items = list(uniq.values())
    for it in items:
        it["score"], it["why"] = score(it["title"], it["text"], it["remote"])
        if salary_too_low(it["salary"]):
            it["score"] = -100
    # LinkedIn: почти всё — для резидентов США/Европы/LatAm. Оставляем только явно «из любой страны».
    for it in items:
        if it["source"] != "LinkedIn":
            continue
        location = it["text"].split("\n", 1)[0]
        anywhere = re.search(r"worldwide|anywhere|any (country|location)|global(ly)? remote|"
                             r"\b(russia|cis|georgia|armenia|kazakhstan|serbia|emea)\b", location, re.I) or \
            re.search(r"work from anywhere|remote[- ]worldwide|any time ?zone|from any country|fully distributed worldwide",
                      it["text"], re.I)
        restricted = re.search(r"authorized to work|work authori[sz]ation|us only|u\.s\. only|must (be based|reside)|"
                               r"\bcdi\b|h/f|on-?site|hybrid|latam|latin america", it["text"] + " " + it["title"], re.I)
        if not anywhere or restricted:
            it["score"] = -100
        else:
            it["score"] -= 2  # английский, конкуренция — чуть ниже российских
    good, same, li = [], set(), 0
    for it in sorted((i for i in items if i["score"] >= MIN_SCORE), key=lambda i: -i["score"]):
        if it["source"] == "LinkedIn":
            if li >= 5:
                continue
            li += 1
        sig = (it["title"].lower().strip(), it["company"].lower().strip())
        if sig in same:  # одна вакансия, размещённая в нескольких городах
            continue
        same.add(sig)
        good.append(it)
    good = good[:MAX_ITEMS]
    msgs = render(good, errors, len(items))

    if args.dry_run:
        for it in sorted(items, key=lambda i: -i["score"])[:40]:
            print(f"{it['score']:>5}  {it['source']:<22} {it['title'][:70]}  | {', '.join(it['why'][:5])}")
        print("\n" + "\n---\n".join(msgs))
        return

    token, chat = load_env()
    if not token or not chat:
        sys.exit("Нет TG_TOKEN / TG_CHAT: положите их в .env рядом со скриптом.")
    for m in msgs:
        tg_send(token, chat, m)
    now = time.time()
    for it in items:
        seen[it["key"]] = now
    save_seen(seen)


if __name__ == "__main__":
    main()
