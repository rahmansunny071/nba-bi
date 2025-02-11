import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time

############################
# 1. HELPER FUNCTIONS
############################

def scrape_draft_data(year):
    """Scrape the NBA draft page for a given year from Basketball Reference."""
    url = f"https://www.basketball-reference.com/draft/NBA_{year}.html"
    resp = requests.get(url)
    if resp.status_code != 200:
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.find('table', {'id': 'stats'})
    if not table:
        return []
    
    data_rows = []
    rows = table.find('tbody').find_all('tr', recursive=False)
    for row in rows:
        # Some rows might be headers
        if 'class' in row.attrs and 'thead' in row.attrs['class']:
            continue
        
        cols = row.find_all(['th','td'])
        if len(cols) < 3:
            continue
        
        pick = cols[0].get_text(strip=True)
        team = cols[1].get_text(strip=True)
        
        player_td = cols[2]
        player_name = player_td.get_text(strip=True)
        link = None
        a_tag = player_td.find('a')
        if a_tag and a_tag.get('href'):
            link = a_tag['href']
        
        data_rows.append({
            'year': year,
            'pick': pick,
            'team': team,
            'player_name': player_name,
            'player_link': link
        })
    return data_rows


def scrape_player_details(relative_url):
    """
    Given a relative URL from Basketball Reference (like '/players/a/abramda01.html'),
    scrape the player's listed position and height in inches.
    """
    base = "https://www.basketball-reference.com"
    full_url = base + relative_url
    
    resp = requests.get(full_url)
    if resp.status_code != 200:
        return {'position': None, 'height_inches': None}
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    meta = soup.find('div', id='meta')
    if not meta:
        return {'position': None, 'height_inches': None}
    
    position = None
    height_inches = None
    
    # Attempt to extract lines like "Position: Point Guard" and "Height: 6-6"
    strong_tags = meta.find_all('strong')
    for s in strong_tags:
        label = s.get_text(strip=True)
        parent_text = s.parent.get_text(" ", strip=True)
        
        if "Position:" in label:
            # e.g. "Position: Point Guard ▪ Shoots: Right"
            # or "Position: Guard-Forward"
            position = parent_text.replace("Position:", "").strip()
        
        if "Height:" in label:
            # e.g. "Height: 6-3 (190cm)"
            match = re.search(r'(\d+)-(\d+)', parent_text)
            if match:
                ft = int(match.group(1))
                inch = int(match.group(2))
                height_inches = ft * 12 + inch
    
    return {'position': position, 'height_inches': height_inches}


def is_point_guard(position_str):
    """
    Naive check to decide if a position indicates PG status.
    We consider 'PG', 'point guard', or ambiguous "guard" (with no forward) as PG.
    """
    if not position_str:
        return False
    p = position_str.lower()
    if "point guard" in p or "pg" in p:
        return True
    if "guard" in p and "forward" not in p and "center" not in p:
        return True
    return False


def get_avg_pg_height_by_team(df, year_min=None, year_max=None):
    """
    Given a DataFrame with columns ['year','team','position_str','height_inches','is_pg'],
    optionally filter by year range, then group by team to compute average PG height.
    Returns a new DataFrame with columns: ['team','avg_height_in','count_pgs'].
    """
    df_filtered = df.copy()
    if year_min is not None:
        df_filtered = df_filtered[df_filtered['year'] >= year_min]
    if year_max is not None:
        df_filtered = df_filtered[df_filtered['year'] <= year_max]
    
    # Only PGs
    df_filtered = df_filtered[df_filtered['is_pg'] == True]
    if df_filtered.empty:
        return pd.DataFrame([], columns=['team','avg_height_in','count_pgs'])
    
    grouped = (
        df_filtered
        .groupby('team', as_index=False)
        .agg(avg_height_in=('height_inches','mean'),
             count_pgs=('player_name','count'))
    )
    grouped = grouped.sort_values('avg_height_in', ascending=False)
    return grouped


###############################
# 2. STREAMLIT APP
###############################

def parse_user_question(question):
    """
    A very naive approach to parse user questions about:
      - "Which team drafted the tallest PGs from YEAR1 to YEAR2?"
      - or "Which team had the tallest PGs in 2000?"
    We'll attempt to extract a start year and an end year if mentioned.
    Returns a dict with 'year_min', 'year_max', 'found_year_range' (bool).
    """
    # Basic regex to find years
    matches = re.findall(r'(\b(19|20)\d{2}\b)', question)
    # 'matches' will be a list of tuples, each like ('1998','19')
    years = [int(m[0]) for m in matches]  # pull just the year int
    
    if not years:
        return {"year_min": None, "year_max": None, "found_year_range": False}
    
    # If user mentioned multiple years, we guess the range is from min to max of them
    y_min = min(years)
    y_max = max(years)
    return {"year_min": y_min, "year_max": y_max, "found_year_range": True}


def main():
    st.title("NBA Draft PG Heights Chatbot Demo")
    
    st.write(
        """
        **Overview**  
        1. **Scrape** NBA draft data from [Basketball Reference](https://www.basketball-reference.com/draft/).  
        2. **Extract** player positions and heights by visiting each player's profile.  
        3. **Filter** for PGs and compute average heights by drafting team.  
        4. **Chatbot**: Ask queries about which team drafted the tallest PGs in a certain year or range.
        
        **Steps to Use**  
        1. Select a range of draft years to scrape.  
        2. Click "Scrape & Build Dataset."  
        3. Once data is loaded, type a question in the chatbox below.  
           Examples:  
           - "Which team drafted the tallest PGs from 2000 to 2010?"  
           - "Which team had the tallest PGs in 1998?"  
           - "Show me the top teams for the entire dataset."
        """
    )
    
    # (A) Control for scraping
    min_scrape_year, max_scrape_year = 1980, 2023
    year_range = st.slider("Select Draft Years to Scrape", 
                           min_value=min_scrape_year, 
                           max_value=max_scrape_year, 
                           value=(1990, 2000))
    
    # We store the big dataset in session_state to avoid re-scraping each time.
    if "df_master" not in st.session_state:
        st.session_state.df_master = None
    
    if st.button("Scrape & Build Dataset"):
        st.write("Beginning data scraping...")
        
        all_rows = []
        for y in range(year_range[0], year_range[1]+1):
            st.write(f"Scraping draft data for {y}...")
            draft_rows = scrape_draft_data(y)
            all_rows.extend(draft_rows)
            time.sleep(1)  # short delay out of courtesy for the site
        
        if not all_rows:
            st.error("No draft data was retrieved. Check logs or try a different range.")
            return
        
        df = pd.DataFrame(all_rows)
        
        st.write("Scraping each player's page for position and height. Please wait...")
        positions = []
        heights = []
        for idx, r in df.iterrows():
            link = r['player_link']
            if not link:
                positions.append(None)
                heights.append(None)
                continue
            details = scrape_player_details(link)
            positions.append(details['position'])
            heights.append(details['height_inches'])
            time.sleep(0.5)
        
        df['position_str'] = positions
        df['height_inches'] = heights
        df['is_pg'] = df['position_str'].apply(is_point_guard)
        
        st.session_state.df_master = df
        st.success("Scraping & dataset building complete!")
    
    st.write("---")
    
    if st.session_state.df_master is None:
        st.warning("Please scrape the data first.")
        return
    
    # (B) Chatbot UI
    st.subheader("Chat with the Draft PG Data")
    
    # We'll store chat messages in session state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    
    user_input = st.text_input("Ask a question about tallest PG draftees, e.g. 'Which team had the tallest PGs in 2010?'")
    
    if st.button("Send"):
        # Add user message to chat
        question = user_input.strip()
        st.session_state.chat_history.append(("user", question))
        
        # Attempt to parse
        parse_result = parse_user_question(question)
        year_min = parse_result["year_min"]
        year_max = parse_result["year_max"]
        found_range = parse_result["found_year_range"]
        
        df_master = st.session_state.df_master.copy()
        
        # If user didn't specify a year range, let's assume entire dataset scraping range
        if not found_range:
            # We'll just use the entire set
            st_msg = "No specific year found in question. Displaying results for **all scraped years**."
            st.session_state.chat_history.append(("assistant", st_msg))
            
            # Compute the ranking
            results = get_avg_pg_height_by_team(df_master)
            
        else:
            st_msg = (f"I detected a year range from {year_min} to {year_max}."
                      " Computing results for that range.")
            st.session_state.chat_history.append(("assistant", st_msg))
            
            results = get_avg_pg_height_by_team(df_master, year_min, year_max)
        
        if results.empty:
            answer = "No point guards were found in that range or the data might be incomplete."
            st.session_state.chat_history.append(("assistant", answer))
        else:
            # Let's present the top 5 in the chat response
            top_results = results.head(5)
            top_strings = []
            for idx, row in top_results.iterrows():
                # Convert inches to feet & inches or just display raw
                avg_in = row['avg_height_in']
                count = row['count_pgs']
                team = row['team']
                # optional: convert to ft-in
                ft_part = int(avg_in // 12)
                in_part = int(avg_in % 12)
                top_strings.append(f"{team} - {avg_in:.2f} in (≈ {ft_part} ft {in_part} in), PGs: {count}")
            
            answer = "**Top 5 teams by average PG height:**\n" + "\n".join(top_strings)
            st.session_state.chat_history.append(("assistant", answer))
    
    # Display the chat history
    for role, msg in st.session_state.chat_history:
        if role == "user":
            st.markdown(f"**You:** {msg}")
        else:
            st.markdown(f"**Bot:** {msg}")
