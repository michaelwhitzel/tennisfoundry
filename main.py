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
    
    # We use an f-string (notice the 'f' before the quotes) to dynamically 
    # insert today's date directly into the URL path, exactly as the API requires.
    url = f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/atp/fixtures/{today}"
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "tennis-api-atp-wta-itf.p.rapidapi.com"
    }
    
    try:
        # We removed the params={"date": today} because the date is now in the URL!
        response = requests.get(url, headers=headers)
        response.raise_for_status() 
        data = response.json()
        print("API RESPONSE:", data)
        
        matches = list()
        
        # We are temporarily guessing the data is stored under 'data' or 'events'
        # We will check your logs to see exactly what this API calls it!
        raw_matches = data.get('data', list())[:10] 
        
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
        return list()

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
        response = client.models.generate_content(
            model='gemini-1.5-flash',
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
