import os
import time
import sqlite3
import requests
import schedule
import logging
from datetime import datetime, timedelta

# --- Configuration & Environment Variables ---

# General Settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
# Fixed internal path, user doesn't need to change this usually
DB_PATH = "/data/history.db" 
REQUEST_DELAY_SECONDS = int(os.getenv("REQUEST_DELAY_SECONDS", "2")) # Delay between individual API calls
MAX_CYCLE_DAYS = int(os.getenv("MAX_CYCLE_DAYS", "30")) # Safety net to reset cycle
# Scheduler in minutes
RUN_EVERY_MINUTES = int(os.getenv("RUN_EVERY", "15"))

# Sonarr Settings
SONARR_URL = os.getenv("SONARR_URL")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")
SONARR_LIMIT = int(os.getenv("SONARR_LIMIT", "10"))
SONARR_CUTOFF_LIMIT = int(os.getenv("SONARR_CUTOFF_LIMIT", "0")) # 0 means disabled
SONARR_ENABLED = True if SONARR_URL and SONARR_API_KEY else False

# Radarr Settings
RADARR_URL = os.getenv("RADARR_URL")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")
RADARR_LIMIT = int(os.getenv("RADARR_LIMIT", "10"))
RADARR_CUTOFF_LIMIT = int(os.getenv("RADARR_CUTOFF_LIMIT", "0")) # 0 means disabled
RADARR_ENABLED = True if RADARR_URL and RADARR_API_KEY else False

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger(__name__)

# --- Database Management ---

def init_db():
    """Initialize the SQLite database and tables if they don't exist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Table for Sonarr searched IDs
        c.execute('''CREATE TABLE IF NOT EXISTS sonarr_searches
                     (id INTEGER PRIMARY KEY, timestamp TEXT)''')
        # Table for Radarr searched IDs
        c.execute('''CREATE TABLE IF NOT EXISTS radarr_searches
                     (id INTEGER PRIMARY KEY, timestamp TEXT)''')
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

def get_searched_ids(table_name):
    """Retrieve all IDs that have already been searched in the current cycle."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"SELECT id FROM {table_name}")
        rows = c.fetchall()
        conn.close()
        return {row[0] for row in rows}
    except Exception as e:
        logger.error(f"Error reading from {table_name}: {e}")
        return set()

def add_searched_id(table_name, item_id):
    """Add an ID to the database after a successful search command."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute(f"INSERT OR IGNORE INTO {table_name} (id, timestamp) VALUES (?, ?)", (item_id, timestamp))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error adding ID {item_id} to {table_name}: {e}")

def wipe_table(table_name):
    """Wipe a table to restart the cycle."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"DELETE FROM {table_name}")
        conn.commit()
        conn.close()
        logger.warning(f"Creating a fresh cycle: Table {table_name} has been wiped.")
    except Exception as e:
        logger.error(f"Error wiping table {table_name}: {e}")

def check_safety_net(table_name):
    """
    Check if the current cycle has exceeded MAX_CYCLE_DAYS.
    If yes, wipe the table to force a reset.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Get the oldest timestamp
        c.execute(f"SELECT timestamp FROM {table_name} ORDER BY timestamp ASC LIMIT 1")
        row = c.fetchone()
        conn.close()

        if row:
            oldest_date = datetime.fromisoformat(row[0])
            if datetime.now() - oldest_date > timedelta(days=MAX_CYCLE_DAYS):
                logger.warning(f"Safety Net Triggered: Cycle for {table_name} exceeded {MAX_CYCLE_DAYS} days.")
                wipe_table(table_name)
    except Exception as e:
        logger.error(f"Error checking safety net for {table_name}: {e}")

# --- API Interaction ---

def fetch_ids_from_endpoint(url, api_key, endpoint_suffix):
    """Helper function to fetch IDs from a specific endpoint."""
    headers = {'X-Api-Key': api_key}
    ids = [] # Default return value
    
    try:
        endpoint = f"{url}{endpoint_suffix}"
        response = requests.get(endpoint, headers=headers, timeout=30)
        response.raise_for_status()
        
        # We process data only if request succeeded
        data = response.json()
        
        # Sonarr/Radarr 'wanted' endpoints usually return 'records' wrapper
        if isinstance(data, dict) and 'records' in data:
            ids = [item['id'] for item in data['records']]
        # Sometimes it returns a direct list (depending on endpoint version)
        elif isinstance(data, list):
             ids = [item['id'] for item in data]
             
    except Exception as e:
        logger.error(f"Error fetching from {url}: {e}")
        # Even if it fails, 'ids' exists (as empty list), so no error happens.
        
    return ids

def get_combined_content(app_name, url, api_key, limit, cutoff_limit):
    """
    Fetch missing content AND cutoff unmet content (if enabled).
    Returns a unified list of IDs to search.
    """
    final_ids = []
    
    # 1. Fetch Missing
    if app_name == "sonarr":
        # Sonarr Missing: /api/v3/wanted/missing
        missing_ids = fetch_ids_from_endpoint(url, api_key, "/api/v3/wanted/missing?page=1&pageSize=1000&sortKey=airDateUtc&sortDir=desc")
        final_ids.extend(missing_ids)
        
        # Sonarr Cutoff: /api/v3/wanted/cutoff
        if cutoff_limit > 0:
            cutoff_ids = fetch_ids_from_endpoint(url, api_key, "/api/v3/wanted/cutoff?page=1&pageSize=1000&sortKey=airDateUtc&sortDir=desc")
            final_ids.extend(cutoff_ids)

    elif app_name == "radarr":
        # Radarr Missing: We filter standard movie endpoint
        headers = {'X-Api-Key': api_key}
        try:
            response = requests.get(f"{url}/api/v3/movie", headers=headers, timeout=30)
            response.raise_for_status()
            movies = response.json()
            
            # Logic: Missing AND Monitored
            missing_ids = [m['id'] for m in movies if not m.get('hasFile') and m.get('monitored')]
            final_ids.extend(missing_ids)

            # Logic: Cutoff Unmet AND Monitored (Only if enabled)
            if cutoff_limit > 0:
                # Radarr V3: Check for qualityCutoffNotMet in movieFile (if file exists)
                # Or use wanted/cutoff endpoint if available (safer to filter manually here to match Missing logic)
                # We will check 'monitored' and 'hasFile' is True, but needs upgrade?
                # Actually, Radarr has a specific endpoint /api/v3/wanted/cutoff similar to Sonarr now.
                # Let's try the endpoint first as it's cleaner.
                cutoff_ids = fetch_ids_from_endpoint(url, api_key, "/api/v3/wanted/cutoff?page=1&pageSize=1000&sortKey=title&sortDir=asc")
                final_ids.extend(cutoff_ids)
                
        except Exception as e:
            logger.error(f"Error fetching Radarr content: {e}")

    # Remove duplicates (if an item appears in both for some reason)
    return list(set(final_ids))

def trigger_search(app_name, url, api_key, item_ids):
    """Trigger the search command for specific IDs."""
    headers = {'X-Api-Key': api_key}
    
    for item_id in item_ids:
        try:
            payload = {}
            if app_name == "sonarr":
                payload = {'name': 'EpisodeSearch', 'episodeIds': [item_id]}
            elif app_name == "radarr":
                payload = {'name': 'MoviesSearch', 'movieIds': [item_id]}

            response = requests.post(f"{url}/api/v3/command", json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            logger.info(f"[{app_name}] Triggered search for ID: {item_id}")
            add_searched_id(f"{app_name}_searches", item_id)
            
            if REQUEST_DELAY_SECONDS > 0:
                time.sleep(REQUEST_DELAY_SECONDS)

        except Exception as e:
            logger.error(f"[{app_name}] Failed to search for ID {item_id}: {e}")

# --- Core Logic ---

def run_cycle(app_name):
    if app_name == "sonarr" and not SONARR_ENABLED: return
    if app_name == "radarr" and not RADARR_ENABLED: return

    url = SONARR_URL if app_name == "sonarr" else RADARR_URL
    api_key = SONARR_API_KEY if app_name == "sonarr" else RADARR_API_KEY
    limit = SONARR_LIMIT if app_name == "sonarr" else RADARR_LIMIT
    cutoff_limit = SONARR_CUTOFF_LIMIT if app_name == "sonarr" else RADARR_CUTOFF_LIMIT
    table_name = f"{app_name}_searches"

    logger.info(f"Starting cycle for {app_name}...")

    # 1. Safety Net
    check_safety_net(table_name)

    # 2. Fetch All Candidates (Missing + Cutoff)
    # We pass the cutoff limit to decide if we fetch upgrades or not
    all_candidates = get_combined_content(app_name, url, api_key, limit, cutoff_limit)
    
    if not all_candidates:
        logger.info(f"[{app_name}] No missing or upgradeable content found.")
        return

    # 3. Filter against DB
    searched_ids = get_searched_ids(table_name)
    target_list = [id for id in all_candidates if id not in searched_ids]

    logger.info(f"[{app_name}] Total Candidates: {len(all_candidates)} | Already Searched: {len(searched_ids)} | Remaining: {len(target_list)}")

    # 4. Completion Check (Reset Condition)
    if not target_list:
        if len(searched_ids) > 0:
            logger.info(f"[{app_name}] CYCLE COMPLETE. Wiping DB.")
            wipe_table(table_name)
        return

    # 5. Execute Batch
    # Logic: We process 'limit' items. If cutoff is enabled, they are mixed in the target_list.
    batch_to_search = target_list[:limit]
    
    logger.info(f"[{app_name}] Searching batch of {len(batch_to_search)} items.")
    trigger_search(app_name, url, api_key, batch_to_search)

# --- Scheduling ---

def job():
    logger.info("--- Scheduled Run Started ---")
    if SONARR_ENABLED: run_cycle("sonarr")
    if RADARR_ENABLED: run_cycle("radarr")
    logger.info("--- Scheduled Run Finished ---")

def main():
    logger.info(f"Starting Arr-Missing-Content Service. Run Every: {RUN_EVERY_MINUTES} mins.")
    init_db()
    job() # Initial run
    
    # Dynamic Schedule
    schedule.every(RUN_EVERY_MINUTES).minutes.do(job)

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()