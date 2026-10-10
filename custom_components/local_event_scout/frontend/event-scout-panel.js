// Event Scout sidebar panel. Plain web component, no build step.
// hass.callApi prefixes "/api/", so paths below are relative to it.

const API = "local_event_scout";
const POLL_MS = 2500;

const esc = (value = "") =>
  String(value ?? "").replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;" })[c]);

function errorText(err) {
  if (!err) return "Unknown error";
  if (typeof err === "string") return err;
  if (err.body) {
    if (typeof err.body === "string") return err.body;
    if (err.body.message) return err.body.message;
  }
  if (err.message) return err.message;
  if (err.error) return err.error;
  try { return JSON.stringify(err); } catch (_) { return String(err); }
}

function parseStart(value) {
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  const date = new Date(value);
  return isNaN(date) ? null : date;
}

function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayLabel(date) {
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  if (dayKey(date) === dayKey(today)) return "Today";
  if (dayKey(date) === dayKey(tomorrow)) return "Tomorrow";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function whenText(event) {
  const start = parseStart(event.start);
  if (!start) return esc(event.start);
  const day = start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  let text = event.all_day ? day : `${day}, ${start.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  if (event.end && event.end.slice(0, 10) !== event.start.slice(0, 10)) {
    const end = parseStart(event.end.slice(0, 10));
    if (end) text += ` – ${end.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  }
  return text;
}

function relative(iso) {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const REPORT_HINTS = {
  location: "Leave the town blank if you don't know where it really is; the event will be hidden.",
  date: "Leave the date blank if you don't know the right one; the event will be hidden.",
  cancelled: "It will be hidden and won't be added again.",
  link: "Leave blank if you don't know the right link.",
  other: "Describe what's wrong. It's passed to future searches.",
};

function shortWhen(value) {
  if (!value) return "";
  return whenText({ start: value, all_day: /^\d{4}-\d{2}-\d{2}$/.test(value) });
}

// "town: Lethbridge → Edmonton" style summary of what a report changed.
function changeText(was = {}, now = {}) {
  const parts = [];
  if ("town" in was || "town" in now) parts.push(`${esc(was.town || "?")} → ${esc(now.town || "?")}`);
  else if ("venue" in was || "venue" in now) parts.push(`${esc(was.venue || "?")} → ${esc(now.venue || "?")}`);
  if ("start" in was || "start" in now) parts.push(`${esc(shortWhen(was.start))} → ${esc(shortWhen(now.start))}`);
  if (now.url) parts.push("new link");
  return parts.join(", ");
}

const ymd = (d) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;

function googleCalendarUrl(e) {
  let dates;
  if (e.all_day) {
    const start = parseStart(e.start.slice(0, 10));
    const last = parseStart((e.end || e.start).slice(0, 10));
    last.setDate(last.getDate() + 1); // Google's end date is exclusive
    dates = `${ymd(start)}/${ymd(last)}`;
  } else {
    const utc = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    const start = new Date(e.start);
    const end = e.end ? new Date(e.end) : new Date(start.getTime() + 2 * 3600e3);
    dates = `${utc(start)}/${utc(end > start ? end : new Date(start.getTime() + 2 * 3600e3))}`;
  }
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: e.title,
    dates,
    details: [e.summary, e.url].filter(Boolean).join("\n\n"),
    location: [e.venue, e.address || e.town].filter(Boolean).join(", "),
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

// Copy text; falls back to selecting an input when the clipboard API is unavailable (plain http).
async function copyText(text, input) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    if (!input) return false;
    input.focus();
    input.select();
    try { return document.execCommand("copy"); } catch (_) { return false; }
  }
}

// Interest ideas for the setup guide: [category, [[name, distance group], ...]].
const SUGGESTIONS = [
  ["Music & nightlife", [["Live music", "local"], ["Indie concerts", "local"], ["Jazz and blues", "local"], ["Classical and symphony", "local"],
    ["Country music", "local"], ["Big-name concerts", "daytrip"], ["DJ nights and dance parties", "local"], ["Open mic nights", "nearby"], ["Karaoke", "nearby"]]],
  ["Arts & culture", [["Theatre", "local"], ["Comedy shows", "local"], ["Art exhibitions and gallery openings", "local"], ["Film screenings", "local"],
    ["Museum events", "local"], ["Author talks and book readings", "local"], ["Dance performances", "local"]]],
  ["Food & drink", [["Food festivals", "daytrip"], ["Farmers markets", "local"], ["Craft beer events", "local"], ["Wine tastings", "local"],
    ["Cooking classes", "local"], ["Food truck events", "local"]]],
  ["Festivals & fairs", [["Festivals", "daytrip"], ["Cultural festivals", "daytrip"], ["Craft fairs and makers markets", "local"],
    ["Rodeos and agricultural fairs", "daytrip"], ["Holiday markets", "local"], ["Fireworks", "local"]]],
  ["Outdoors & active", [["Hiking groups", "daytrip"], ["Running races", "local"], ["Cycling events", "local"], ["Stargazing", "daytrip"],
    ["Hot air balloon festivals", "travel"], ["Paragliding", "travel"], ["Birdwatching", "daytrip"], ["Fishing derbies", "daytrip"]]],
  ["Games & fandom", [["Trivia nights", "nearby"], ["Board game nights", "local"], ["Tabletop RPG nights", "local"], ["Video game tournaments", "local"],
    ["Comic and cosplay conventions", "daytrip"], ["Anime conventions", "travel"], ["Game development meetups", "local"]]],
  ["Tech & learning", [["Tech meetups", "local"], ["Hackathons", "daytrip"], ["Workshops and classes", "local"], ["Science talks", "local"], ["Maker events", "local"]]],
  ["Social", [["Singles events", "local"], ["Speed dating", "local"], ["Meetup groups", "local"], ["Volunteering", "local"], ["LGBTQ+ events", "local"], ["Dance classes", "local"]]],
  ["Sports", [["Hockey games", "local"], ["Football games", "local"], ["Basketball games", "local"], ["Baseball games", "local"], ["Soccer games", "local"], ["Motorsport", "daytrip"]]],
  ["Family & wellness", [["Family events", "local"], ["Kids' activities", "nearby"], ["Yoga classes", "nearby"], ["Meditation and wellness", "local"]]],
];

const WIZARD_STEPS = ["Location", "Interests", "AI check", "Event calendars", "Done"];

const money = (value) => (typeof value === "number" ? `$${value.toFixed(2)}` : "–");

class LocalEventScoutPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "events";
    this._filter = "picks";
    this._sort = "score";
    this._dirty = false;
    this._error = "";
    this._notice = "";
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) this._load();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this.toggleAttribute("narrow", !!narrow);
    if (this._menu) {
      this._menu.narrow = narrow;
      this._menu.style.display = narrow ? "" : "none";
    }
  }

  set panel(_panel) {}

  disconnectedCallback() {
    clearInterval(this._poll);
    this._poll = null;
  }

  connectedCallback() {
    if (this._state && this._state.scanning) this._startPolling();
  }

  async _api(method, path, body) {
    try {
      return await this._hass.callApi(method, `${API}/${path}`, body);
    } catch (err) {
      throw new Error(errorText(err));
    }
  }

  async _load() {
    this._renderShell();
    try {
      this._setState(await this._api("GET", "config"), true);
    } catch (err) {
      this._error = `Could not load Event Scout: ${err.message}`;
      this._renderHeader();
    }
  }

  _setState(state, resetDraft = false) {
    if (!resetDraft && this._dirty && this._tab === "settings" && this.shadowRoot.querySelector(".settings")) {
      this._draft = this._collect();
    }
    this._state = state;
    if (resetDraft || !this._dirty) {
      this._draft = JSON.parse(JSON.stringify(state.config));
      this._dirty = false;
    }
    this._renderHeader();
    this._renderBody();
    if (state.scanning) this._startPolling();
  }

  _startPolling() {
    if (this._poll) return;
    this._poll = setInterval(async () => {
      try {
        const state = await this._api("GET", "config");
        const wasScanning = this._state && this._state.scanning;
        this._state = state;
        this._renderHeader();
        if (this._tab === "events" && (!state.scanning || !wasScanning)) this._renderBody();
        if (!state.scanning) {
          clearInterval(this._poll);
          this._poll = null;
          this._renderBody();
        }
      } catch (_) {
        /* keep polling; HA may be restarting */
      }
    }, POLL_MS);
  }

  // ----------------------------------------------------------------- shell

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="toolbar"><span id="menu-slot"></span><div class="title">Event Scout</div></div>
      <main>
        <header>
          <div class="head-text">
            <h1>Event Scout</h1>
            <p id="status" class="muted"></p>
          </div>
          <div class="head-actions">
            <span id="spend" class="chip" title="OpenRouter usage this month for this API key"></span>
            <button id="scan" class="primary"></button>
          </div>
        </header>
        <div id="banner"></div>
        <nav class="tabs">
          <button data-tab="events">Events</button>
          <button data-tab="settings">Settings</button>
        </nav>
        <section id="body"></section>
      </main>`;
    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    menu.style.display = this._narrow ? "" : "none";
    this.shadowRoot.getElementById("menu-slot").appendChild(menu);
    this._menu = menu;

    this.shadowRoot.getElementById("scan").addEventListener("click", () => this._scan());
    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        this._renderBody();
      })
    );
    const body = this.shadowRoot.getElementById("body");
    body.addEventListener("click", (e) => this._onClick(e));
    body.addEventListener("input", (e) => {
      if (e.target.closest(".settings")) this._markDirty();
    });
    body.addEventListener("change", (e) => {
      if (e.target.dataset.action === "sort") {
        this._sort = e.target.value;
        this._renderBody();
      } else if (e.target.dataset.action === "report-problem") {
        this._showReportFields(e.target.closest("form"));
      } else if (e.target.closest(".settings")) this._markDirty();
    });
  }

  _renderHeader() {
    const root = this.shadowRoot;
    const state = this._state;
    const status = root.getElementById("status");
    const scan = root.getElementById("scan");
    const spend = root.getElementById("spend");
    const banner = root.getElementById("banner");
    if (!status) return;

    let bannerHtml = "";
    if (this._error) bannerHtml += `<p class="banner error">${esc(this._error)}</p>`;
    if (this._notice) bannerHtml += `<p class="banner info">${esc(this._notice)}</p>`;

    if (!state) {
      status.textContent = this._error ? "" : "Loading…";
      scan.textContent = "Search now";
      scan.disabled = true;
      spend.style.display = "none";
      banner.innerHTML = bannerHtml;
      return;
    }
    const last = state.last_run;
    if (state.scanning) {
      status.innerHTML = `<span class="spinner"></span> ${esc(state.progress || "Searching…")}`;
    } else if (last) {
      let text = `Last searched ${relative(last.finished || last.started)}`;
      if (last.note) text += ` · ${last.note}`;
      else if (last.kept !== undefined) text += ` · ${last.new || 0} new, ${money(last.cost)}`;
      if (last.note && last.kept !== undefined) text += ` · ${last.new || 0} new, ${money(last.cost)}`;
      status.textContent = text;
      if (last.error) bannerHtml += `<p class="banner ${last.status === "ok" ? "warn" : "error"}">${esc(last.error)}</p>`;
    } else {
      status.textContent = "No searches yet.";
    }
    scan.disabled = !!state.scanning;
    scan.textContent = state.scanning ? "Searching…" : "Search now";

    const sp = state.spend;
    if (state.provider === "gemini") {
      spend.style.display = "";
      spend.title = "Estimated cost of searches on your billing project, and Google searches used of the monthly free allowance";
      spend.textContent = `~${money(state.month_cost)} · ${state.month_web_searches} of ${Number(state.free_searches).toLocaleString()} free searches`;
    } else if (sp && typeof sp.usage_monthly === "number") {
      spend.style.display = "";
      spend.textContent = typeof sp.limit === "number"
        ? `${money(sp.usage_monthly)} of ${money(sp.limit)} this month`
        : `${money(sp.usage_monthly)} this month`;
    } else {
      spend.style.display = "none";
    }
    banner.innerHTML = bannerHtml;
    root.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === this._tab));
  }

  _renderBody() {
    if (!this._state) return;
    const form = this.shadowRoot.querySelector("form.report");
    if (form) this._reportDraft = Object.fromEntries(new FormData(form));
    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === this._tab));
    const body = this.shadowRoot.getElementById("body");
    const config = this._state.config;
    if (!this._wizard && !this._wizardDismissed && (!config.locations.length || !config.interests.length)) {
      this._wizard = { step: config.locations.length ? 2 : 1 };
    }
    if (this._wizard && this._tab === "events") {
      body.innerHTML = this._wizardHtml();
      return;
    }
    body.innerHTML = this._tab === "settings" ? this._settingsHtml() : this._eventsHtml();
  }

  // ---------------------------------------------------------------- events

  _bucketLabel(id) {
    const bucket = (this._state.config.buckets || []).find((b) => b.id === id);
    return bucket ? bucket.label : id;
  }

  _eventsHtml() {
    const state = this._state;
    const min = state.config.min_score;
    const all = state.events || [];
    const isPick = (e) => !["dislike", "dismiss"].includes(e.status) && (e.score === null || e.score === undefined || e.score >= min);
    const groups = {
      picks: all.filter(isPick),
      all: all.filter((e) => !["dislike", "dismiss"].includes(e.status)),
      liked: all.filter((e) => e.status === "like"),
      hidden: all.filter((e) => ["dislike", "dismiss"].includes(e.status)),
    };
    let list = groups[this._filter] || groups.picks;
    const score = (e) => (typeof e.score === "number" ? e.score : -1);
    list = [...list].sort((a, b) =>
      this._sort === "score" ? score(b) - score(a) || a.start.localeCompare(b.start) : a.start.localeCompare(b.start)
    );

    const chip = (key, label) =>
      `<button class="filter ${this._filter === key ? "active" : ""}" data-action="filter" data-filter="${key}">${label} <span>${groups[key].length}</span></button>`;
    const controls = `
      <div class="controls">
        <div class="filters">${chip("picks", "Top picks")}${chip("all", "All upcoming")}${chip("liked", "Liked")}${chip("hidden", "Hidden")}</div>
        <label class="sort">Sort <select data-action="sort">
          <option value="score" ${this._sort === "score" ? "selected" : ""}>Best match</option>
          <option value="date" ${this._sort === "date" ? "selected" : ""}>Soonest</option>
        </select></label>
      </div>`;

    if (!list.length) {
      const empty = !state.config.locations.length || !state.config.interests.length
        ? "Add a location and some interests in Settings, then select Search now."
        : this._filter === "picks"
          ? "No picks yet. Run a search, or lower the minimum score in Settings."
          : "Nothing here.";
      return `${controls}<p class="empty">${esc(empty)}</p>`;
    }

    if (this._sort === "date") {
      let html = controls;
      let current = "";
      let open = false;
      for (const event of list) {
        const start = parseStart(event.start);
        const key = start ? dayKey(start) : "?";
        if (key !== current) {
          if (open) html += "</div>";
          html += `<h2 class="day">${esc(start ? dayLabel(start) : "Date unknown")}</h2><div class="events">`;
          current = key;
          open = true;
        }
        html += this._card(event, min);
      }
      return html + (open ? "</div>" : "");
    }
    return `${controls}<div class="events">${list.map((e) => this._card(e, min)).join("")}</div>`;
  }

  _card(e, min) {
    const hasScore = typeof e.score === "number";
    const tier = !hasScore ? "none" : e.score >= 8 ? "high" : e.score >= min ? "mid" : "low";
    const distance = typeof e.distance_km === "number"
      ? `${e.distance_source === "estimate" ? "~" : ""}${Math.round(e.distance_km)} km`
      : "";
    const where = [e.venue, e.town, distance].filter(Boolean).map(esc).join(" · ");
    const hidden = ["dislike", "dismiss"].includes(e.status);
    const action = (verdict, icon, title, active) =>
      `<button class="icon ${active ? "on" : ""}" title="${title}" data-action="feedback" data-id="${esc(e.id)}" data-verdict="${active ? "clear" : verdict}">
        <ha-icon icon="${icon}"></ha-icon></button>`;
    return `
      <article class="${hidden ? "faded" : ""}">
        <div class="score ${tier}" title="Match score">${hasScore ? e.score : "?"}</div>
        <div class="content">
          <h3><a href="${esc(e.url)}" target="_blank" rel="noreferrer noopener">${esc(e.title)}</a>
            ${e.status === "new" && e.first_seen && Date.now() - new Date(e.first_seen).getTime() < 26 * 3600e3 ? '<span class="new">New</span>' : ""}</h3>
          <p class="when">${whenText(e)}</p>
          ${where ? `<p class="where">${where}</p>` : ""}
          ${e.why ? `<p class="why">${esc(e.why)}</p>` : ""}
          ${e.weather_note ? `<p class="weather"><ha-icon icon="mdi:weather-cloudy-alert"></ha-icon> ${esc(e.weather_note)}</p>` : ""}
          ${e.summary ? `<p class="summary">${esc(e.summary)}</p>` : ""}
          ${this._reportedHtml(e)}
          ${hidden && e.hidden_reason ? `<p class="muted small">Hidden: ${esc(e.hidden_reason)}</p>` : ""}
          <div class="meta">
            <span class="tag">${esc(this._bucketLabel(e.bucket))}</span>
            ${e.interest ? `<span class="tag">${e.priority === "high" ? "★ " : ""}${esc(e.interest)}</span>` : ""}
            ${e.found_via === "source" ? `<span class="tag" title="${esc(e.source_url)}">Saved source</span>` : ""}
            ${e.outdoor ? '<span class="tag">Outdoor</span>' : ""}
            <span class="actions">
            ${action("like", e.status === "like" ? "mdi:thumb-up" : "mdi:thumb-up-outline", "Interested", e.status === "like")}
            ${action("dislike", e.status === "dislike" ? "mdi:thumb-down" : "mdi:thumb-down-outline", "Not for me (teaches future searches)", e.status === "dislike")}
            ${action("dismiss", e.status === "dismiss" ? "mdi:eye-off" : "mdi:eye-off-outline", "Hide (no effect on future searches)", e.status === "dismiss")}
            <button class="icon ${this._adding === e.id ? "on" : ""}" title="Add to calendar" data-action="add-open" data-id="${esc(e.id)}">
              <ha-icon icon="mdi:calendar-plus"></ha-icon></button>
            <button class="icon ${this._reporting === e.id ? "on" : ""}" title="Report wrong info" data-action="report-open" data-id="${esc(e.id)}">
              <ha-icon icon="${(e.reports || []).length ? "mdi:flag" : "mdi:flag-outline"}"></ha-icon></button>
            </span>
          </div>
          ${this._adding === e.id ? this._addHtml(e) : ""}
          ${this._reporting === e.id ? this._reportFormHtml(e) : ""}
        </div>
      </article>`;
  }

  _feedUrl(suffix = ".ics") {
    return `${window.location.origin}/api/${API}/calendar/${this._state.calendar_token}${suffix}`;
  }

  _addHtml(e) {
    return `<div class="add-cal">
      <a href="${esc(googleCalendarUrl(e))}" target="_blank" rel="noreferrer noopener">Google Calendar</a>
      <a href="${esc(this._feedUrl(`/${encodeURIComponent(e.id)}.ics`))}" download="${esc(e.title.replace(/[^\w .-]+/g, "").slice(0, 60) || "event")}.ics">Download .ics (Outlook, Apple Calendar)</a>
    </div>`;
  }

  _calendarHtml() {
    if (!this._state.calendar_token) return "";
    const row = (id, label, url) => `
      <div class="row feed"><label>${label}<input id="${id}" readonly value="${esc(url)}"></label>
        <button class="secondary small-btn" data-action="copy-feed" data-target="${id}">Copy</button></div>`;
    return `
      <div class="card">
        <h2>Calendar feed</h2>
        <p class="muted">Subscribe to your picks from a calendar app (in Outlook or Apple Calendar: add a calendar from the internet / subscription). It updates every few hours. Google Calendar fetches the feed from Google's servers, so it only works if this address is reachable from the internet, for example through Home Assistant Cloud remote access. Anyone with the link can see the events, so treat it like a password.</p>
        ${row("feed-picks", "Picks and liked events", this._feedUrl())}
        ${row("feed-liked", "Liked events only", this._feedUrl(".ics?only=liked"))}
        <p class="muted small" id="feed-note">${esc(this._feedNote || "")}</p>
        <button class="secondary" data-action="new-feed-link">Make a new link</button>
        <span class="muted small">The old link stops working.</span>
      </div>`;
  }

  _reportedHtml(e) {
    const reports = e.reports || [];
    if (!reports.length) return "";
    const problems = this._state.problems || {};
    const labels = [...new Set(reports.map((r) => problems[r.problem] || r.problem))].join("; ");
    const changes = changeText(e.original || {}, Object.fromEntries(Object.keys(e.original || {}).map((k) => [k, e[k]])));
    const notes = reports.filter((r) => r.note).map((r) => esc(r.note)).join(" · ");
    return `<p class="reported"><ha-icon icon="mdi:flag"></ha-icon>
      <span>Reported: ${esc(labels)}${changes ? ` (${changes})` : ""}${notes ? `<span class="muted"> · ${notes}</span>` : ""}</span>
      <button class="link" data-action="report-undo" data-id="${esc(e.id)}">Undo</button></p>`;
  }

  _reportFormHtml(e) {
    const d = this._reportDraft || {};
    const problem = d.problem || "location";
    const problems = this._state.problems || {};
    const options = Object.entries(problems)
      .map(([k, label]) => `<option value="${k}" ${k === problem ? "selected" : ""}>${esc(label)}</option>`).join("");
    const group = (key, html) => `<div class="rf" data-for="${key}" ${key === problem ? "" : "hidden"}>${html}</div>`;
    return `
      <form class="report" data-id="${esc(e.id)}" onsubmit="return false">
        <label>What's wrong? <select name="problem" data-action="report-problem">${options}</select></label>
        ${group("location", `
          <input name="town" placeholder="Correct town or city${e.town ? ` (listed as ${esc(e.town)})` : ""}" value="${esc(d.town || "")}">
          <input name="venue" placeholder="Venue (optional)" value="${esc(d.venue || "")}">`)}
        ${group("date", `
          <label>Date <input type="date" name="date" value="${esc(d.date || "")}"></label>
          <label>Time <input type="time" name="time" value="${esc(d.time || "")}"></label>
          <label>Last day <input type="date" name="end_date" value="${esc(d.end_date || "")}"></label>`)}
        ${group("link", `<input name="url" type="url" placeholder="https://… correct page" value="${esc(d.url || "")}">`)}
        <textarea name="note" rows="2" placeholder="Note (optional)">${esc(d.note || "")}</textarea>
        <p class="muted small hint" data-hint>${esc(REPORT_HINTS[problem] || "")}</p>
        <div class="form-actions">
          <button type="button" class="primary" data-action="report-submit" data-id="${esc(e.id)}" ${this._reportBusy ? "disabled" : ""}>${this._reportBusy ? "Saving…" : "Send report"}</button>
          <button type="button" class="secondary" data-action="report-cancel">Cancel</button>
        </div>
      </form>`;
  }

  _showReportFields(form) {
    const problem = form.elements.problem.value;
    form.querySelectorAll(".rf").forEach((el) => { el.hidden = el.dataset.for !== problem; });
    form.querySelector("[data-hint]").textContent = REPORT_HINTS[problem] || "";
  }

  async _report(id, body) {
    this._error = "";
    this._notice = "";
    this._reportBusy = true;
    if (!body.undo) this._renderBody();
    try {
      const state = await this._api("POST", "report", { id, ...body });
      this._reporting = null;
      this._reportDraft = null;
      this._reportBusy = false;
      this._notice = state.message || "";
      this._setState(state);
      setTimeout(() => { this._notice = ""; this._renderHeader(); }, 6000);
    } catch (err) {
      this._reportBusy = false;
      this._error = err.message;
      this._renderBody();
    }
    this._renderHeader();
  }

  _submitReport(id) {
    const form = this.shadowRoot.querySelector(`form.report[data-id="${CSS.escape(id)}"]`);
    if (!form) return;
    const d = Object.fromEntries(new FormData(form));
    const fields = { location: ["town", "venue"], date: ["date", "time", "end_date"], link: ["url"] }[d.problem] || [];
    const correction = Object.fromEntries(fields.map((k) => [k, (d[k] || "").trim()]).filter(([, v]) => v));
    if (d.problem === "other" && !(d.note || "").trim()) {
      this._error = "Describe what's wrong in the note.";
      this._renderHeader();
      return;
    }
    this._reportDraft = d;
    this._report(id, { problem: d.problem, correction, note: (d.note || "").trim() });
  }

  _reportsHtml() {
    const reports = [...(this._state.reports || [])].reverse();
    if (!reports.length) return "";
    const problems = this._state.problems || {};
    const rows = reports.map((r) => {
      const change = changeText(r.was, r.now);
      return `<li><b>${esc(r.title)}</b> <span class="muted small">${esc(shortWhen(r.start))}</span><br>
        ${esc(problems[r.problem] || r.problem)}${change ? `: ${change}` : ""}${r.note ? ` · <span class="muted">${esc(r.note)}</span>` : ""}
        <span class="muted small"> · ${esc(relative(r.at))}</span></li>`;
    }).join("");
    return `
      <div class="card">
        <h2>Reported problems</h2>
        <p class="muted">Your corrections stay attached to the event when searches find it again, and recent ones are passed to the AI so it doesn't repeat the mistake. A source that Event Scout saved by itself is paused once 3 of its events have been reported. To undo one, use Undo on the event.</p>
        <ul class="reports">${rows}</ul>
      </div>`;
  }

  // ----------------------------------------------------------- setup guide

  _wizardHtml() {
    const step = this._wizard.step;
    const steps = WIZARD_STEPS.map((label, i) =>
      `<li class="${i + 1 === step ? "current" : i + 1 < step ? "done" : ""}"><span>${i + 1}</span>${label}</li>`).join("");
    const pages = [null, this._wizLocation, this._wizInterests, this._wizCheck, this._wizCalendars, this._wizDone];
    return `
      <div class="wizard card">
        <div class="wiz-head">
          <h2>Set up Event Scout</h2>
          <button class="link" data-action="w-skip">Skip the guide</button>
        </div>
        <ol class="wiz-steps">${steps}</ol>
        ${pages[step].call(this)}
      </div>`;
  }

  _wizNav(next = "Next", { back = true, nextAction = "w-next", disabled = false } = {}) {
    return `<div class="wiz-nav">
      ${back ? '<button class="secondary" data-action="w-back">Back</button>' : "<span></span>"}
      <button class="primary" data-action="${nextAction}" ${disabled ? "disabled" : ""}>${next}</button></div>`;
  }

  _wizLocation() {
    const loc = this._draft.locations[0] || {};
    return `
      <h3>Where are you?</h3>
      <p class="muted">Events are found around this town, and distances are measured from it. You can add more places later in Settings.</p>
      <div class="row">
        <input id="w-city" placeholder="Town or city" value="${esc(loc.city || "")}">
        <input id="w-region" placeholder="Province / state" value="${esc(loc.region || "")}">
        <input id="w-country" placeholder="Country" value="${esc(loc.country || "")}">
      </div>
      <button class="secondary" data-action="w-locate" ${this._locating ? "disabled" : ""}>${this._locating ? "Looking up…" : this._state.host === "homeassistant" ? "Use Home Assistant's home location" : "Use my home location"}</button>
      ${this._wizNav("Next", { back: false })}`;
  }

  _wizInterests() {
    const chosen = new Set(this._draft.interests.map((i) => i.name.toLowerCase()));
    const groups = SUGGESTIONS.map(([cat, items]) => `
      <div class="chip-group"><h4>${esc(cat)}</h4><div class="chips">
        ${items.map(([name, bucket]) => `<button class="chip-btn ${chosen.has(name.toLowerCase()) ? "on" : ""}" data-action="w-chip" data-name="${esc(name)}" data-bucket="${bucket}">${esc(name)}</button>`).join("")}
      </div></div>`).join("");
    const buckets = this._draft.buckets;
    const bucketOptions = (selected) =>
      buckets.map((b) => `<option value="${b.id}" ${b.id === selected ? "selected" : ""}>${esc(b.label)} (${b.km} km)</option>`).join("");
    const priorityOptions = (selected) => [["high", "High priority"], ["normal", "Normal"], ["low", "Low priority"]]
      .map(([v, label]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${label}</option>`).join("");
    const rows = this._draft.interests.map((it, i) => `
      <div class="row w-int" data-index="${i}">
        <span class="w-int-name">${esc(it.name)}</span>
        <select data-f="bucket">${bucketOptions(it.bucket)}</select>
        <select data-f="priority" class="priority">${priorityOptions(it.priority)}</select>
        <button class="icon" title="Remove" data-action="w-remove" data-index="${i}"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>`).join("");
    return `
      <h3>What are you into?</h3>
      <p class="muted">Pick a few ideas or type your own. Specific works best: "bluegrass jam nights" finds more than "music".
        Each interest has a distance group: how far you'd travel for it.</p>
      ${groups}
      <div class="row"><input id="w-custom" placeholder="Something else, e.g. salsa dancing">
        <button class="secondary" data-action="w-add">Add</button></div>
      ${rows ? `<h4>Your interests (${this._draft.interests.length})</h4>${rows}` : ""}
      ${this._wizNav("Next", { disabled: !this._draft.interests.length })}`;
  }

  _wizCheck() {
    const gemini = this._state.provider === "gemini";
    const t = this._testResult;
    const failed = t && !t.working;
    const where = this._state.host === "homeassistant"
      ? "Settings → Devices &amp; services → Local Event Scout → ⋮ → Reconfigure"
      : "Event Scout's provider settings";
    const help = gemini
      ? `Google Search only works with a key from an AI Studio project that has billing turned on (it includes 5,000 free searches a month).
         Create or check keys at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer noopener">aistudio.google.com/apikey</a>.`
      : `Check the key at <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noreferrer noopener">openrouter.ai/settings/keys</a>:
         it needs credit, and a monthly limit above $0.`;
    return `
      <h3>Check the AI connection</h3>
      <p class="muted">You're using ${gemini ? "Google Gemini" : "OpenRouter"}. This runs one small web search near ${esc((this._draft.locations[0] || {}).city || "you")}
        to make sure your key can search the web (${gemini ? "free" : "a fraction of a cent"}).</p>
      <div class="test-search">
        <button class="secondary" data-action="test-search" ${this._testing ? "disabled" : ""}>${this._testing ? "Testing…" : "Test web search"}</button>
        ${this._testHtml()}
      </div>
      ${failed ? `<div class="help"><p>${help}</p><p>To change keys: ${where}.</p></div>` : ""}
      ${this._wizNav(t ? "Next" : "Skip this check")}`;
  }

  _wizCalendars() {
    const buckets = this._draft.buckets;
    const bucketOptions = (selected) =>
      buckets.map((b) => `<option value="${b.id}" ${b.id === selected ? "selected" : ""}>${esc(b.label)} (${b.km} km)</option>`).join("");
    const saved = (this._state.sources || []).length;
    return `
      <h3>Event calendars <span class="muted small">(optional)</span></h3>
      <p class="muted">Saved event pages are read directly on every search, with no web-search fee. They often find more than searching does.</p>
      ${this._discoverHtml(bucketOptions)}
      ${saved ? `<p class="muted small">${saved} source${saved === 1 ? "" : "s"} saved.</p>` : ""}
      ${this._wizNav(this._discovered && this._discovered.some((d) => d.checked) ? "Save selected and continue" : "Next")}`;
  }

  _wizDone() {
    const c = this._state.config;
    const loc = c.locations[0] || {};
    return `
      <h3>All set</h3>
      <ul class="summary-list">
        <li>Searching around <b>${esc([loc.city, loc.region].filter(Boolean).join(", "))}</b></li>
        <li><b>${c.interests.length}</b> interest${c.interests.length === 1 ? "" : "s"}</li>
        <li><b>${(this._state.sources || []).length}</b> saved event page${(this._state.sources || []).length === 1 ? "" : "s"}</li>
        <li>Searches run every night at <b>${esc(c.schedule)}</b>; distance groups and everything else can be changed in Settings.</li>
      </ul>
      <p class="muted">The first search takes a minute or two.</p>
      <div class="wiz-nav">
        <button class="secondary" data-action="w-back">Back</button>
        <span><button class="secondary" data-action="w-close">Finish without searching</button>
        <button class="primary" data-action="w-finish">Search now</button></span>
      </div>`;
  }

  _wizCollect() {
    const root = this.shadowRoot;
    if (root.getElementById("w-city")) {
      const loc = this._draft.locations[0] || {};
      this._draft.locations[0] = {
        ...loc,
        city: root.getElementById("w-city").value.trim(),
        region: root.getElementById("w-region").value.trim(),
        country: root.getElementById("w-country").value.trim(),
      };
    }
    root.querySelectorAll(".w-int").forEach((row) => {
      const it = this._draft.interests[Number(row.dataset.index)];
      if (!it) return;
      it.bucket = row.querySelector('[data-f="bucket"]').value;
      it.priority = row.querySelector('[data-f="priority"]').value;
    });
  }

  async _wizSave() {
    try {
      this._setState(await this._api("POST", "config", this._draft), true);
      return true;
    } catch (err) {
      this._error = err.message;
      this._renderHeader();
      return false;
    }
  }

  async _wizAction(action, target) {
    this._error = "";
    this._wizCollect();
    const w = this._wizard;
    if (action === "w-skip" || action === "w-close") {
      this._wizard = null;
      this._wizardDismissed = true;
      if (action === "w-close") await this._wizSave();
    } else if (action === "w-back") {
      w.step = Math.max(1, w.step - 1);
    } else if (action === "w-next") {
      if (w.step === 1) {
        if (!(this._draft.locations[0] || {}).city) {
          this._error = "Enter your town or city.";
          this._renderHeader();
          return;
        }
        if (!(await this._wizSave())) return;
      } else if (w.step === 2) {
        if (!(await this._wizSave())) return;
      } else if (w.step === 4 && this._discovered && this._discovered.some((d) => d.checked)) {
        if (!(await this._saveDiscovered())) return;
      }
      w.step = Math.min(WIZARD_STEPS.length, w.step + 1);
    } else if (action === "w-locate") {
      this._locating = true;
      this._renderBody();
      try {
        const place = await this._api("GET", "setup/locate");
        this._draft.locations[0] = { ...(this._draft.locations[0] || {}), ...place };
      } catch (err) {
        this._error = err.message;
      }
      this._locating = false;
    } else if (action === "w-chip" || action === "w-add") {
      const name = action === "w-add" ? this.shadowRoot.getElementById("w-custom").value.trim() : target.dataset.name;
      if (!name) return;
      const index = this._draft.interests.findIndex((i) => i.name.toLowerCase() === name.toLowerCase());
      if (index >= 0 && action === "w-chip") this._draft.interests.splice(index, 1);
      else if (index < 0) this._draft.interests.push({ name, bucket: target.dataset.bucket || "local", priority: "normal" });
    } else if (action === "w-remove") {
      this._draft.interests.splice(Number(target.dataset.index), 1);
    } else if (action === "w-finish") {
      this._wizard = null;
      this._wizardDismissed = true;
      this._filter = "picks";
      await this._scan();
      return;
    }
    this._renderHeader();
    this._renderBody();
  }

  // -------------------------------------------------------------- settings

  _settingsHtml() {
    const c = this._draft;
    const s = this._state;
    const bucketOptions = (selected) =>
      c.buckets.map((b) => `<option value="${b.id}" ${b.id === selected ? "selected" : ""}>${esc(b.label)} (${b.km} km)</option>`).join("");
    const location = (l, i) => `
      <div class="row loc" data-index="${i}">
        <input data-f="city" placeholder="City" value="${esc(l.city)}">
        <input data-f="region" placeholder="Province / state" value="${esc(l.region)}">
        <input data-f="country" placeholder="Country" value="${esc(l.country)}">
        <button class="icon" title="Remove" data-action="remove-location" data-index="${i}"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>`;
    const bucket = (b) => `
      <div class="row bucket" data-id="${b.id}">
        <input data-f="label" value="${esc(b.label)}" aria-label="Name">
        <label class="inline"><input data-f="km" type="number" min="1" max="2000" value="${b.km}"> km</label>
        <label class="inline">look <input data-f="lookahead_days" type="number" min="1" max="180" value="${b.lookahead_days}"> days ahead</label>
        <label class="inline">every <input data-f="refresh_days" type="number" min="1" max="30" value="${b.refresh_days}"> days</label>
        <span class="muted small">${(s.next_due || {})[b.id] ? `next search ${esc(s.next_due[b.id])}` : "no interests yet"}</span>
      </div>`;
    const priorityOptions = (selected = "normal") =>
      [["high", "High priority"], ["normal", "Normal"], ["low", "Low priority"]]
        .map(([v, label]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${label}</option>`).join("");
    const interest = (it, i) => `
      <div class="row interest" data-index="${i}">
        <input data-f="name" value="${esc(it.name)}" placeholder="e.g. indie folk concerts">
        <select data-f="bucket">${bucketOptions(it.bucket)}</select>
        <select data-f="priority" class="priority">${priorityOptions(it.priority)}</select>
        <button class="icon" title="Remove" data-action="remove-interest" data-index="${i}"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>`;
    const weatherOptions = [
      `<option value="" ${!c.weather_entity ? "selected" : ""}>Automatic</option>`,
      `<option value="none" ${c.weather_entity === "none" ? "selected" : ""}>Don't use weather</option>`,
      ...(s.weather_entities || []).map((id) => `<option value="${esc(id)}" ${c.weather_entity === id ? "selected" : ""}>${esc(this._hass.states[id]?.attributes.friendly_name || id)}</option>`),
    ].join("");
    const engines = ["auto", "exa", "parallel", "native"]
      .map((e) => `<option value="${e}" ${c.search_engine === e ? "selected" : ""}>${e === "auto" ? "Automatic" : e[0].toUpperCase() + e.slice(1)}</option>`)
      .join("");

    return `
      <div class="settings">
        <div class="card">
          <h2>Locations</h2>
          <p class="muted">Distances are measured from these places. The first one falls back to your Home Assistant home if it can't be found on the map.</p>
          <div class="locations">${c.locations.map(location).join("")}</div>
          <button class="secondary" data-action="add-location">Add location</button>
        </div>

        <div class="card">
          <h2>Distance groups</h2>
          <p class="muted">Each interest belongs to one group. Groups searched less often save money; far-away groups look further ahead so you hear about big events early.</p>
          <div class="buckets">${c.buckets.map(bucket).join("")}</div>
        </div>

        <div class="card">
          <h2>Interests</h2>
          <p class="muted">Specific beats broad: "bluegrass jam nights" works better than "music".</p>
          <div class="interests">${c.interests.map(interest).join("") || '<p class="muted">No interests yet.</p>'}</div>
          <button class="secondary" data-action="add-interest">Add interest</button>
          <details>
            <summary>Add several at once</summary>
            <textarea id="bulk" rows="3" placeholder="One per line or comma-separated"></textarea>
            <div class="row"><select id="bulk-bucket">${bucketOptions("local")}</select>
              <button class="secondary" data-action="bulk-add">Add all</button></div>
          </details>
        </div>

        <div class="card">
          <h2>What to avoid</h2>
          <textarea id="dislikes" rows="2" placeholder="e.g. kids events, networking mixers">${esc((c.dislikes || []).join(", "))}</textarea>
        </div>

        <div class="card">
          <h2>Search options</h2>
          <div class="grid">
            <label>Nightly search <input id="schedule" type="time" value="${esc(c.schedule)}"></label>
            <label>Minimum score for picks <input id="min_score" type="number" min="0" max="10" value="${c.min_score}"></label>
            <label>Max events per search <input id="max_results" type="number" min="1" max="20" value="${c.max_results}"></label>
            <label>Weather <select id="weather_entity">${weatherOptions}</select></label>
            ${s.provider === "gemini" ? `
            <label>Search model (billing project) <input id="gemini_search_model" value="${esc(c.gemini_search_model)}"></label>
            <label>Model for everything else <input id="gemini_model" value="${esc(c.gemini_model)}"></label>` : `
            <label>Model <input id="model" value="${esc(c.model)}"></label>
            <label>Search engine <select id="search_engine">${engines}</select></label>`}
            <label>${s.provider === "gemini" ? "Searches per group" : "Web searches per group"}
              <input id="searches_per_group" type="number" min="1" max="10" value="${c.searches_per_group}">
              <span class="hint">${s.provider === "gemini"
                ? "Each is a separate Google search on different interests. Free up to 5,000 a month; a fraction of a cent in tokens each."
                : "Each costs about half a cent plus reading time. More finds more."}</span></label>
            <label>Interests per search <input id="interests_per_search" type="number" min="1" max="20" value="${c.interests_per_search}">
              <span class="hint">Fewer means deeper searches; the rest take turns on later runs.</span></label>
          </div>
          <label class="check">Once a group has two working saved sources, run its full web search every
            <input id="discovery_days" type="number" min="1" max="60" value="${c.discovery_days}"> days</label>
          <label class="check"><input id="check_links" type="checkbox" ${c.check_links ? "checked" : ""}> Drop events whose links are broken</label>
          <label class="check"><input id="auto_sources" type="checkbox" ${c.auto_sources ? "checked" : ""}> Save good event pages as sources automatically</label>
          <div class="test-search">
            <button class="secondary" data-action="test-search" ${this._testing ? "disabled" : ""}>${this._testing ? "Testing…" : "Test web search"}</button>
            <span class="muted small">Runs one small search to check that your key can really search the web (costs a fraction of a cent).</span>
            ${this._testHtml()}
          </div>
          <p class="muted small">AI provider: ${s.provider === "gemini" ? "Google Gemini" : "OpenRouter"}. To switch provider or change keys: Settings → Devices &amp; services → Local Event Scout → ⋮ → Reconfigure.</p>
        </div>

        ${this._sourcesHtml(bucketOptions)}

        ${this._calendarHtml()}

        ${this._reportsHtml()}

        ${this._usageHtml()}

        ${this._troubleHtml()}

        <div class="card"><h2>Setup guide</h2>
          <p class="muted">Walk through location, interest ideas, the AI check and event calendars again.</p>
          <button class="secondary" data-action="open-guide">Open the setup guide</button></div>

        <div class="save-bar">
          <span class="muted" id="dirty">${this._dirty ? "Unsaved changes" : ""}</span>
          <button class="primary" data-action="save">Save settings</button>
        </div>
      </div>`;
  }

  _sourcesHtml(bucketOptions) {
    const sources = this._state.sources || [];
    const origin = { manual: "Added by you", discovered: "From calendar search", auto: "Found by search", liked: "From an event you liked" };
    const status = (src) => {
      if (!src.enabled) return `<span class="warn-text">Paused${src.paused_reason ? `: ${esc(src.paused_reason)}` : ""}</span>`;
      if (!src.last_checked) return "Not checked yet";
      const when = relative(src.last_checked);
      if (src.last_error) return `<span class="warn-text">Last check ${when}: ${esc(src.last_error)}</span>`;
      return `Last check ${when}: ${src.last_found} event${src.last_found === 1 ? "" : "s"} · ${src.found_total} total`;
    };
    const row = (src) => `
      <div class="source ${src.enabled ? "" : "faded"}">
        <div class="source-main">
          <a href="${esc(src.url)}" target="_blank" rel="noreferrer noopener">${esc(src.name || src.url)}</a>
          <span class="muted small">${esc(this._bucketLabel(src.bucket))} · ${origin[src.origin] || ""}</span>
          <span class="muted small">${status(src)}</span>
        </div>
        <button class="secondary small-btn" data-action="source-toggle" data-url="${esc(src.url)}" data-enabled="${src.enabled}">${src.enabled ? "Pause" : "Resume"}</button>
        <button class="icon" title="Remove" data-action="source-remove" data-url="${esc(src.url)}"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>`;
    return `
      <div class="card">
        <h2>Saved sources</h2>
        <p class="muted">Event pages that are read directly on each run: no web-search fee, just a little model use. Once a group has two working sources, the full web search for it only runs every few days (see Search options). Sources that keep coming up empty are paused automatically. Changes here apply right away.</p>
        <div class="sources">${sources.map(row).join("") || '<p class="muted">None yet. They are added automatically when a search finds a good event on a venue or organiser page, or you can add one below.</p>'}</div>
        ${this._discoverHtml(bucketOptions)}
        <div class="row add-source">
          <input id="source-url" placeholder="https://venue.example/events">
          <select id="source-bucket">${bucketOptions("local")}</select>
          <button class="secondary" data-action="source-add">Add source</button>
        </div>
      </div>`;
  }

  _usageHtml() {
    const last = this._state.last_run;
    if (!last || !last.calls || !last.calls.length) return "";
    const kinds = { search: "Web search", source: "Saved sources", rank: "Scoring" };
    const num = (v) => (typeof v === "number" ? v.toLocaleString() : "–");
    const rows = last.calls.map((c) => `
      <tr><td>${kinds[c.kind] || esc(c.kind)}<div class="muted small">${esc(c.label)}</div></td>
        <td>${num(c.web_searches)}</td><td>${num(c.input_tokens)}</td><td>${num(c.output_tokens)}</td><td>${num(c.reasoning_tokens)}</td>
        <td>${c.free_tier ? "free" : `${c.cost_estimated ? "~" : ""}${typeof c.cost === "number" ? "$" + c.cost.toFixed(4) : "–"}`}</td></tr>`).join("");
    return `
      <div class="card">
        <h2>Last run cost</h2>
        <p class="muted">${esc(relative(last.started))}: ${last.searches || 0} web-search calls, ${last.pages_read || 0} saved pages read, total ${money(last.cost)}. "Thinking" tokens are part of the output tokens.</p>
        <div class="table-wrap"><table class="usage">
          <thead><tr><th>Call</th><th>Searches</th><th>Input tokens</th><th>Output tokens</th><th>Thinking</th><th>Cost</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </div>`;
  }

  _discoverHtml(bucketOptions) {
    const city = (this._state.config.locations[0] || {}).city;
    if (!city) return '<p class="muted small">Add your location to find its event calendars automatically.</p>';
    const cost = this._state.provider === "gemini" ? "one free Google search" : "one web search, about 1–2¢";
    const labels = { good: "Works well", ok: "Readable", weak: "May not work", broken: "Couldn't load" };
    const list = this._discovered || [];
    const rows = list.map((src, i) => `
      <div class="disc ${src.rating}">
        <input type="checkbox" data-disc="${i}" ${src.checked ? "checked" : ""} ${src.rating === "broken" ? "disabled" : ""} aria-label="Save ${esc(src.name)}">
        <div class="disc-main">
          <a href="${esc(src.url)}" target="_blank" rel="noreferrer noopener">${esc(src.name)}</a>
          <span class="badge ${src.rating}">${labels[src.rating] || ""}</span>
          <div class="muted small">${esc([src.covers, src.note].filter(Boolean).join(" · "))}</div>
        </div>
        <select data-disc-bucket="${i}" ${src.rating === "broken" ? "disabled" : ""}>${bucketOptions(src.bucket || "local")}</select>
      </div>`).join("");
    return `
      <div class="discover">
        <button class="secondary" data-action="discover" ${this._discovering ? "disabled" : ""}>
          ${this._discovering ? "Searching…" : `Find event calendars for ${esc(city)}`}</button>
        <span class="muted small">Searches for city, tourism, venue and news event listings (${cost}) and checks which ones Event Scout can read.</span>
        ${this._discoverError ? `<p class="warn-text">${esc(this._discoverError)}</p>` : ""}
        ${this._discovered && !list.length && !this._discoverError ? '<p class="muted">No new calendars found.</p>' : ""}
        ${list.length ? `<div class="disc-list">${rows}</div>
          <button class="primary" data-action="discover-save">Save selected</button>` : ""}
      </div>`;
  }

  async _discover() {
    this._error = "";
    this._discoverError = "";
    this._discovering = true;
    this._discovered = null;
    if (this._dirty) this._draft = this._collect();
    this._renderBody();
    try {
      const found = await this._api("POST", "discover", {});
      this._discoverError = found.error || "";
      this._discovered = (found.sources || []).map((src) => ({ ...src, checked: ["good", "ok"].includes(src.rating), bucket: "local" }));
    } catch (err) {
      this._discoverError = err.message;
    }
    this._discovering = false;
    this._renderBody();
  }

  _collectDiscovered() {
    const list = this._discovered || [];
    this.shadowRoot.querySelectorAll("[data-disc]").forEach((box) => { list[Number(box.dataset.disc)].checked = box.checked; });
    this.shadowRoot.querySelectorAll("[data-disc-bucket]").forEach((sel) => { list[Number(sel.dataset.discBucket)].bucket = sel.value; });
    return list.filter((src) => src.checked && src.rating !== "broken");
  }

  async _saveDiscovered() {
    const chosen = this._collectDiscovered();
    if (!chosen.length) {
      this._discoverError = "Tick at least one page to save.";
      this._renderBody();
      return false;
    }
    try {
      if (this._dirty) this._draft = this._collect();
      this._setState(await this._api("POST", "sources", {
        action: "add_many", sources: chosen.map(({ url, name, bucket }) => ({ url, name, bucket })),
      }));
      this._notice = `Saved ${chosen.length} source${chosen.length === 1 ? "" : "s"}. They're read on every search.`;
      this._discovered = null;
      this._renderBody();
      this._renderHeader();
      return true;
    } catch (err) {
      this._discoverError = err.message;
      this._renderBody();
      return false;
    }
  }

  _troubleHtml() {
    return `
      <div class="card">
        <h2>Troubleshooting</h2>
        <p class="muted">Something not working? Copy the debug info and paste it into a bug report:
          <a href="https://github.com/TheDevCache/HA-LocalEventSearch/issues" target="_blank" rel="noreferrer noopener">open an issue</a>
          or email TheDevCache@gmail.com. It contains your settings (including your towns and interests) and the last few searches, but never your API keys.</p>
        <button class="secondary" data-action="debug" ${this._debugBusy ? "disabled" : ""}>${this._debugBusy ? "Collecting…" : "Copy debug info"}</button>
        ${this._debugText ? `<textarea id="debug-text" rows="10" readonly>${esc(this._debugText)}</textarea>
          <p class="muted small">${esc(this._debugCopied || "")}</p>` : ""}
      </div>`;
  }

  async _debug() {
    this._error = "";
    this._debugBusy = true;
    if (this._dirty) this._draft = this._collect();
    this._renderBody();
    try {
      const info = await this._api("GET", "debug");
      this._debugText = JSON.stringify(info, null, 2);
    } catch (err) {
      this._error = err.message;
      this._renderHeader();
    }
    this._debugBusy = false;
    this._debugCopied = "";
    this._renderBody();
    if (!this._debugText) return;
    const copied = await copyText(this._debugText, this.shadowRoot.getElementById("debug-text"));
    this._debugCopied = copied ? "Copied to the clipboard." : "Couldn't copy automatically: click in the box, press Ctrl+A, then Ctrl+C.";
    const note = this.shadowRoot.querySelector("#debug-text + p");
    if (note) note.textContent = this._debugCopied;
  }

  _testHtml() {
    const t = this._testResult;
    if (!t) return "";
    const names = { interactions: "Interactions API", generate: "generateContent API", openrouter: "OpenRouter web search" };
    const rows = Object.entries(t.results || {}).map(([api, r]) => {
      const verdict = r.error
        ? `<span class="warn-text">failed: ${esc(r.error)}</span>`
        : r.searched
          ? `<span class="ok-text">searched${r.searches ? ` (${r.searches} ${r.searches === 1 ? "query" : "queries"})` : ""}</span>`
          : `<span class="warn-text">answered without searching</span>`;
      return `<li><b>${esc(names[api] || api)}</b>: ${verdict}${r.answer ? `<div class="muted small">${esc(r.answer)}</div>` : ""}</li>`;
    }).join("");
    const summary = t.working
      ? `Web search works${t.provider === "gemini" ? ` using the ${esc(names[t.working])}; Event Scout will use it from now on` : ""}.`
      : "Web search is not working with this key and model.";
    return `<div class="test-result"><p>${summary} <span class="muted small">Model: ${esc(t.model)}</span></p><ul>${rows}</ul></div>`;
  }

  async _testSearch() {
    this._error = "";
    this._testing = true;
    this._testResult = null;
    if (this._dirty) this._draft = this._collect();
    this._renderBody();
    try {
      this._testResult = await this._api("POST", "test_search", {});
    } catch (err) {
      this._error = err.message;
      this._renderHeader();
    }
    this._testing = false;
    this._renderBody();
  }

  async _sourceAction(body) {
    this._error = "";
    try {
      this._setState(await this._api("POST", "sources", body));
    } catch (err) {
      this._error = err.message;
    }
    this._renderHeader();
  }

  _markDirty() {
    this._dirty = true;
    const flag = this.shadowRoot.getElementById("dirty");
    if (flag) flag.textContent = "Unsaved changes";
  }

  _collect() {
    const root = this.shadowRoot;
    const val = (el, f) => el.querySelector(`[data-f="${f}"]`).value;
    const c = { ...this._draft };
    c.locations = [...root.querySelectorAll(".loc")].map((row) => ({
      ...(this._draft.locations[Number(row.dataset.index)] || {}),
      city: val(row, "city").trim(),
      region: val(row, "region").trim(),
      country: val(row, "country").trim(),
    }));
    c.buckets = [...root.querySelectorAll(".bucket")].map((row) => ({
      id: row.dataset.id,
      label: val(row, "label"),
      km: Number(val(row, "km")),
      lookahead_days: Number(val(row, "lookahead_days")),
      refresh_days: Number(val(row, "refresh_days")),
    }));
    c.interests = [...root.querySelectorAll(".interest")].map((row) => ({
      name: val(row, "name").trim(), bucket: val(row, "bucket"), priority: val(row, "priority"),
    }));
    c.dislikes = root.getElementById("dislikes").value.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
    c.schedule = root.getElementById("schedule").value;
    c.min_score = Number(root.getElementById("min_score").value);
    c.max_results = Number(root.getElementById("max_results").value);
    c.weather_entity = root.getElementById("weather_entity").value;
    const opt = (id) => root.getElementById(id);
    if (opt("model")) c.model = opt("model").value.trim();
    if (opt("search_engine")) c.search_engine = opt("search_engine").value;
    if (opt("gemini_model")) c.gemini_model = opt("gemini_model").value.trim();
    if (opt("gemini_search_model")) c.gemini_search_model = opt("gemini_search_model").value.trim();
    c.discovery_days = Number(opt("discovery_days").value);
    c.searches_per_group = Number(opt("searches_per_group").value);
    c.interests_per_search = Number(opt("interests_per_search").value);
    c.check_links = opt("check_links").checked;
    c.auto_sources = opt("auto_sources").checked;
    return c;
  }

  // --------------------------------------------------------------- actions

  async _onClick(e) {
    const target = e.target.closest("[data-action]");
    if (!target || target.tagName === "SELECT") return;
    const action = target.dataset.action;
    if (action.startsWith("w-")) {
      await this._wizAction(action, target);
    } else if (action === "filter") {
      this._filter = target.dataset.filter;
      this._renderBody();
    } else if (action === "feedback") {
      await this._feedback(target.dataset.id, target.dataset.verdict);
    } else if (action === "add-open") {
      this._adding = this._adding === target.dataset.id ? null : target.dataset.id;
      this._renderBody();
    } else if (action === "copy-feed") {
      const input = this.shadowRoot.getElementById(target.dataset.target);
      const ok = await copyText(input.value, input);
      const note = this.shadowRoot.getElementById("feed-note");
      this._feedNote = ok ? "Link copied." : "Couldn't copy automatically: select the link and press Ctrl+C.";
      if (note) note.textContent = this._feedNote;
    } else if (action === "new-feed-link") {
      try {
        this._feedNote = "New link made. Update any calendar that used the old one.";
        this._setState(await this._api("POST", "calendar/new_link", {}));
      } catch (err) {
        this._error = err.message;
        this._renderHeader();
      }
    } else if (action === "report-open") {
      this._reporting = this._reporting === target.dataset.id ? null : target.dataset.id;
      this._reportDraft = null;
      this._renderBody();
      this.shadowRoot.querySelector("form.report input, form.report select")?.focus();
    } else if (action === "report-cancel") {
      this._reporting = null;
      this._reportDraft = null;
      this._renderBody();
    } else if (action === "report-submit") {
      this._submitReport(target.dataset.id);
    } else if (action === "report-undo") {
      await this._report(target.dataset.id, { undo: true });
    } else if (action === "save") {
      await this._save();
    } else if (action === "open-guide") {
      if (this._dirty) this._draft = this._collect();
      this._wizard = { step: 1 };
      this._tab = "events";
      this._renderHeader();
      this._renderBody();
    } else if (action === "discover") {
      await this._discover();
    } else if (action === "discover-save") {
      await this._saveDiscovered();
    } else if (action === "debug") {
      await this._debug();
    } else if (action === "test-search") {
      await this._testSearch();
    } else if (action === "source-add") {
      const url = this.shadowRoot.getElementById("source-url").value.trim();
      const bucket = this.shadowRoot.getElementById("source-bucket").value;
      await this._sourceAction({ action: "add", url, bucket });
    } else if (action === "source-remove") {
      await this._sourceAction({ action: "remove", url: target.dataset.url });
    } else if (action === "source-toggle") {
      await this._sourceAction({ action: "update", url: target.dataset.url, enabled: target.dataset.enabled !== "true" });
    } else if (["add-location", "remove-location", "add-interest", "remove-interest", "bulk-add"].includes(action)) {
      const draft = this._collect();
      const index = Number(target.dataset.index);
      if (action === "add-location") draft.locations.push({ city: "", region: "", country: draft.locations[0]?.country || "" });
      if (action === "remove-location") draft.locations.splice(index, 1);
      if (action === "add-interest") draft.interests.push({ name: "", bucket: "local", priority: "normal" });
      if (action === "remove-interest") draft.interests.splice(index, 1);
      if (action === "bulk-add") {
        const bucket = this.shadowRoot.getElementById("bulk-bucket").value;
        const names = this.shadowRoot.getElementById("bulk").value.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
        const existing = new Set(draft.interests.map((i) => i.name.toLowerCase()));
        names.filter((n) => !existing.has(n.toLowerCase())).forEach((name) => draft.interests.push({ name, bucket, priority: "normal" }));
      }
      this._draft = draft;
      this._dirty = true;
      this._renderBody();
      if (action === "add-interest") this.shadowRoot.querySelector(".interest:last-child input")?.focus();
      if (action === "add-location") this.shadowRoot.querySelector(".loc:last-child input")?.focus();
    }
  }

  async _save() {
    this._error = "";
    this._notice = "";
    try {
      const state = await this._api("POST", "config", this._collect());
      this._setState(state, true);
      this._notice = "Settings saved.";
      setTimeout(() => { this._notice = ""; this._renderHeader(); }, 3000);
    } catch (err) {
      this._error = err.message;
    }
    this._renderHeader();
  }

  async _scan() {
    this._error = "";
    this._notice = "";
    if (this._dirty) {
      this._notice = "You have unsaved settings; the search uses the saved ones.";
    }
    try {
      this._setState(await this._api("POST", "run", { force: true }));
    } catch (err) {
      this._error = err.message;
      this._renderHeader();
    }
  }

  async _feedback(id, verdict) {
    try {
      this._setState(await this._api("POST", "feedback", { id, verdict }));
    } catch (err) {
      this._error = err.message;
      this._renderHeader();
    }
  }
}

const STYLES = `
  :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: none; align-items: center; height: 56px; padding: 0 4px; background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color)); }
  .toolbar .title { font-size: 20px; margin-left: 8px; }
  :host([narrow]) .toolbar { display: flex; }
  :host([narrow]) .head-text h1 { display: none; }
  main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; box-sizing: border-box; }
  header { display: flex; justify-content: space-between; gap: 16px; align-items: center; flex-wrap: wrap; }
  h1 { margin: 0; font-size: 28px; font-weight: 500; }
  h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; }
  h2.day { margin: 24px 0 10px; font-size: 15px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  h3 { margin: 0; font-size: 16px; font-weight: 500; line-height: 1.35; }
  p { margin: 4px 0; line-height: 1.45; }
  .muted { color: var(--secondary-text-color); }
  .small { font-size: 12px; }
  .head-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .chip { padding: 6px 12px; border-radius: 16px; background: var(--secondary-background-color); font-size: 13px; color: var(--secondary-text-color); }
  button { font: inherit; cursor: pointer; border: 0; border-radius: 8px; padding: 9px 16px; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color); }
  button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); margin-top: 8px; }
  button:disabled { opacity: .6; cursor: progress; }
  button.icon { background: transparent; color: var(--secondary-text-color); padding: 6px; border-radius: 50%; line-height: 0; }
  button.icon:hover { background: var(--secondary-background-color); }
  button.icon.on { color: var(--primary-color); }
  .tabs { display: flex; gap: 4px; margin: 18px 0 14px; border-bottom: 1px solid var(--divider-color); }
  .tabs button { background: none; border-radius: 0; color: var(--secondary-text-color); border-bottom: 2px solid transparent; padding: 10px 14px; }
  .tabs button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  .banner { padding: 10px 14px; border-radius: 8px; margin: 12px 0 0; }
  .banner.error { background: var(--error-color); color: #fff; }
  .banner.warn { background: var(--warning-color, #ffa600); color: #000; }
  .banner.info { background: var(--secondary-background-color); }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--secondary-text-color); border-top-color: transparent;
    border-radius: 50%; animation: spin 1s linear infinite; vertical-align: -1px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .controls { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  .filters { display: flex; gap: 6px; flex-wrap: wrap; }
  .filter { background: var(--secondary-background-color); color: var(--primary-text-color); border-radius: 16px; padding: 6px 12px; font-size: 14px; }
  .filter span { color: var(--secondary-text-color); margin-left: 4px; }
  .filter.active { background: var(--primary-color); color: var(--text-primary-color); }
  .filter.active span { color: inherit; opacity: .8; }
  .sort { display: flex; gap: 6px; align-items: center; color: var(--secondary-text-color); font-size: 14px; }
  .events { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
  article, .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); border: var(--ha-card-border-width, 0) solid var(--ha-card-border-color, transparent); }
  article { display: flex; gap: 14px; padding: 16px; }
  article.faded { opacity: .55; }
  .score { flex: 0 0 auto; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; font-weight: 600;
    background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .score.high { background: var(--primary-color); color: var(--text-primary-color); }
  .score.mid { border: 2px solid var(--primary-color); color: var(--primary-color); background: transparent; }
  .content { flex: 1; min-width: 0; }
  .content a { color: var(--primary-text-color); text-decoration: none; }
  .content a:hover { color: var(--primary-color); text-decoration: underline; }
  .new { font-size: 11px; background: var(--accent-color, var(--primary-color)); color: var(--text-primary-color); border-radius: 8px; padding: 1px 6px; margin-left: 6px; vertical-align: 2px; }
  .when { font-weight: 500; color: var(--primary-color); margin-top: 6px; }
  .where { color: var(--secondary-text-color); font-size: 14px; }
  .why { margin-top: 8px; }
  .weather { color: var(--warning-color, #c77700); font-size: 14px; --mdc-icon-size: 16px; }
  .summary { color: var(--secondary-text-color); font-size: 14px; }
  .meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 10px; --mdc-icon-size: 20px; }
  .tag { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .actions { margin-left: auto; display: flex; gap: 2px; }
  .reported { font-size: 13px; color: var(--warning-color, #c77700); --mdc-icon-size: 16px; margin-top: 6px; }
  .reported ha-icon { vertical-align: -3px; margin-right: 2px; }
  .reported span { color: var(--primary-text-color); }
  button.link { background: none; padding: 0 4px; color: var(--primary-color); text-decoration: underline; font-size: 13px; }
  form.report { margin-top: 12px; padding: 12px; border-radius: 8px; background: var(--secondary-background-color); display: flex; flex-direction: column; gap: 8px; }
  form.report label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
  form.report .rf { display: flex; gap: 8px; flex-wrap: wrap; }
  form.report .rf[hidden] { display: none; }
  form.report .rf > input { flex: 1 1 140px; min-width: 0; }
  form.report .rf > label { flex: 1 1 120px; }
  form.report .hint { margin: 0; }
  .form-actions { display: flex; gap: 8px; }
  .form-actions button { margin: 0; }
  .add-cal { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 10px; font-size: 14px; }
  .add-cal a { color: var(--primary-color) !important; text-decoration: underline !important; }
  .feed label { flex: 1 1 320px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
  .feed input { width: 100%; font-family: monospace; font-size: 12px; }
  .feed button { align-self: flex-end; }
  .wizard { padding: 20px 24px; }
  .wiz-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  .wiz-steps { display: flex; flex-wrap: wrap; gap: 6px 18px; list-style: none; padding: 0; margin: 8px 0 18px; font-size: 13px; color: var(--secondary-text-color); }
  .wiz-steps li { display: flex; align-items: center; gap: 6px; }
  .wiz-steps span { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: var(--secondary-background-color); font-size: 12px; }
  .wiz-steps li.current { color: var(--primary-text-color); font-weight: 500; }
  .wiz-steps li.current span, .wiz-steps li.done span { background: var(--primary-color); color: var(--text-primary-color); }
  .wizard h3 { margin: 0 0 6px; font-size: 20px; font-weight: 500; }
  .wizard h4 { margin: 14px 0 6px; font-size: 14px; font-weight: 500; color: var(--secondary-text-color); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip-btn { background: var(--secondary-background-color); color: var(--primary-text-color); border-radius: 16px; padding: 6px 12px; font-size: 14px; }
  .chip-btn.on { background: var(--primary-color); color: var(--text-primary-color); }
  .w-int-name { flex: 2 1 160px; }
  .wiz-nav { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--divider-color); }
  .wiz-nav button { margin: 0; }
  .wiz-nav span { display: flex; gap: 8px; flex-wrap: wrap; }
  .help { margin-top: 12px; padding: 10px 14px; border-radius: 8px; background: var(--secondary-background-color); }
  .help a, .muted a { color: var(--primary-color); }
  .summary-list { padding-left: 20px; line-height: 1.8; }
  .discover { margin: 14px 0 6px; padding-top: 12px; border-top: 1px solid var(--divider-color); }
  .discover > button { margin: 0 10px 0 0; }
  .disc-list { margin: 10px 0; }
  .disc { display: flex; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .disc.broken { opacity: .55; }
  .disc-main { flex: 1; min-width: 0; }
  .disc-main a { color: var(--primary-text-color); }
  .disc select { flex: 0 0 auto; max-width: 180px; }
  .badge { font-size: 11px; border-radius: 8px; padding: 1px 7px; margin-left: 6px; background: var(--secondary-background-color); color: var(--secondary-text-color); white-space: nowrap; }
  .badge.good { background: var(--success-color, #43a047); color: #fff; }
  .badge.weak { background: var(--warning-color, #ffa600); color: #000; }
  .badge.broken { background: var(--error-color, #db4437); color: #fff; }
  #debug-text { margin-top: 10px; font-family: monospace; font-size: 12px; }
  ul.reports { margin: 0; padding-left: 18px; }
  ul.reports li { margin: 8px 0; line-height: 1.4; }
  .empty { padding: 40px 0; text-align: center; color: var(--secondary-text-color); }
  .settings { display: grid; gap: 16px; }
  .card { padding: 18px 20px; }
  .row { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
  .row > input, .row > select { flex: 1 1 140px; }
  .bucket > input[data-f="label"] { flex: 1 1 120px; }
  .inline { display: flex; align-items: center; gap: 6px; color: var(--secondary-text-color); font-size: 14px; }
  .inline input { width: 72px; }
  input, select, textarea { box-sizing: border-box; font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 8px 10px; }
  textarea { width: 100%; resize: vertical; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .grid label { display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: var(--secondary-text-color); }
  .check { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
  details { margin-top: 12px; }
  summary { cursor: pointer; color: var(--primary-color); }
  details textarea { margin-top: 8px; }
  .priority { flex: 0 1 150px !important; }
  .source { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
  .source.faded { opacity: .6; }
  .source-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .source-main a { color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .small-btn { margin: 0; padding: 6px 12px; font-size: 13px; }
  .add-source input { flex: 3 1 240px; }
  .add-source button { margin: 0; }
  .warn-text { color: var(--warning-color, #c77700); }
  .ok-text { color: var(--success-color, #43a047); }
  .test-search { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .test-search button { margin: 0; }
  .test-result { flex-basis: 100%; background: var(--secondary-background-color); border-radius: 8px; padding: 10px 14px; }
  .test-result p { margin: 0 0 6px; }
  .test-result ul { margin: 0; padding-left: 18px; }
  .test-result li { margin: 4px 0; }
  .table-wrap { overflow-x: auto; }
  table.usage { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.usage th, table.usage td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  table.usage th:first-child, table.usage td:first-child { text-align: left; white-space: normal; }
  table.usage th { color: var(--secondary-text-color); font-weight: 500; }
  #discovery_days { width: 72px; }
  .grid label .hint { font-size: 12px; color: var(--secondary-text-color); opacity: .85; }
  .save-bar { position: sticky; bottom: 0; display: flex; justify-content: flex-end; align-items: center; gap: 14px; padding: 12px 0;
    background: linear-gradient(transparent, var(--primary-background-color) 30%); }
  @media (max-width: 600px) {
    main { padding: 16px 12px 40px; }
    .head-text h1 { display: none; }
    .events { grid-template-columns: 1fr; }
  }
`;

customElements.define("local-event-scout-panel", LocalEventScoutPanel);
