import os
import requests
import json
import datetime
from google import genai
from jinja2 import Environment, FileSystemLoader

# 1. SETUP
# Retrieve keys from GitHub Secrets
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize the new Google GenAI client
client = genai.Client(api_key=GEMINI_API_KEY)

def get_matches():
    """Fetches upcoming tennis matches."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    url = f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/atp/fixtures/{today}"
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "tennis-api-atp-wta-itf.p.rapidapi.com"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() 
        data = response.json()
        
        matches = list()
        raw_matches = data.get('data', list())[:10] 
        
        # MAPPING THE EXACT KEYS FROM YOUR LOGS
        for m in raw_matches:
            matches.append({
                "tournament": "ATP Match", 
                "surface": "Unknown", 
                "player1": m.get('player1', {}).get('name', 'Player 1'),
                "player2": m.get('player2', {}).get('name', 'Player 2'),
            })
        return matches
    except Exception as e:
        print(f"Error fetching data: {e}")
        return list()

def get_prediction(match):
    """Asks Gemini to predict the winner."""
    prompt = f"""
    Act as a professional tennis analyst. 
    Match: {match['player1']} vs {match['player2']}
    
    Predict the winner based on general knowledge of these players.
    Output JSON with these keys: winner, confidence (number 0-100), reasoning (max 15 words).
    """
    
    try:
        # UPDATED TO GEMINI 2.0 FLASH
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"winner": "TBD", "confidence": 0, "reasoning": "Analysis unavailable"}

def main():
    # 1. Get Data
    matches = get_matches()
    
    # 2. Analyze with AI
    analyzed_matches = list()
    
    for match in matches:
        print(f"Analyzing {match['player1']} vs {match['player2']}...")
        prediction = get_prediction(match)
        match['prediction'] = prediction
        analyzed_matches.append(match)
        
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
