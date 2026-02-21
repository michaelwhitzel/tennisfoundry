import os
import requests
import json
import datetime
import google.generativeai as genai
from jinja2 import Environment, FileSystemLoader

# 1. SETUP
# Retrieve keys from GitHub Secrets
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# Use 'response_mime_type' to force Gemini to reply with JSON
model = genai.GenerativeModel('gemini-1.5-flash', generation_config={"response_mime_type": "application/json"})

def get_matches():
    """Fetches upcoming tennis matches."""
    url = "https://ultimate-tennis1.p.rapidapi.com/v1/matches"
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "ultimate-tennis1.p.rapidapi.com"
    }
    
    try:
        # Fetching matches for today
        response = requests.get(url, headers=headers, params={"date": today})
        data = response.json()
        
        # Simple data cleaning to get a list of readable matches
        matches =
        raw_matches = data.get('events',)[:10] # Limit to 10 matches to save AI credits
        
        for m in raw_matches:
            matches.append({
                "tournament": m.get('tournament', {}).get('name', 'Tournament'),
                "surface": m.get('tournament', {}).get('surface', 'Hard'),
                "player1": m.get('homeTeam', {}).get('name', 'Player 1'),
                "player2": m.get('awayTeam', {}).get('name', 'Player 2'),
            })
        return matches
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

def get_prediction(match):
    """Asks Gemini to predict the winner."""
    prompt = f"""
    Act as a professional tennis analyst. 
    Match: {match['player1']} vs {match['player2']}
    Surface: {match['surface']}
    
    Predict the winner based on general knowledge of these players.
    Output JSON with these keys: winner, confidence (number 0-100), reasoning (max 15 words).
    """
    
    try:
        response = model.generate_content(prompt)
        return json.loads(response.text)
    except:
        return {"winner": "TBD", "confidence": 0, "reasoning": "Analysis unavailable"}

def main():
    # 1. Get Data
    matches = get_matches()
    
    # 2. Analyze with AI
    analyzed_matches =
    
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
