import os
import requests
import json
import datetime
import time
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
    
    # Initialize the dictionary safely without using brackets!
    all_matches = dict(ATP=dict(), WTA=dict())
    
    for tour in ("atp", "wta"):
        url = f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/{tour}/fixtures/{today}"
        
        querystring = dict(include="tournament,tournament.court,player1,player2", pageSize=100)
        
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status() 
            data = response.json()
            
            raw_matches = data.get("data", list())
            tour_key = tour.upper()
            tour_dict = all_matches.get(tour_key)
            
            for m in raw_matches:
                tourney_name = m.get("tournament", dict()).get("name", f"{tour_key} Match")
                
                # FILTER: Skip Challenger, ITF, and Doubles events
                name_check = tourney_name.lower()
                if "challenger" in name_check or "itf" in name_check or "doubles" in name_check:
                    continue
                
                p1 = m.get("player1", dict())
                p2 = m.get("player2", dict())
                
                p1_name = p1.get("name", "Player 1")
                p2_name = p2.get("name", "Player 2")
                
                if "/" in p1_name or "/" in p2_name:
                    continue
                
                def get_rank(player_data):
                    r = player_data.get("ranking") or player_data.get("rank")
                    try:
                        return int(r)
                    except (ValueError, TypeError):
                        return 9999
                        
                p1_rank = get_rank(p1)
                p2_rank = get_rank(p2)
                
                p1_image = p1.get("image") or p1.get("photo") or ""
                p2_image = p2.get("image") or p2.get("photo") or ""
                
                surface = m.get("tournament", dict()).get("court", dict()).get("name", "Unknown")
                
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
            
    # SORT MATCHES BY RANK
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
    Act as a professional tennis analyst applying the Analytic Network Process (ANP) model for match prediction.
    Evaluate both tangible criteria (rankings, surface preference, head-to-head) and intangible criteria (psychological momentum, fatigue, motivation).
    
    Match: {p1} (Rank: {r1}) vs {p2} (Rank: {r2})
    Tournament: {t_name}
    Surface: {surf}
    
    Predict the winner.
    Output ONLY valid JSON with no markdown formatting. Do not wrap in ```json.
    Use exactly these keys:
    {{"winner": "Player Name", "confidence": <insert unique integer between 0 and 100>, "reasoning": "Brief ANP-based explanation."}}
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
                time.sleep(4) 
        
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
