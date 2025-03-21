# Mindhive Full Stack Developer Assessment

This repository contains the Mindhive Full Stack Developer Assessment solution. The goal of this project is to:

- Scrape Subway outlet data from the official website (subway.com.my/find-a-subway).
- Parse and store the data in a structured PostgreSQL database (address, hours, GPS coordinates, etc.).
- Develop a backend API using FastAPI to serve the stored data and handle advanced location/time-based queries.
- Integrate an AI-driven chatbot component to interpret natural-language queries (like "Which outlets close before 9pm in Bangsar?"), then return matching store information.
- Optionally, visualize these outlets in a map-based frontend (e.g., with React + Leaflet), including 5km radius coverage circles for each store.

## Table of Contents
- [Features](#features)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Getting Started](#getting-started)
  - [1. Clone and Environment Setup](#1-clone-and-environment-setup)
  - [2. Install Dependencies](#2-install-dependencies)
  - [3. Configure Database](#3-configure-database)
  - [4. Run the Scraper](#4-run-the-scraper)
  - [5. Start the API](#5-start-the-api)
- [Usage and Endpoints](#usage-and-endpoints)
- [AI Chatbot Queries](#ai-chatbot-queries)
- [Further Notes and Improvements](#further-notes-and-improvements)
- [License](#license)

## Features
- **Web Scraping**
  - Uses Selenium to dynamically load and scrape Subway outlets (particularly in Kuala Lumpur).
  - Extracts names, addresses, operating hours, and embedded map links (Waze/Google).

- **PostgreSQL Database**
  - Normalized schema (e.g., columns for city, street_address, opening_hours in JSON).
  - Processes raw text into structured formats (24-hour or daily open/close times).
  - Handles "ON CONFLICT" constraints to avoid duplicate entries.

- **FastAPI Backend**
  - Provides REST endpoints to query stored outlet data (e.g., filter by city, time, or 24-hour flag).
  - Offers compound queries (location + time), e.g., "Which outlets in Sunway are open after 8pm?"

- **AI Chatbot Integration**
  - Optional calls to OpenAI (GPT-3.5) or fallback logic to interpret natural language queries.
  - For example: "Which store in Bangsar closes before 9pm?"
  - Returns structured JSON with matching outlet IDs and suggested map center.

- **Map Visualization (Optional)**
  - If you include a frontend (React + Leaflet, for instance), you can display store markers and a 5KM coverage circle around each store.
  - Toggles available for "Show coverage," "Highlight intersecting stores," etc.

## Project Structure
A simplified overview (actual files may vary):

```
mindhive-assessment/
├── backend/
│   ├── db.py                 # PostgreSQL connection logic
│   ├── main.py               # Scraping script with Selenium
│   ├── api.py                # FastAPI routes and query logic
│   ├── chatbot.py (example)  # AI integration (process_with_ai, etc.)
│   ├── ...
├── frontend/ (optional)      
│   ├── src/                  # React + Leaflet code for map display
│   └── ...
├── requirements.txt          # Python package dependencies
├── README.md                 # This README
└── ...
```

## Requirements
- Python 3.8+
- PostgreSQL (e.g., version 12 or higher)
- Chrome + Selenium WebDriver if scraping locally (or adjust for another driver).
- Environment Variables for DB config & OpenAI key (if you're using the chatbot).
  - OPENAI_API_KEY – for GPT queries (optional).
  - DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT – for PostgreSQL connection.
- Python Libraries (as seen in requirements.txt):
  ```
  fastapi
  uvicorn
  psycopg2-binary
  pydantic
  requests
  beautifulsoup4
  python-dotenv
  gunicorn
  selenium
  webdriver_manager
  openai  # if you want AI chatbot
  ```

## Getting Started
### 1. Clone and Environment Setup
```bash
git clone https://github.com/your-user/mindhive-assessment.git
cd mindhive-assessment
```

(Optional) create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # on macOS/Linux
venv\Scripts\activate     # on Windows
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Database
Create a PostgreSQL database, e.g. subway_db.

Set environment variables (or edit db.py) with credentials:
```
DB_NAME=subway_db
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
```

Run the included SQL snippet to create the subway_outlets table (if not done automatically in code):
```sql
CREATE TABLE IF NOT EXISTS subway_outlets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address TEXT NOT NULL,
    operating_hours TEXT,
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    waze_link TEXT,
    google_maps_link TEXT,
    street_address VARCHAR(255),
    district VARCHAR(100),
    city VARCHAR(100),
    postcode VARCHAR(10),
    opening_hours JSONB,
    is_24hours BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, address)
);
```

### 4. Run the Scraper
The main.py (or similarly named script) uses Selenium to visit the Subway site and insert data into PostgreSQL:
```bash
python backend/main.py
```

It searches "Kuala Lumpur," enumerates pages, and populates subway_outlets.

### 5. Start the API
Launch the FastAPI app (usually api.py or main.py again if it includes the server):
```bash
uvicorn api:app --reload
```

The API docs will be available at http://127.0.0.1:8000/docs.

If you integrated a chatbot endpoint, you can POST to /chatbot/query to test AI queries.

## Usage and Endpoints
Below are a few key endpoints:

- **GET /outlets**  
  Returns all outlets from the DB.

- **GET /api/outlets/search?query=<location>&time=<time>**  
  Example: .../search?query=Bangsar&time=after+10pm  
  Returns outlets in Bangsar that are open after 10pm.

- **GET /api/outlets/open-now**  
  Optional location param if you only want "open now" in a certain area.  
  Checks current local time to see which outlets are open.

- **POST /chatbot/query**  
  Expects JSON body like:
  ```json
  {
    "query": "Which outlets in Bangsar close before 9pm?"
  }
  ```
  Returns a JSON response with "answer", "related_ids", and optional "center".

Note: If you integrated a React frontend, the app might fetch /outlets or call the chatbot route, then highlight relevant IDs on a Leaflet map.

## AI Chatbot Queries
When the user types natural-language queries such as:

- "Which stores in KL open before 8am?"
- "Any 24-hour outlets in Petaling Jaya?"
- "Which store in Bangsar closes the earliest?"

The chatbot logic (via OpenAI or fallback patterns) interprets the query and returns a structured action:
```json
{
  "answer": "...",
  "action": "compound_query",
  "location": "Bangsar",
  "time": "close before 9pm"
}
```

Your API then executes the location+time filter against the DB, returning the final store list.

Make sure OPENAI_API_KEY is set if you want real GPT responses. Otherwise, a fallback logic handles partial matches.

## Further Notes and Improvements
- **Front-End Map**
  - If you have a React + Leaflet app, you can fetch store data from the /outlets endpoint and render markers.
  - Add a toggle to show/hide 5KM circles around each store to reduce clutter.

- **Handling Overlapping Circles**
  - The code can check if two outlets are within 5km and highlight them accordingly (like "intersecting store coverage").

- **Advanced Address Parsing**
  - Addresses from Subway's site can be inconsistent. We implemented regex to extract city, postcode, etc., but some edge cases remain.

- **Cross-Midnight Hours**
  - Some outlets close after midnight. We handle that by marking is_overnight if close < open. If you expand queries like "still open at 1am," you'll see special logic.

- **API Deployment**
  - For production, you might use gunicorn or uvicorn behind a reverse proxy (NGINX). See the requirements.txt for possible server packages.

## License
This project is primarily for the Mindhive Full Stack Developer Assessment. It may be distributed as part of a technical evaluation. If you need to adapt or reuse code outside of this context, please include appropriate references and comply with any relevant license terms.

Thank you! If you have any questions or suggestions, feel free to open an issue or contact the author. 
