import os
import requests
import json
import datetime
from google import genai
from jinja2 import Environment, FileSystemLoader

# 1. SETUP
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DEBUG_DUMPS = os.environ.get("DEBUG_DUMPS", "0") == "1"

client = genai.Client(api_key=GEMINI_API_KEY)

RAPID_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE_URL = f"https://{RAPID_HOST}"

# In-memory cache to avoid repeated lookups (works great within a single run)
rank_cache = {}


def deep_get(d, path, default=None):
    """Safely read nested dict keys."""
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def normalize_rank(value):
    """Convert possible rank inputs into a valid int rank or None."""
    try:
        r = int(value)
        if 0 < r < 5000:
            return r
    except (TypeError, ValueError):
        return None
    return None


def extract_rank_from_player_or_match(player_data, match_data, key_prefix):
    """
    Try many common ranking structures across APIs.
    Returns int rank or None.
    """
    candidates = [
        # direct fields
        player_data.get("ranking"),
        player_data.get("rank"),

        # common nested shapes
        deep_get(player_data, ["ranking", "rank"]),
        deep_get(player_data, ["ranking", "position"]),
        deep_get(player_data, ["rankings", "singles", "rank"]),
        deep_get(player_data, ["rankings", "singles", "position"]),
        deep_get(player_data, ["ranking", "singles", "rank"]),
        deep_get(player_data, ["ranking", "singles", "position"]),
        deep_get(player_data, ["stats", "ranking"]),
        deep_get(player_data, ["stats", "rank"]),

        # fixture-level fallback fields
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
    Uses a cache to avoid repeated calls.

    This function tries multiple possible endpoint patterns (since API shapes vary).
    """
    if not player_id:
        return None
    if player_id in rank_cache:
        return rank_cache[player_id]

    # Candidate endpoints (we don't know the exact one — debug dumps will confirm)
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
        # Sometimes player details are nested further
        if isinstance(data, dict) and "player" in data and isinstance(data["player"], dict):
            data = data["player"]

        rank = extract_rank_from_player_or_match(data if isinstance(data, dict) else {}, {}, "")
        if rank is not None:
            break

    rank_cache[player_id] = rank  # can be None (cache misses too)
    return rank


def extract_surface(tourn, match_obj):
    """
    Try multiple likely shapes for court/surface from tournament/match payload.
    """
    surface = (
        tourn.get("surface")
        or deep_get(tourn, ["court", "surface"])
        or deep_get(tourn, ["court", "name"])
        or deep_get(tourn, ["tournament", "surface"])
        or match_obj.get("surface")
        or deep_get(match_obj, ["court", "surface"])
        or deep_get(match_obj, ["court", "name"])
        or "Unknown"
    )

    # Normalize a bit
    if isinstance(surface, dict):
        surface = surface.get("name") or surface.get("surface") or "Unknown"
    if not surface:
        surface = "Unknown"

    return surface


def get_matches():
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": RAPID_HOST
    }

    # New structure: tournament -> {surface, matches[]}
    all_matches = {"ATP": {}, "WTA": {}}

    for tour in ("atp", "wta"):
        url = f"{BASE_URL}/tennis/v2/{tour}/fixtures/{today}"

        # Pulling 100 matches per page and requesting extra player/court data
        querystring = {"include": "tournament,tournament.court,player1,player2", "pageSize": 100}

        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=25)
            response.raise_for_status()
            data = response.json()

            raw_matches = data.get("data", [])
            tour_key = tour.upper()
            tour_dict = all_matches[tour_key]

            dumped = False

            for m in raw_matches:
                tourn = m.get("tournament", {})
                tourney_name = tourn.get("name", f"{tour_key} Match")
                name_check = tourney_name.lower()

                # STRICT FILTER: Block all Challenger, ITF, Doubles, and Exhibition matches.
                exclusions = [
                    "challenger", "itf", "doubles", "exhibition",
                    "m15", "m25", "w15", "w35", "w50", "w75", "w100", "utr"
                ]
                if any(x in name_check for x in exclusions):
                    continue

                p1 = m.get("player1", {}) or {}
                p2 = m.get("player2", {}) or {}

                p1_name = p1.get("name", "Player 1")
                p2_name = p2.get("name", "Player 2")

                # remove doubles teams if they leak through
                if "/" in p1_name or "/" in p2_name:
                    continue

                # Optional debug dumps (only once per run)
                if DEBUG_DUMPS and not dumped:
                    try:
                        with open("debug_match.json", "w") as f:
                            json.dump(m, f, indent=2)
                        with open("debug_player1.json", "w") as f:
                            json.dump(p1, f, indent=2)
                        with open("debug_tournament.json", "w") as f:
                            json.dump(tourn, f, indent=2)
                        print("DEBUG: wrote debug_match.json, debug_player1.json, debug_tournament.json")
                        dumped = True
                    except Exception as e:
                        print(f"DEBUG dump failed: {e}")

                # IDs (used for player rank lookup if needed)
                p1_id = p1.get("id") or p1.get("playerId") or deep_get(p1, ["player", "id"])
                p2_id = p2.get("id") or p2.get("playerId") or deep_get(p2, ["player", "id"])

                # Rank extraction (robust)
                p1_rank = extract_rank_from_player_or_match(p1, m, "player1")
                p2_rank = extract_rank_from_player_or_match(p2, m, "player2")

                # If still missing, try player endpoint lookup (cached)
                if p1_rank is None:
                    p1_rank = fetch_player_rank_from_api(tour, str(p1_id) if p1_id else None, headers)
                if p2_rank is None:
                    p2_rank = fetch_player_rank_from_api(tour, str(p2_id) if p2_id else None, headers)

                # Images with fallbacks
                p1_image = p1.get("image") or p1.get("photo") or p1.get("picture") or ""
                p2_image = p2.get("image") or p2.get("photo") or p2.get("picture") or ""

                # Surface extraction (robust)
                surface = extract_surface(tourn, m)

                # Rank display / sorting rank
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
                    "p1_id": p1_id,
                    "p2_id": p2_id,
                    "best_rank": best_rank
                }

                # Tournament bucket
                if tourney_name not in tour_dict:
                    tour_dict[tourney_name] = {"surface": surface, "matches": []}

                # Prefer a known surface over Unknown
                if (tour_dict[tourney_name]["surface"] in ("Unknown", "", None)) and (surface not in ("Unknown", "", None)):
                    tour_dict[tourney_name]["surface"] = surface

                tour_dict[tourney_name]["matches"].append(match_obj)

        except Exception as e:
            print(f"Error fetching {tour.upper()} data: {e}")

    # SORT MATCHES BY RANK (Lowest number is best)
    for tour_key in all_matches:
        for tourney_name in all_matches[tour_key]:
            all_matches[tour_key][tourney_name]["matches"].sort(key=lambda x: x.get("best_rank", 9999))

    return all_matches


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

Output ONLY valid JSON with no markdown formatting. Do not wrap in ```json.
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


def main():
    matches_dict = get_matches()

    for tour in matches_dict:
        for tourney in matches_dict[tour]:
            match_list = matches_dict[tour][tourney]["matches"]
            for match in match_list:
                print(f"Analyzing {match.get('player1')} vs {match.get('player2')}...")
                prediction = get_prediction(match)
                match["prediction"] = prediction

    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("index.html")
    html_output = template.render(
        matches=matches_dict,
        last_updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_output)


if __name__ == "__main__":
    main()
