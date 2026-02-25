import os
import requests
import json
import datetime
from urllib.parse import quote_plus
from google import genai
from jinja2 import Environment, FileSystemLoader

# =========================
# CONFIG / SETUP
# =========================
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DEBUG_DUMPS = os.environ.get("DEBUG_DUMPS", "0") == "1"

# During design: limit to 2 ATP + 2 WTA total matches
MAX_MATCHES_PER_TOUR = int(os.environ.get("MAX_MATCHES_PER_TOUR", "2"))

if not RAPID_API_KEY:
    raise RuntimeError("Missing RAPID_API_KEY env var")
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY env var")

client = genai.Client(api_key=GEMINI_API_KEY)

RAPID_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE_URL = f"https://{RAPID_HOST}"

rank_cache = {}

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


def try_fetch_json(url, headers, params=None, timeout=20):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_player_rank_from_api(tour, player_id, headers):
    """
    If fixtures don't include rank, try to fetch player detail and parse it.
    Tries multiple endpoint patterns and fails gracefully.
    """
    if not player_id:
        return None
    if player_id in rank_cache:
        return rank_cache[player_id]

    candidate_urls = [
        f"{BASE_URL}/tennis/v2/{tour}/player/{player_id}",
        f"{BASE_URL}/tennis/v2/{tour}/players/{player_id}",
        f"{BASE_URL}/tennis/v2/{tour}/player/profile/{player_id}",
        f"{BASE_URL}/tennis/v2/player/{player_id}",
        f"{BASE_URL}/tennis/v2/players/{player_id}",
    ]

    rank = None
    for url in candidate_urls:
        payload = try_fetch_json(url, headers=headers)
        if not payload:
            continue

        data = payload.get("data", payload)
        if isinstance(data, dict) and "player" in data and isinstance(data["player"], dict):
            data = data["player"]

        rank = extract_rank_from_player_or_match(data if isinstance(data, dict) else {}, {}, "")
        if rank is not None:
            break

    rank_cache[player_id] = rank
    return rank


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
    """
    Guaranteed avatar image (initials). Always works on GitHub Pages.
    """
    # ui-avatars is a simple initials generator. Encode name safely.
    name_q = quote_plus(player_name.strip() if player_name else "Player")
    # Dark-friendly palette
    return f"https://ui-avatars.com/api/?name={name_q}&background=111827&color=E5E7EB&bold=true&size=128&format=png"


def normalize_image_url(raw, player_name: str) -> str:
    """
    Tries to produce a usable https URL.
    If missing/bad, returns initials avatar.
    """
    url = ""

    # Some APIs return dicts like {"url": "..."} or {"path": "..."}
    if isinstance(raw, dict):
        url = raw.get("url") or raw.get("image") or raw.get("path") or raw.get("photo") or ""
    elif isinstance(raw, str):
        url = raw.strip()

    if not url:
        return avatar_fallback_url(player_name)

    # protocol-relative //example.com/img.jpg
    if url.startswith("//"):
        url = "https:" + url

    # force https when possible
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]

    # relative path "/images/.."
    # We don't know the correct host reliably, so treat as unusable and fallback
    if url.startswith("/"):
        return avatar_fallback_url(player_name)

    # If it’s not http(s), fallback
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
    utc_now = datetime.datetime.utcnow()
    date_list = [
        (utc_now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        utc_now.strftime("%Y-%m-%d"),
        (utc_now + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
    ]

    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": RAPID_HOST
    }

    all_matches = {"ATP": {}, "WTA": {}}
    seen_keys = set()

    exclusions = [
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

    for tour in ("atp", "wta"):
        tour_key = tour.upper()

        for day in date_list:
            page = 1
            while True:
                url = f"{BASE_URL}/tennis/v2/{tour}/fixtures/{day}"
                querystring = {
                    "include": "tournament,tournament.court,player1,player2",
                    "pageSize": 100,
                    "page": page,
                    "pageNumber": page,
                }

                try:
                    response = requests.get(url, headers=headers, params=querystring, timeout=25)
                    response.raise_for_status()
                    data = response.json()
                    raw_matches = data.get("data", []) or []

                    if not raw_matches:
                        break

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

                        p1_id = p1.get("id") or p1.get("playerId") or deep_get(p1, ["player", "id"])
                        p2_id = p2.get("id") or p2.get("playerId") or deep_get(p2, ["player", "id"])

                        p1_rank = extract_rank_from_player_or_match(p1, m, "player1")
                        p2_rank = extract_rank_from_player_or_match(p2, m, "player2")

                        if p1_rank is None:
                            p1_rank = fetch_player_rank_from_api(tour, str(p1_id) if p1_id else None, headers)
                        if p2_rank is None:
                            p2_rank = fetch_player_rank_from_api(tour, str(p2_id) if p2_id else None, headers)

                        surface = extract_surface(tourn, m)

                        # Images: pull + normalize + always fallback
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

                    if len(raw_matches) < 100:
                        break

                    page += 1
                    if page > 10:
                        break

                except Exception as e:
                    print(f"Error fetching {tour.upper()} data for {day} page {page}: {e}")
                    break

    for tour_key in all_matches:
        for tourney_name in all_matches[tour_key]:
            all_matches[tour_key][tourney_name]["matches"].sort(key=lambda x: x.get("best_rank", 9999))

    return all_matches


def limit_matches_for_design(matches_dict, max_per_tour=2):
    limited = {"ATP": {}, "WTA": {}}

    for tour_key in ("ATP", "WTA"):
        remaining = max_per_tour
        if remaining <= 0:
            continue

        for tourney_name, tourney_data in matches_dict.get(tour_key, {}).items():
            if remaining <= 0:
                break

            kept_matches = []
            for m in tourney_data.get("matches", []):
                if remaining <= 0:
                    break
                kept_matches.append(m)
                remaining -= 1

            if kept_matches:
                limited[tour_key][tourney_name] = {
                    "surface": tourney_data.get("surface", "Unknown"),
                    "matches": kept_matches
                }

    return limited


def main():
    matches_dict = get_matches()
    matches_dict = limit_matches_for_design(matches_dict, MAX_MATCHES_PER_TOUR)

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
