# JobScout

**A daily, AI-first job radar delivered to Telegram.** JobScout scans job boards and Telegram channels,
scores every vacancy against a developer profile and sends only the best new matches to a Telegram bot.

Built end to end with **Claude Code** using a spec → implement → review → test workflow:
every source parser, the scoring model and the filters were designed as specs, generated with the agent,
then reviewed and tuned against real data.

*Русская версия — ниже.*

## What it does

```
hh.ru ─┐
Habr Career ─┤                    ┌─ dedupe (cross-source, seen.json)
hireseeker ──┼─► collect ─► enrich ─► score ─► filter ─► Telegram digest (11:00 daily)
LinkedIn ────┤   (HTML/JSON)  (full text,   (keywords,   (remote only,
Telegram ────┘                 salary,       title,       salary floor,
 (23 public channels            work format)  stack)       no junior / office)
  + in-channel search)
```

- **Sources:** hh.ru search pages + vacancy pages (embedded JSON state), Habr Career (SSR state),
  hireseeker.ru, LinkedIn public guest search, 23 public Telegram channels via `t.me/s/<channel>`
  including **in-channel keyword search** (`?q=Claude`, `?q=Cursor`, …), optional private chats via Telethon.
- **Enrichment:** for short cards the vacancy page is fetched to get full description, key skills,
  salary and work format.
- **Scoring:** weighted signals for AI tooling (vibe coding, Claude Code, Cursor, AI-first/native),
  stack (React, TypeScript, Next.js, …) and the role in the title; penalties for backend-only titles.
- **Hard filters:** office/hybrid-only, salary ceiling below the floor (with USD/EUR/KZT conversion),
  junior/intern/assistant roles, non-dev roles, résumé and digest posts in channels, LinkedIn jobs
  restricted to US/EU/LatAm residents.
- **Delivery:** HTML-formatted Telegram message, split into ≤4096-char chunks; sources that failed
  are listed at the bottom so silent breakage is visible.
- **Zero dependencies:** Python 3.8+ standard library only (Telethon is optional).

## Quick start

```sh
cp .env.example .env          # put TG_TOKEN and TG_CHAT there
python3 jobscout.py --dry-run # print the digest, send nothing, remember nothing
python3 jobscout.py           # send to Telegram and remember what was sent
```

Schedule it:
- **Linux server:** `sudo sh install.sh` (cron, 11:00 Moscow time).
- **Windows:** Task Scheduler → run `pyw.exe jobscout.py --log` daily (windowless, logs to `log.txt`).

## Tuning

Everything is at the top of `jobscout.py`: search queries (`HH_QUERIES`, `HABR_QUERIES`,
`LINKEDIN_QUERIES`, `TG_QUERIES`), channels (`TG_CHANNELS`, `TG_SEARCH_ONLY`), keyword weights
(`AI_TERMS`, `STACK_TERMS`), `MIN_SCORE`, `MIN_SALARY_RUB`, `MAX_ITEMS`.

## Engineering notes

- hh.ru closed anonymous API search (403), so the scraper reads the `HH-Lux-InitialState`
  JSON embedded in pages instead of brittle HTML selectors.
- The Windows scheduled run was intermittently killed (`0xC000013A`) when the console window was
  closed mid-run; fixed by switching to `pythonw` with a self-managed log.
- State writes are atomic (`seen.tmp` → `seen.json`), entries expire after 60 days.

---

## По-русски

**JobScout** каждый день в 11:00 присылает в Telegram подборку новых вакансий под профиль разработчика:
собирает hh.ru, Habr Career, hireseeker, LinkedIn и 23 Telegram-канала (с поиском внутри каналов),
дочитывает полные описания, оценивает совпадение по AI-инструментам и стеку, отсекает офис, низкие
зарплаты, junior-позиции и вакансии «только для резидентов», убирает дубли.

Сделан целиком в **Claude Code** по схеме «спецификация → реализация → ревью → тесты».

Запуск: скопировать `.env.example` в `.env`, вписать `TG_TOKEN` и `TG_CHAT`,
затем `python3 jobscout.py --dry-run` для проверки и `python3 jobscout.py` для отправки.

## License

MIT
