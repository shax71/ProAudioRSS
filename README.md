# Pro Audio Daily

Pulls RSS from six pro-audio sites, merges stories covered by more than one site into a single card ("Also covered by …"), and writes `dashboard.html`.

```bash
python3 build.py --open        # build and open (default: last 7 days)
```

- Feeds live in `FEEDS` at the top of `build.py`; layout lives in `template.html`.
- Duplicates: same URL, near-identical titles, or two shared rare title words (usually brand + product) within 4 days.
- The page has 24h / 48h / Week ranges, per-source toggles, and keyword filter.
- Triage: stories you open, tick (✓), or clear with **Mark all as seen** are hidden on later visits, so the page shows only what's new. **Show seen** brings them back; **Undo** reverses the last mark-all.
- Each card is tagged with the companies in its title (Behringer, Native Instruments, …) plus DAWs (Logic Pro, Pro Tools, …). Known brands are listed in `COMPANIES` / `DAWS` in `build.py`; unlisted developers are picked up from headlines like "Pulsar Audio releases …". Click a tag to mute it; muted tags and keywords stay hidden in every future build. Unmute from the "Muted" bar. Mutes are saved in the browser, so they're per-browser.

## Daily refresh

Published at https://shax71.github.io/ProAudioRSS/ by `.github/workflows/daily.yml`, which rebuilds every day at 06:00 UTC and on every push to `main`. To rebuild now: `gh workflow run daily.yml`.

Mutes and seen stories are stored in each browser, so they don't sync between devices.
