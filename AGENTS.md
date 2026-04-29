# AGENTS.md

> **Read this first** before making changes. This file is the operational manual for AI coding agents working on this repo.

---

## TL;DR

Streamlit web app that turns a user's Google Maps saved restaurants into a structured AI-tagged itinerary planner. Stack: **Python 3.12 / GPT-4o via OpenRouter / Pydantic / sklearn DBSCAN / folium / Streamlit**. Runs locally, deploys to Streamlit Cloud. Bilingual (zh/en).

---

## Setup commands

```bash
# Activate the dedicated conda env (NOT the user's `da` env)
conda activate lbs                    # Python 3.12

# Install (only if env is missing deps)
pip install -r requirements.txt
playwright install chromium           # Only if running scraper

# Run locally
streamlit run app.py                  # Opens http://localhost:8501

# Verify API key works
python test_api.py
```

**Project root on user's Mac**: `~/Documents/la-vibe-itinerary/`

---

## Code conventions

- **Python style**: Standard PEP 8, no enforced linter. 4-space indents.
- **Type hints**: Use them (`list[dict]`, `str | None`, `Literal[...]`).
- **Strings in user-facing UI**: Use `t("key")` for translations, never hardcode Chinese/English.
- **Field accessors for bilingual data**: Use `field(row, "one_liner")` to read `_zh` or `_en` based on current language.
- **Enum values for filter labels**: Use `fmt_locale(value, ZH_MAP)` for "Chinese / English Title Case".
- **Comments**: Mostly Chinese (project is bilingual but author is native Chinese). Don't translate existing Chinese comments to English unless asked.
- **No new dependencies** without checking `requirements.txt` first. The user's deployment is on Streamlit Cloud free tier — keep deps minimal.

---

## File map (don't move these without good reason)

```
la-vibe-itinerary/
├── app.py                         # Single-file Streamlit app (~1170 lines)
├── requirements.txt               # Locked deps for Streamlit Cloud
├── .env                           # OPENROUTER_API_KEY (never commit!)
├── .gitignore                     # Includes .env, data/cache/, RESUME_FINAL.md, etc.
├── prompts/
│   └── enrich_prompt.txt          # 20-dim GPT-4o prompt template (bilingual fields)
├── scripts/
│   ├── 01_scrape_maps.py          # Playwright (legacy, partially blocked by Google)
│   ├── 01b_parse_takeout.py       # Google Takeout fallback parser
│   ├── 02_process_data.py         # Main pipeline: GPT-4o enrichment with Pydantic + cache
│   └── 03_cluster_routes.py       # DBSCAN clustering + TSP route optimization
├── data/
│   ├── my_places.csv              # Source data (5 cols)
│   ├── my_places_sample.csv       # Backup sample (don't overwrite)
│   ├── enriched_places.csv        # Pipeline output (25+ cols, bilingual)
│   ├── routes.json                # Cluster + route output
│   └── cache/                     # Per-place GPT-4o cache (gitignored)
├── docs/demo_screenshots/         # Static images for README
├── test_api.py                    # OpenRouter connectivity sanity check
├── README.md                      # Public-facing (English-friendly)
├── DEPLOY_GUIDE.md                # User's deploy instructions
├── DEMO_SCRIPT.md                 # 30s/90s/3min interview demo scripts
├── deploy.sh                      # Pre-commit safety check + git init
└── AGENTS.md                      # This file
```

---

## Architecture (3 layers)

```
DATA LAYER → INTELLIGENCE LAYER → DECISION LAYER

1. DATA LAYER (offline pipeline, run when source data changes)
   my_places.csv → 02_process_data.py → enriched_places.csv
   - GPT-4o generates 20 dimensions per place
   - Pydantic strict-validates output (retries on schema fail)
   - Per-place file cache prevents re-billing

2. INTELLIGENCE LAYER (offline + online)
   - Offline: 03_cluster_routes.py → routes.json (DBSCAN + TSP)
   - Online (in app.py): NL Agent calls GPT-4o for soft semantic match

3. DECISION LAYER (Streamlit app.py)
   - Sidebar: filters (pills + sliders)
   - Map (left 60%): folium with status-aware markers
   - Right panel (40%): scrollable cards, top has itinerary if generated
   - Top-right: 🌐 lang toggle + 🔄 reset
```

---

## Pydantic schema (data contract)

`scripts/02_process_data.py::EnrichedPlace` is the source of truth for fields. Bilingual fields come in `_zh` / `_en` pairs. Cuisine is strictly enumerated to 25 standard global cuisines (Italian/French/Japanese/Korean/etc.).

**If you add a new field**: update both the Pydantic schema AND `prompts/enrich_prompt.txt` example.

---

## Bilingual i18n system

- All hardcoded user-facing strings go through `T = {"key": ("中文", "English"), ...}` dict in `app.py`.
- Function `t(key, **kwargs)` reads current language from `st.session_state["lang"]`.
- AI-generated content uses `_zh` / `_en` fields; access via `field(row, "one_liner")`.
- Enum displays (vibe / scenario / cuisine) use `fmt_locale(value, ZH_MAP)`.

**When adding new UI text**: always go through `t()`. Don't hardcode strings.

---

## Streamlit session_state contract

| Key | Purpose | Cleared by |
|---|---|---|
| `lang` | "zh" or "en" | Persistent (don't clear on filter reset) |
| `last_nl_query` | User's last AI query | Reset button, "🗑️ 清除推荐" |
| `highlighted_name` | Map-clicked or 📍-clicked place name | Reset button, new map click |
| `itinerary_clusters` | Generated routes (persists across reruns) | Reset button, new "Generate" click |
| `pills_vibes` / `pills_scenarios` / `pills_cuisines` | Filter selections | Reset button |
| `main_map` | st_folium internal key | Auto-managed |

**Reset button policy**: Clear all session_state EXCEPT `lang` (preserves user's language choice across resets).

---

## Common tasks

### Run the data pipeline (regenerate enriched_places.csv)

```bash
rm -rf data/cache && mkdir -p data/cache    # Clear cache for full refresh
python scripts/02_process_data.py           # Full run (~$0.20-0.30, ~30s)
python scripts/02_process_data.py --limit 5 # Test run on 5 places (~$0.03)
python scripts/03_cluster_routes.py         # Re-cluster (free, instant)
```

### Add a new translation

Add to `T = {...}` dict in `app.py`:
```python
"my_new_key": ("中文文案", "English text"),
```
Use as `t("my_new_key")` or `t("my_new_key", n=count)`.

### Add a new filter

1. Add UI element inside `with st.sidebar:` block (use `st.expander` for pills, `st.slider` for ranges)
2. Add filter logic in the `mask = ...` block (search for "应用筛选")
3. Add label translations to `T` dict

### Update OPENROUTER_API_KEY safely (zsh-compatible)

```bash
printf "Paste new key, then Enter (input hidden): " && \
read -s NEW_KEY && echo "" && \
printf "OPENROUTER_API_KEY=%s\n" "$NEW_KEY" > .env && \
echo "✅ Wrote $(wc -c < .env) chars"
```

**NEVER** `cat .env` or `echo $NEW_KEY` (they expose the key in scrollback). **NEVER** edit `.env` with TextEdit (it adds smart quotes that break parsing).

### Deploy a code change

```bash
git add . && git commit -m "feat: ..." && git push
# Streamlit Cloud auto-redeploys in 1-2 min
```

If you change `requirements.txt`, the cloud rebuild takes ~5 min.

---

## Important constraints

### DO NOT

- ❌ Commit `.env` or any `sk-or-...` API keys (gitignored, but verify)
- ❌ Use `cat .env` for debugging — it dumps the key to scrollback
- ❌ Remove `data/enriched_places.csv` from git — Streamlit Cloud needs it
- ❌ Add `playwright` to runtime imports in `app.py` — Streamlit Cloud can't install browsers
- ❌ Hardcode Chinese/English UI strings — always use `t()`
- ❌ Reference `highlighted` variable before line ~860 in app.py (forward reference); use `st.session_state.get("highlighted_name")` instead
- ❌ Edit `.env` with TextEdit (smart quotes break the file)

### DO

- ✅ Use `st.session_state` for any state that must survive reruns (filters, AI results, itinerary)
- ✅ Validate GPT-4o output with Pydantic (already wired in `02_process_data.py`)
- ✅ Keep the right-panel scrollable container at `LIST_HEIGHT - 50` so the bottom "back to top" button is reachable
- ✅ Test bilingual mode after any UI change (toggle 🌐 in top right)
- ✅ Run `python -m py_compile app.py` after edits to catch syntax errors fast
- ✅ Update `AGENTS.md` (this file) when changing architecture / conventions / file structure

---

## Key design decisions (with rationale)

### Why DBSCAN over K-means?
User-saved places have arbitrary spatial distribution. K-means requires K presetting and force-assigns isolated points to clusters. DBSCAN handles both naturally and identifies outliers (suburb singletons stay singletons).

### Why Pydantic strict enum for `cuisine_primary`?
Earlier runs had GPT-4o drift to "Food Hall" / "California Bakery" (business types, not cuisines). `Literal[25 cuisines]` constraint + tenacity retry forces standardization.

### Why move itinerary detail INTO the right panel?
User pain point: "scroll down to see route, scroll up to see map". Solution: itinerary section is rendered at top of the right scrollable column, so map (left) and itinerary (right) are co-visible at one viewport.

### Why JS-injected dynamic styling instead of CSS `:has()`?
CSS `:has()` selectors targeting Streamlit-rendered DOM are fragile (testid names change between versions). JS finds the bordered container by `getComputedStyle().borderTopWidth >= 1` — version-agnostic.

### Why generic LoremFlickr food images instead of real dish photos?
Real photos require Google Places Photos API (~$7/1000 calls + Cloud setup) or SerpAPI (~$50/mo). LoremFlickr is free, instant, generic-but-appetizing. Each dish dialog has "🖼️ Real Photos via Google" escape hatch.

### Why bilingual via re-running pipeline instead of on-the-fly translation?
On-the-fly translation = $$$ per page view + 5-10s latency. Pre-generating both `_zh` and `_en` is one-time $0.30 cost, free at runtime. Pydantic schema enforces both fields exist.

---

## Known limitations

| Issue | Why | Workaround |
|---|---|---|
| Multiselect "Select all" stays English | Streamlit framework hardcoded | Switched to `st.pills` for filters |
| Dish images are generic stock photos | Real photo APIs cost $$ | "Real Photos via Google" escape link |
| No multi-day trip planning | Out of scope | Listed in README "Future Work" |
| No accessibility (a11y) features | Not prioritized | Listed in README "Future Work" |
| Cuisine_tags are still GPT-generated (low quality) | Not strictly enumerated | Hidden from UI, kept in data |

---

## Streamlit version dependencies

- `st.pills` requires Streamlit ≥ 1.40
- `st.dialog` requires Streamlit ≥ 1.31
- `st.link_button` requires Streamlit ≥ 1.30
- `placeholder` parameter on `st.multiselect` requires Streamlit ≥ 1.27

If user reports rendering glitches, check `streamlit --version` first.

---

## Operational hot paths

### "I changed app.py, why isn't it updating?"
Streamlit auto-detects file changes and prompts for "Rerun". If it doesn't:
- Hard refresh browser (`Cmd+Shift+R`)
- Or kill terminal Streamlit (`Ctrl+C`) and re-run `streamlit run app.py`

### "My API key isn't working"
Order of likely causes:
1. **Account balance is $0** on OpenRouter (Credits page)
2. Key wasn't pasted fully (missed a char)
3. `.env` has smart quotes (TextEdit issue) — use `read -s` method
4. Wrong env activated (`conda activate lbs` vs `da`)

### "Map clicks aren't highlighting cards"
Check `returned_objects=["last_object_clicked_tooltip"]` is set on `st_folium`. Without it, click events return nothing.

### "Itinerary disappears when I click a marker"
Already fixed in latest. The `itinerary_clusters` must be persisted in `st.session_state["itinerary_clusters"]`, not recomputed only when generate button is pressed.

---

## Quick references

- **OpenRouter dashboard**: https://openrouter.ai/keys
- **Streamlit Cloud dashboard**: https://share.streamlit.io
- **GitHub repo**: https://github.com/ihcoaixnaug/la-vibe-itinerary
- **Live demo**: https://la-vibe-itinerary.streamlit.app

---

## Bootstrap prompt for new agent sessions

When starting a fresh AI session:

> "Read `AGENTS.md` first to get project context. The main code is `app.py`. The user's environment is conda env `lbs` at `~/Documents/la-vibe-itinerary/`. Always sync changes from outputs to that path. Don't break the bilingual system or the Pydantic schema. Then I'll describe the new task."
