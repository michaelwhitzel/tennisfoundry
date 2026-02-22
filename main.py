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

# Initialize the new Google GenAI client
client = genai.Client(api_key=GEMINI_API_KEY)

def get_matches():
    """Fetches upcoming ATP and WTA tennis matches."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "tennis-api-atp-wta-itf.p.rapidapi.com"
    }
    
    all_matches =
    
    # Loop through both the men's and women's tours
    for tour in ['atp', 'wta']:
        url = f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/{tour}/fixtures/{today}"
        
        # 'include' asks the API to attach the hidden tournament and court data
        # 'pageSize' ensures we get a full list of matches, not just the first few
        querystring = {
            "include": "tournament,tournament.court",
            "pageSize": 100
        }
        
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status() 
            data = response.json()
            
            # We removed the [:10] limit to process every match found
            raw_matches = data.get('data',)
            
            for m in raw_matches:
                # Extract the newly included tournament data
                tournament_info = m.get('tournament', {})
                tourney_name = tournament_info.get('name', f'{tour.upper()} Match')
                
                # FILTER: Skip Challenger and ITF events
                name_check = tourney_name.lower()
                if "challenger" in name_check or "itf" in name_check:
                    continue
                
                # Extract the court surface (e.g., Hard, Clay, Grass)
                court_info = tournament_info.get('court', {})
                surface = court_info.get('name', 'Unknown')
                
                all_matches.append({
                    "tournament": tourney_name, 
                    "surface": surface, 
                    "player1": m.get('player1', {}).get('name', 'Player 1'),
                    "player2": m.get('player2', {}).get('name', 'Player 2'),
                })
                
        except Exception as e:
            print(f"Error fetching {tour.upper()} data: {e}")
            
    return all_matches

def get_prediction(match):
    """Asks Gemini to predict the winner."""
    # We added the tournament and surface directly into the AI prompt so it can reason better!
    prompt = f"""
    Act as a professional tennis analyst. 
    Match: {match['player1']} vs {match['player2']}
    Tournament: {match['tournament']}
    Surface: {match['surface']}
    
    Predict the winner based on general knowledge of these players and their surface preferences.
    Output ONLY valid JSON with no markdown formatting, using exactly these keys:
    {{"winner": "Player Name", "confidence": 85, "reasoning": "Brief explanation."}}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"winner": "TBD", "confidence": 0, "reasoning": "Analysis unavailable"}

def main():
    # 1. Get Data
    matches = get_matches()
    
    # 2. Analyze with AI
    analyzed_matches =
    
    for match in matches:
        print(f"Analyzing {match['player1']} vs {match['player2']} at {match['tournament']}...")
        prediction = get_prediction(match)
        match['prediction'] = prediction
        analyzed_matches.append(match)
        
        # Pause for 4 seconds between AI requests
        time.sleep(4) 
        
    # 3. Build Website
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('index.html')
    html_output = template.render(
        matches=analyzed_matches,
        last_updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    
    with open('index.html', 'w') as f:
        f.write(html_output)

if __name__ == "__main__":
    main()
