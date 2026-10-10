# Local Event Scout

A Home Assistant integration that searches the web each night for local events matching your interests, scores them, and shows the best ones on a sidebar page and in a calendar.

It works with either of two AI providers, chosen when you add the integration:

- **[OpenRouter](https://openrouter.ai)**: any model, pay per use. The default model is `deepseek/deepseek-v4.1-flash`, and web search is OpenRouter's search tool.
- **Google Gemini through [AI Studio](https://aistudio.google.com) keys**: searches use Google Search grounding, and most runs cost nothing (see Cost).

## What it does

- **Distance groups.** Each interest belongs to a group: Nearby 15 km, Local 50 km, Day trip 150 km, or Worth travelling 400 km. Each group has its own look-ahead window and search frequency, so far-away festivals show up months ahead while trivia nights are checked daily.
- **Real distances.** Venues are located with OpenStreetMap, and events outside their group's radius are dropped.
- **Scoring.** Every event gets a 0–10 match score with a one-line reason. Picks are events at or above your minimum score.
- **Interest priority.** Each interest is High, Normal or Low priority. High interests come first in searches and get +1 on their score; Low interests get −1.
- **Saved sources.** When a search finds a good event on a venue's or organiser's events page, that page is saved. Later runs read saved pages directly, with no web-search fee. Once a distance group has two working sources, its full web search runs only every few days (weekly by default). Sources that keep coming up empty are paused. You can add, pause and remove sources on the settings page; 👍 on an event also saves its page.
- **Feedback.** 👍 and 👎 on an event teach future searches. Hiding an event only removes it from view.
- **Report wrong info.** The flag on an event lets you say what's wrong: the town or venue, the date, a cancelled event, the link, or anything else, with the right details if you know them. A corrected town is looked up on the map again, and the event is hidden if it's now outside its distance group. Corrections stick when later searches find the same event again, a cancelled event is never re-added, and recent corrections are passed to the AI so it doesn't repeat the mistake. A source Event Scout saved by itself is paused once 3 of its events have been reported. **Undo** on the event puts the original details back.
- **Weather.** Outdoor events on days with a bad forecast get a warning and a lower score. This uses your Home Assistant weather entity.
- **Clean results.** Today's date goes into every search. Past events, duplicates, and events with broken links are removed.
- **Retries.** When the AI service is overloaded or rate-limited, each call is retried twice (after about 15 and 45 seconds, or whenever the service asks). Garbled answers are asked for once more. If a part of the run still fails, it is tried again 30 minutes later (up to 3 times a day); groups whose search failed stay due, and events left unscored are scored on the next run.
- **Gemini search check.** Searches go through Google's Interactions API first and fall back to the older generateContent API; whichever actually searches with your key is remembered. If Gemini answers without running a Google search, it is asked once more; if it still doesn't, the answer is discarded instead of risking made-up events. **Test web search** in Search options runs one small search and shows exactly what happened.
- **Results add up.** New searches add to the list and never replace it. An event is removed only a day after it ends; hidden events stay in the Hidden tab until then.
- **Spending.** The settings page shows what each call in the last run cost (web searches, input, output and thinking tokens). With OpenRouter, your key's monthly usage and limit appear on the page and as a sensor, and scans are skipped when the limit is nearly used up. With Gemini, the page shows the estimated cost and how many of the 5,000 free monthly Google searches you've used.

### Entities

| Entity | What it shows |
|---|---|
| `sensor.event_scout_status` | `ok`, `scanning`, `error`, `budget` or `never`, plus details of the last search |
| `sensor.event_scout_upcoming_picks` | Number of picks, with the top five as attributes |
| `sensor.event_scout_openrouter_spend_this_month` | AI spend this month in USD: OpenRouter's real figure for the key, or Event Scout's estimate for Gemini |
| `sensor.event_scout_web_searches_this_month` | Web searches made this month |
| `sensor.event_scout_last_search_cost` | Cost of the most recent search |
| `calendar.event_scout_picks` | Picks as calendar events |

Action: `local_event_scout.scan_now`. With `force: false` it only searches groups that are due.

## Install

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/TheDevCache/HA-LocalEventSearch` as an **Integration**.
2. Install **Local Event Scout**, then restart Home Assistant.

### Manual

Copy `custom_components/local_event_scout` into your Home Assistant `config/custom_components/` folder and restart.

## Set up

1. Get keys for one provider:
   - **OpenRouter:** create an API key and **set a monthly limit on it**.
   - **Gemini:** at [aistudio.google.com](https://aistudio.google.com), create a key in a project **with billing turned on** (needed for Google Search; it includes 5,000 free searches a month). Optionally create a second key in a free-tier project: it's used for everything except searching, at no cost.
2. **Settings → Devices & services → Add integration → Local Event Scout**, pick the provider and paste the key(s).
3. Open **Event Scout** in the sidebar. On the **Settings** tab, add your location and interests, then save.
4. Press **Search now**, or wait for the nightly search (02:30 by default).

To switch provider or change keys later: **Settings → Devices & services → Local Event Scout → ⋮ → Reconfigure**.

## Cost

- **OpenRouter:** each group's search may make up to 2 web searches (about half a cent each, plus reading time). Change this with **Web searches per group** in Search options; **Interests per search** (default 3) sets how many interests share each search, with the rest taking turns on later runs. Expect roughly 1–2¢ per call with DeepSeek V4.1 Flash, plus a fraction of a cent for scoring and for reading saved sources. Saved sources cut the number of web searches over time.
- **Gemini:** web searches are free up to 5,000 a month on the billing project; you pay only for the tokens of those calls (well under 1¢ each with `gemini-3.1-flash-lite`). Everything else is free on the free-tier key.

The settings page shows the exact cost of every call in the last run.

## Developing

The search logic in `custom_components/local_event_scout/scout/` has no Home Assistant imports, so you can run and tune it on any PC:

```bash
pip install aiohttp pytest pytest-asyncio
export OPENROUTER_API_KEY=sk-or-...          # PowerShell: $env:OPENROUTER_API_KEY="sk-or-..."
python scripts/scout.py --city Portland --region Oregon --country USA \
    --interest "trivia nights:nearby:high" --interest "indie concerts:local" --dislike "kids events"
python scripts/scout.py --help        # Gemini, saved sources, JSON output
pytest
```

Prompts are in `scout/prompts.py`. The Home Assistant side (storage, scheduling, sensors, calendar, sidebar page) lives in the rest of the integration folder.

## Contact

Bugs and ideas: [open an issue](https://github.com/TheDevCache/HA-LocalEventSearch/issues) or email TheDevCache@gmail.com.
