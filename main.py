import os
import requests
import json
import datetime
from google import genai
from jinja2 import Environment, FileSystemLoader

# 1. SETUP
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_matches():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    headers = dict()
    headers.update({"X-RapidAPI-Key": RAPID_API_KEY})
    headers.update({"X-RapidAPI-Host": "tennis-api-atp-wta-itf.p.rapidapi.com"})
    
    all_matches = dict(ATP=dict(), WTA=dict())
    
    for tour in ("atp", "wta"):
        url = f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/{tour}/fixtures/{today}"
        
        # Pulling 100 matches per page and requesting extra player/court data
        querystring = dict(include="tournament,tournament.court,player1,player2", pageSize=100)
        
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status() 
            data = response.json()
            
            raw_matches = data.get("data", list())
            tour_key = tour.upper()
            tour_dict = all_matches.get(tour_key)
            
            for m in raw_matches:
                tourn = m.get("tournament", dict())
                tourney_name = tourn.get("name", f"{tour_key} Match")
                
                name_check = tourney_name.lower()
                
                # STRICT FILTER: 'qualif' removed as requested.
                # Block all Challenger, ITF, Doubles, and Exhibition matches.
                exclusions = [
                    "challenger", "itf", "doubles", "exhibition", 
                    "m15", "m25", "w15", "w35", "w50", "w75", "w100", "utr"
                ]
                if any(x in name_check for x in exclusions):
                    continue
                
                p1 = m.get("player1", dict())
                p2 = m.get("player2", dict())
                
                p1_name = p1.get("name", "Player 1")
                p2_name = p2.get("name", "Player 2")
                
                if "/" in p1_name or "/" in p2_name:
                    continue
                
                # Digging deep for ranks with fallbacks
                def get_rank(player_data, match_data, key_prefix):
                    r = player_data.get("ranking") or player_data.get("rank") or match_data.get(f"{key_prefix}Rank") or match_data.get(f"{key_prefix}_rank")
                    try:
                        return int(r)
                    except (ValueError, TypeError):
                        return 9999
                        
                p1_rank = get_rank(p1, m, "player1")
                p2_rank = get_rank(p2, m, "player2")
                
                # Digging deep for images with fallbacks
                p1_image = p1.get("image") or p1.get("photo") or p1.get("picture") or ""
                p2_image = p2.get("image") or p2.get("photo") or p2.get("picture") or ""
                
                # Digging deep for court surface with fallbacks
                surface = tourn.get("surface") or tourn.get("court", dict()).get("name") or m.get("surface") or m.get("court", dict()).get("name") or "Unknown"
                
                match_obj = dict(
                    tournament=tourney_name,
                    surface=surface,
                    player1=p1_name,
                    player2=p2_name,
                    p1_rank=p1_rank if p1_rank!= 9999 else "UR",
                    p2_rank=p2_rank if p2_rank!= 9999 else "UR",
                    p1_image=p1_image,
                    p2_image=p2_image,
                    best_rank=min(p1_rank, p2_rank)
                )
                
                if tourney_name not in tour_dict:
                    tour_dict.update({tourney_name: list()})
                    
                tour_dict.get(tourney_name).append(match_obj)
                
        except Exception as e:
            print(f"Error fetching {tour.upper()} data: {e}")
            
    # SORT MATCHES BY RANK (Lowest number is best)
    for tour_key in all_matches:
        tourney_dict = all_matches.get(tour_key)
        for tourney in tourney_dict:
            match_list = tourney_dict.get(tourney)
            match_list.sort(key=lambda x: x.get("best_rank"))
            
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
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"AI Error: {e}")
        return dict(winner="TBD", confidence=0, reasoning="Analysis unavailable")

def main():
    matches_dict = get_matches()
    
    for tour in matches_dict:
        tourney_dict = matches_dict.get(tour)
        for tourney in tourney_dict:
            match_list = tourney_dict.get(tourney)
            for match in match_list:
                print(f"Analyzing {match.get('player1')} vs {match.get('player2')}...")
                prediction = get_prediction(match)
                match.update({"prediction": prediction})
                
                # The time.sleep() delay has been completely removed to make this run instantly!
        
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("index.html")
    html_output = template.render(
        matches=matches_dict,
        last_updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    
    with open("index.html", "w") as f:
        f.write(html_output)

if __name__ == "__main__":
    main()
