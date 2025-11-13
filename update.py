import requests
import os
import json
from datetime import datetime, timedelta
from google import genai
from google.genai.errors import APIError

HTML_FILE = 'blackrock_ibit_tracker.html'

def fetch_live_data():
    """
    Fetches live BTC price using CoinGecko and calculates IBIT metrics 
    based on a manually updated holdings number.
    """
    print("--- FETCHING LIVE BTC PRICE AND CALCULATING IBIT METRICS ---")
    
    # 1. Automatic BTC Price Fetch (Uses CoinGecko Public API - NO KEY NEEDED)
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd')
        response.raise_for_status() # Raises an error for bad status codes
        current_price = response.json().get('bitcoin', {}).get('usd')
        if not current_price:
            print("Error: Could not retrieve current BTC price from CoinGecko.")
            return None, None
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching BTC price: {e}")
        return None, None

    
    # 2. Manual Holdings Input (The necessary compromise for free data)
    
    # <-- USER: UPDATE THIS NUMBER WEEKLY OR BI-WEEKLY -->
    # Find the latest total IBIT holdings in BTC from a public source (e.g., Bitbo.io)
    # This number will be used as the base for the AUM calculation.
    LATEST_IBIT_HOLDINGS_BTC = 306050.0 # Example: Update this to the current total
    # -------------------------------------------------------------------
    
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    # Load the existing data to find the holdings from the previous day
    last_holdings = None
    
    try:
        with open(HTML_FILE, 'r') as f:
            content = f.read()
        
        # Simple method to find the last holdings entry
        data_start = content.find('// <NEW_FUND_DATA_INJECTION>')
        data_end = content.find('// </NEW_FUND_DATA_INJECTION>')
        
        if data_start != -1 and data_end != -1:
            data_block = content[data_start:data_end]
            last_data_line = data_block.strip().split('\n')[-2]
            
            # This is fragile but works given our HTML format:
            # { date: "...", price: ..., holdings: 305613.5, btcFlow: ..., aum: ..., usdFlow: ... }
            if 'holdings:' in last_data_line:
                # Basic string manipulation to extract the holdings value
                start_h = last_data_line.find('holdings:') + len('holdings:')
                end_h = last_data_line.find(',', start_h)
                last_holdings = float(last_data_line[start_h:end_h].strip())
    except Exception as e:
        print(f"Warning: Could not extract last holdings data. Using hardcoded fallback. Error: {e}")
        last_holdings = LATEST_IBIT_HOLDINGS_BTC - 500 # Fallback 

    # 3. Calculate Daily Flow and AUM
    btc_flow = LATEST_IBIT_HOLDINGS_BTC - last_holdings
    aum = LATEST_IBIT_HOLDINGS_BTC * current_price
    usd_flow = btc_flow * current_price

    new_entry = {
        "date": today_str,
        "price": current_price,
        "holdings": LATEST_IBIT_HOLDINGS_BTC,
        "btcFlow": btc_flow,
        "aum": aum,
        "usdFlow": usd_flow
    }

    return new_entry, today_str


def generate_ai_content(new_data):
    """Generates a daily market summary using the Gemini API."""
    print("--- GENERATING AI CONTENT ---")
    
    # Get the key from the environment variable (GitHub Secret)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set.")
        return "AI content generation failed: Missing API Key."

    client = genai.Client(api_key=api_key)

    data_summary = f"""
    Latest IBIT ETF Metrics (Date: {new_data['date']}):
    - BTC Price: ${new_data['price']:,}
    - Total BTC Holdings: {new_data['holdings']:,} BTC
    - Daily BTC Net Flow: {new_data['btcFlow']:,} BTC
    - Total AUM (USD): ${new_data['aum']:,}
    - Daily USD Net Flow: ${new_data['usdFlow']:,}
    """
    
    system_prompt = "You are a professional financial blogger specializing in Bitcoin ETFs. Write a concise, 3-4 sentence daily summary of the BlackRock IBIT ETF activity. Focus on the net flow and any significant change in holdings or price. Maintain an informative and moderately optimistic tone."

    user_prompt = f"Write the summary based on these key metrics:\n\n{data_summary}"
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-09-2025',
            contents=user_prompt,
            system_instruction=system_prompt
        )
        return response.text.replace('\n', ' ')
    except APIError as e:
        print(f"Gemini API Error: {e}")
        return "AI content generation failed due to API connection error."
    except Exception as e:
        print(f"An unexpected error occurred during AI generation: {e}")
        return "AI content generation failed due to an unexpected error."


def update_html_file(new_entry, summary_text):
    """Replaces the old data array and AI content in the HTML file."""
    print("--- UPDATING HTML FILE ---")
    
    with open(HTML_FILE, 'r') as f:
        content = f.read()

    # 1. Update the Data Array
    data_start_tag = '// <NEW_FUND_DATA_INJECTION>'
    data_end_tag = '// </NEW_FUND_DATA_INJECTION>'

    start_index = content.find(data_start_tag)
    end_index = content.find(data_end_tag)

    if start_index == -1 or end_index == -1:
        print("Error: Data injection markers not found.")
        return False
        
    # Extract existing array, remove the last closing bracket and semicolon
    existing_data_block = content[start_index + len(data_start_tag):end_index]
    existing_data_lines = existing_data_block.strip().split('\n')
    
    # Find the last line that starts with { and strip the comma
    if existing_data_lines and existing_data_lines[-1].strip().startswith('{'):
        existing_data_lines[-1] = existing_data_lines[-1].strip().rstrip(',')
    
    # Reconstruct the new array content
    new_data_line = f"{{ date: \"{new_entry['date']}\", price: {new_entry['price']}, holdings: {new_entry['holdings']}, btcFlow: {new_entry['btcFlow']}, aum: {new_entry['aum']}, usdFlow: {new_entry['usdFlow']} }}"
    
    updated_data_block = '\n'.join(existing_data_lines) + ',\n            ' + new_data_line + '\n        '

    # Inject the updated array back into the HTML
    new_script_content = f"{data_start_tag}\n        const fundData = [\n{updated_data_block.strip()}];\n        {data_end_tag}"
    content = content[:start_index] + new_script_content + content[end_index + len(data_end_tag):]

    # 2. Update the AI Article Content
    ai_content_start_tag = '<p id="aiContent" class="text-lg text-gray-700 leading-relaxed mb-8">'
    ai_content_end_tag = '</p>'
    
    ai_start_index = content.find(ai_content_start_tag) + len(ai_content_start_tag)
    ai_end_index = content.find(ai_content_end_tag, ai_start_index)

    if ai_start_index == -1 or ai_end_index == -1:
        print("Error: AI content injection markers not found.")
        return False

    # Inject the new summary text
    content = content[:ai_start_index] + '\n                ' + summary_text + '\n            ' + content[ai_end_index:]

    # 3. Update the last updated date
    content = content.replace("DATE_PLACEHOLDER", new_entry['date'])
    
    with open(HTML_FILE, 'w') as f:
        f.write(content)

    print(f"Successfully updated {HTML_FILE} with data for {new_entry['date']}")
    return True


if __name__ == '__main__':
    # Step 1: Fetch/Generate new data
    new_data, today_date = fetch_live_data()
    if not new_data:
        print("Daily update failed: Could not retrieve new data.")
        exit(1)

    # Step 2: Generate AI content
    summary = generate_ai_content(new_data)
    
    # Step 3: Update the HTML file
    if update_html_file(new_data, summary):
        print("--- DAILY UPDATE SUCCESSFUL ---")
