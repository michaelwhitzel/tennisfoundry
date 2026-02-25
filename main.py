import os
import requests
import json
import datetime
import time
from urllib.parse import quote_plus
from google import genai
from jinja2 import Environment, FileSystemLoader

# =========================
# CONFIG / SETUP
# =========================
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DEBUG_DUMPS = os.environ.get("DEBUG_DUMPS", "0") == "1"
MAX_MATCHES_PER_TOUR = int(os.environ.get("MAX_MATCHES_PER_TOUR", "2"))  # design mode default

# Design mode optimizations: keep API calls minimal to avoid 429
DESIGN_MODE = os.environ.get("DESIGN_MODE", "1") == "1"

if not RAPID_API_KEY:
    raise RuntimeError("Missing RAPID_API_KEY env var")
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY env var")

client = genai.Client(api_key=GEMINI_API_KEY)

RAPID_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE_URL = f"https://{RAPID_HOST}"

rank_cache = {}
CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, "matches.json")


# =========================
# HELPERS
# =========================
def deep_get(d, path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def normalize_rank(value):
    try:
        r = int(value)
        if 0 < r < 5000:
            return r
    except (TypeError, ValueError):
        return None
    return None


def extract_rank_from_player_or_match(player_data, match_data, key_prefix):
    candidates = [
        player_data.get("ranking"),
        player_data.get("rank"),
        deep_get(player_data, ["ranking", "rank"]),
        deep_get(player_data, ["ranking", "position"]),
        deep_get(player_data, ["rankings", "singles", "rank"]),
        deep_get(player_data, ["rankings", "singles", "position"]),
        deep_get(player_data, ["ranking", "singles", "rank"]),
        deep_get(player_data, ["ranking", "singles", "position"]),
        deep_get(player_data, ["stats", "ranking"]),
        deep_get(player_data, ["stats", "rank"]),
        match_data.get(f"{key_prefix}Rank"),
        match_data.get(f"{key_prefix}_rank"),
        deep_get(match_data, [key_prefix, "ranking"]),
        deep_get(match_data, [key_prefix, "rank"]),
        deep_get(match_data, [key_prefix, "rankings", "singles", "rank"]),
    ]
    for c in candidates:
        r = normalize_rank(c)
        if r is not None:
            return r
    return None


def safe_mkdir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def load_cached_matches():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and "ATP" in payload and "WTA" in payload:
            return payload
    except Exception:
        return None
    return None


def save_cached_matches(matches_dict):
    safe_mkdir(CACHE_DIR)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(matches_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Cache write failed: {e}")


def try_fetch_json_with_backoff(url, headers, params=None, timeout=25, max_retries=5):
    """
    Handles 429 by waiting (Retry-After if present) with exponential backoff.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
        except Exception as e:
            print(f"Request failed: {e} url={url}")
            return None

        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                print(f"JSON parse failed: {e}")
                return None

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = int(retry_after)
                except Exception:
                    wait_s = 2 ** attempt
            else:
                wait_s = min(2 ** attempt, 20)

            print(f"HTTP 429 (rate limited). Waiting {wait_s}s then retrying... url={url}")
            time.sleep(wait_s)
            continue

        # Other errors: log and stop
        print(f"HTTP {r.status_code} for {url} params={params}")
        return None

    print(f"Exceeded retries for {url}")
    return None


def extract_surface(tourn, match_obj):
    surface = (
        tourn.get("surface")
        or deep_get(tourn, ["court", "surface"])
        or deep_get(tourn, ["court", "name"])
        or match_obj.get("surface")
        or deep_get(match_obj, ["court", "surface"])
        or deep_get(match_obj, ["court", "name"])
        or "Unknown"
    )
    if isinstance(surface, dict):
        surface = surface.get("name") or surface.get("surface") or "Unknown"
    if not surface:
        surface = "Unknown"

    s = str(surface).strip()
    if s.lower() in ("hardcourt", "hard court"):
        return "Hard"
    if s.lower() in ("claycourt", "clay court"):
        return "Clay"
    if s.lower() in ("grasscourt", "grass court"):
        return "Grass"
    return s


def avatar_fallback_url(player_name: str) -> str:
    name_q = quote_plus(player_name.strip() if player_name else "Player")
    return f"https://ui-avatars.com/api/?name={name_q}&background=111827&color=E5E7EB&bold=true&size=128&format=png"


def normalize_image_url(raw, player_name: str) -> str:
    url = ""
    if isinstance(raw, dict):
        url = raw.get("url") or raw.get("image") or raw.get("path") or raw.get("photo") or ""
    elif isinstance(raw, str):
        url = raw.strip()

    if not url:
        return avatar_fallback_url(player_name)

    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if url.startswith("/"):
        return avatar_fallback_url(player_name)
    if not (url.startswith("https://") or url.startswith("http://")):
        return avatar_fallback_url(player_name)
    return url


def get_prediction(match):
    p1 = match.get("player1")
    r1 = match.get("p1_rank")
    p2 = match.get("player2")
    r2 = match.get("p2_rank")
    t_name = match.get("tournament")
    surf = match.get("surface")

    prompt = f"""
Act as a professional tennis analyst.
Match: {p1} (Rank: {r1}) vs {p2} (Rank: {r2})
Tournament: {t_name}
Surface: {surf}

Predict the winner.
- Internally apply the Analytic Network Process (ANP) model (weighing tangible criteria like rank/surface and intangible criteria like momentum/fatigue).
- DO NOT mention "ANP" or "Analytic Network Process" in your response.
- Calculate a highly specific and unique confidence integer between 50 and 99. Do not default to 85.
- Keep the reasoning to exactly one short, punchy sentence.

Output ONLY valid JSON with no markdown formatting.
Use exactly these keys:
{{"winner": "Player Name", "confidence": <insert unique integer>, "reasoning": "One short sentence here."}}
""".strip()

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"winner": "TBD", "confidence": 0, "reasoning": "Analysis unavailable"}


# =========================
# FETCH MATCHES
# =========================
def get_matches():
    """
    In DESIGN_MODE:
      - only fetch TODAY (UTC)
      - only page 1
    Outside design mode:
      - fetch yesterday/today/tomorrow + paginate
    If rate-limited / empty: fall back to last cached matches.
    """
    utc_now = datetime.datetime.utcnow()

    if DESIGN_MODE:
        date_list = [utc_now.strftime("%Y-%m-%d")]
        max_pages = 1
    else:
        date_list = [
            (utc_now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            utc_now.strftime("%Y-%m-%d"),
            (utc_now + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
        max_pages = 10

    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    all_matches = {"ATP": {}, "WTA": {}}
    seen_keys = set()

    # In design mode, keep filtering lighter so we reliably get something
    exclusions = ["doubles", "exhibition"] if DESIGN_MODE else [
        "challenger", "itf", "doubles", "exhibition",
        "m15", "m25", "w15", "w35", "w50", "w75", "w100", "utr"
    ]

    def add_match(tour_key, tourney_name, surface, match_obj):
        if tourney_name not in all_matches[tour_key]:
            all_matches[tour_key][tourney_name] = {"surface": surface, "matches": []}

        if (all_matches[tour_key][tourney_name]["surface"] in ("Unknown", "", None)) and (surface not in ("Unknown", "", None)):
            all_matches[tour_key][tourney_name]["surface"] = surface

        all_matches[tour_key][tourney_name]["matches"].append(match_obj)

    dumped_any = False
    total_raw_seen = 0
    total_kept = 0

    for tour in ("atp", "wta"):
        tour_key = tour.upper()

        for day in date_list:
            page = 1
            while page <= max_pages:
                url = f"{BASE_URL}/tennis/v2/{tour}/fixtures/{day}"
                params = {
                    "include": "tournament,tournament.court,player1,player2",
                    "pageSize": 100,
                    "page": page,
                    "pageNumber": page,
                }

                data = try_fetch_json_with_backoff(url, headers=headers, params=params)
                if not data:
                    break

                raw_matches = data.get("data", []) or []
                if not raw_matches:
                    break

                total_raw_seen += len(raw_matches)

                for m in raw_matches:
                    tourn = m.get("tournament", {}) or {}
                    tourney_name = tourn.get("name", f"{tour_key} Match")
                    name_check = tourney_name.lower()

                    if any(x in name_check for x in exclusions):
                        continue

                    p1 = m.get("player1", {}) or {}
                    p2 = m.get("player2", {}) or {}

                    p1_name = p1.get("name", "Player 1")
                    p2_name = p2.get("name", "Player 2")

                    if "/" in p1_name or "/" in p2_name:
                        continue

                    if DEBUG_DUMPS and not dumped_any:
                        try:
                            with open("debug_match.json", "w") as f:
                                json.dump(m, f, indent=2)
                            with open("debug_player1.json", "w") as f:
                                json.dump(p1, f, indent=2)
                            with open("debug_tournament.json", "w") as f:
                                json.dump(tourn, f, indent=2)
                            print("DEBUG: wrote debug_match.json, debug_player1.json, debug_tournament.json")
                            dumped_any = True
                        except Exception as e:
                            print(f"DEBUG dump failed: {e}")

                    match_id = m.get("id") or m.get("fixtureId") or m.get("matchId")
                    dedupe_key = match_id or f"{tourney_name}|{p1_name}|{p2_name}|{day}"
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)

                    # ranks (best-effort; skip extra API calls in DESIGN_MODE)
                    p1_rank = extract_rank_from_player_or_match(p1, m, "player1")
                    p2_rank = extract_rank_from_player_or_match(p2, m, "player2")

                    surface = extract_surface(tourn, m)

                    raw_p1_img = p1.get("image") or p1.get("photo") or p1.get("picture") or deep_get(p1, ["images", "headshot"]) or ""
                    raw_p2_img = p2.get("image") or p2.get("photo") or p2.get("picture") or deep_get(p2, ["images", "headshot"]) or ""

                    p1_image = normalize_image_url(raw_p1_img, p1_name)
                    p2_image = normalize_image_url(raw_p2_img, p2_name)

                    p1_rank_display = p1_rank if p1_rank is not None else "UR"
                    p2_rank_display = p2_rank if p2_rank is not None else "UR"
                    best_rank = min(p1_rank or 9999, p2_rank or 9999)

                    match_obj = {
                        "tournament": tourney_name,
                        "surface": surface,
                        "player1": p1_name,
                        "player2": p2_name,
                        "p1_rank": p1_rank_display,
                        "p2_rank": p2_rank_display,
                        "p1_image": p1_image,
                        "p2_image": p2_image,
                        "p1_avatar": avatar_fallback_url(p1_name),
                        "p2_avatar": avatar_fallback_url(p2_name),
                        "best_rank": best_rank,
                    }

                    add_match(tour_key, tourney_name, surface, match_obj)
                    total_kept += 1

                # In DESIGN_MODE we only do page 1; outside we keep going until < pageSize
                if len(raw_matches) < 100 or DESIGN_MODE:
                    break

                page += 1

    # Sort inside tournaments
    for tour_key in all_matches:
        for tourney_name in all_matches[tour_key]:
            all_matches[tour_key][tourney_name]["matches"].sort(key=lambda x: x.get("best_rank", 9999))

    print(f"[FETCH] raw seen: {total_raw_seen}, kept: {total_kept}, design_mode={DESIGN_MODE}")

    # Cache fallback: if kept==0, use last good cache
    if total_kept == 0:
        cached = load_cached_matches()
        if cached:
            print("[CACHE] Using last-known-good cache due to empty fetch (likely 429).")
            return cached
        else:
            print("[CACHE] No cache available.")
            return all_matches

    # Save successful fetch
    save_cached_matches(all_matches)
    return all_matches


def count_matches(matches_dict):
    total = 0
    for tour in matches_dict:
        for tname in matches_dict[tour]:
            total += len(matches_dict[tour][tname].get("matches", []))
    return total


def limit_matches_for_design(matches_dict, max_per_tour=2):
    limited = {"ATP": {}, "WTA": {}}
    for tour_key in ("ATP", "WTA"):
        remaining = max_per_tour
        if remaining <= 0:
            continue

        for tourney_name, tourney_data in matches_dict.get(tour_key, {}).items():
            if remaining <= 0:
                break

            kept = []
            for m in tourney_data.get("matches", []):
                if remaining <= 0:
                    break
                kept.append(m)
                remaining -= 1

            if kept:
                limited[tour_key][tourney_name] = {
                    "surface": tourney_data.get("surface", "Unknown"),
                    "matches": kept
                }
    return limited


def main():
    matches_dict = get_matches()
    total = count_matches(matches_dict)
    print(f"[TOTAL] matches available before limit: {total}")

    matches_dict = limit_matches_for_design(matches_dict, MAX_MATCHES_PER_TOUR)
    display_total = count_matches(matches_dict)
    print(f"[DISPLAY] predicting for {display_total} matches (MAX_MATCHES_PER_TOUR={MAX_MATCHES_PER_TOUR})")

    # Only run AI on displayed matches
    for tour in matches_dict:
        for tourney in matches_dict[tour]:
            for match in matches_dict[tour][tourney]["matches"]:
                print(f"Analyzing {match.get('player1')} vs {match.get('player2')}...")
                match["prediction"] = get_prediction(match)

    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("index.html")

    html_output = template.render(
        matches=matches_dict,
        last_updated=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    )

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_output)


if __name__ == "__main__":
    main()
