# Arr Missing Content Searcher

**Created & Maintained by:** [3azmeo](https://github.com/3azmeo)

A lightweight, Dockerized Python application designed to automate the "Exhaustive Loop" search for missing content in **Sonarr** and **Radarr**.

Unlike the default behavior of *Arrs* (which might only search for missing items when they are first added or via manual trigger), this tool ensures that **every single missing item** is eventually searched for, cycled through, and re-searched after a safety period.

## Why use this?

1.  **True Exhaustive Search:** It creates a loop that goes through your entire missing list item by item. It will not re-search an item until it has finished searching for *everything else* in the list.
2.  **Anti-Ban Protection (Throttling):** Private trackers hate "Rapid Fire" API calls. This tool sleeps for a configurable amount of seconds (default: 5s) between every single search request to mimic human behavior.
3.  **Upgrade Support (Cutoff):** Can be configured to also search for items that met the cutoff but need an upgrade (e.g., you have 720p but want 1080p).
4.  **Safety Net:** If the cycle gets stuck or takes too long (e.g., > 30 days), it automatically resets the database to ensure fresh searches.

---

## Quick Start (Docker Compose)

You do not need to build anything. Just use the pre-built image.

Add this service to your `docker-compose.yml`:

```yaml
services:
  arr-missing-content:
    image: 3azmeo/arr-missing-content:latest
    container_name: arr-missing-content
    restart: unless-stopped
    networks:
      - arr-stack-network
    volumes:
      - ./data:/data
    environment:
      - LOG_LEVEL=INFO
      
      # --- Safety & Scheduling ---
      - REQUEST_DELAY_SECONDS=5        # Wait 5 seconds between each search (Vital for private trackers)
      - MAX_CYCLE_DAYS=30              # Force a full cycle reset after 30 days
      - RUN_EVERY=15                   # Run the check every 15 minutes
      
      # --- Sonarr Configuration ---
      - SONARR_URL=http://sonarr:8989
      - SONARR_API_KEY=YOUR_SONARR_API_KEY
      - SONARR_LIMIT=10                # How many episodes to search per run
      - SONARR_CUTOFF_LIMIT=0          # 0 = Disabled. Set to > 0 to search for upgrades.
      
      # --- Radarr Configuration ---
      - RADARR_URL=http://radarr:7878
      - RADARR_API_KEY=YOUR_RADARR_API_KEY
      - RADARR_LIMIT=10                # How many movies to search per run
      - RADARR_CUTOFF_LIMIT=0          # 0 = Disabled. Set to > 0 to search for upgrades.

networks:
  arr-stack-network:
    external: true
```

---

## Environment Variables Explained

### General Settings
| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOG_LEVEL` | `INFO` | Controls how much text is output to the logs (DEBUG, INFO, WARNING). |
| `RUN_EVERY` | `15` | The interval (in minutes) the script wakes up to process a batch. |
| `REQUEST_DELAY_SECONDS` | `5` | **Important:** The pause between each API call to your Arrs (and subsequently your indexers). |
| `MAX_CYCLE_DAYS` | `30` | If a full search cycle hasn't completed in X days, the database is wiped to start fresh. |

### Sonarr & Radarr Settings
| Variable | Default | Description |
| :--- | :--- | :--- |
| `SONARR_URL` | - | The internal or external URL for Sonarr. |
| `SONARR_API_KEY` | - | Your generic API Key from Sonarr Settings. |
| `SONARR_LIMIT` | `10` | Number of **episodes** to search in one batch (every 15 mins). |
| `SONARR_CUTOFF_LIMIT` | `0` | If set to `10`, it will also fetch 10 items that need **Upgrading**. Set to `0` or comment out to disable. |
| `RADARR_LIMIT` | `10` | Number of **movies** to search in one batch. |
| `RADARR_CUTOFF_LIMIT` | `0` | Same as Sonarr, for upgrading movies. |

---

## How the Logic Works

1.  **Initialization:** The script connects to a local SQLite database (`/data/history.db`) to remember what it has already searched.
2.  **Fetch:** It asks Sonarr/Radarr for a list of all *Missing* items (and *Cutoff Unmet* if enabled).
3.  **Filter:** It removes items that are already in the database (meaning they were searched recently in this cycle).
4.  **Execute:** It takes the top X items (defined by `LIMIT`) and triggers a search command for them.
5.  **Sleep:** It sleeps for `REQUEST_DELAY_SECONDS` between each item to be gentle on your indexers.
6.  **Cycle Reset:**
    * If the list of items to search becomes empty (Cycle Complete) -> Database is wiped -> Starts over.
    * If the cycle is older than `MAX_CYCLE_DAYS` -> Database is wiped -> Starts over.

---

**Author:** [3azmeo](https://github.com/3azmeo)