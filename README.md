# Chess.com API Client

A Django REST API that provides endpoints to query the Chess.com public API and maintain a local database of player games and ratings.

## Setup

### Local Development
1. Make sure you have Python 3.8+ and Django installed
2. Install dependencies: `pip install -r requirements.txt`
3. Run migrations: `python manage.py migrate`
4. Start the development server: `python manage.py runserver`

### PythonAnywhere Deployment
1. Create a PythonAnywhere account and a MySQL database
2. Clone this repository to PythonAnywhere
3. Create a virtual environment: `mkvirtualenv --python=/usr/bin/python3.9 myenv`
4. Install dependencies: `pip install -r requirements.txt`
5. Install MySQL client: `pip install mysqlclient`
6. Update `settings.py` with your MySQL configuration
7. Run migrations: `python manage.py migrate`
8. Configure the Web app in PythonAnywhere dashboard

## API Endpoints

### Player Data Endpoints
- `/api/player/<username>/` - Get player profile information
- `/api/player/<username>/stats/` - Get player statistics
- `/api/player/<username>/games/` - Get player's current games
- `/api/player/<username>/games/archives/?archive=YYYY/MM` - Get player's archived games (optionally from specific year/month)

### Database Endpoints
- `/api/player/<username>/scrape-games/` - Scrape and store all games for a player in the database
  - Optional parameters: `limit` (number of archives), `only_new` (true/false)
- `/api/player/<username>/rating-history/` - Get a player's rating history from the database
  - Optional parameters: `time_class`, `year`, `month`, `aggregation`, `data_format`

### Title Endpoints
- `/api/titled/<title_abbr>/` - Get a list of players with the specified title
  - Valid titles: GM, WGM, IM, WIM, FM, WFM, NM, WNM, CM, WCM

## Examples

```
# Get a player's profile
http://localhost:8000/api/player/magnuscarlsen/

# Get player statistics
http://localhost:8000/api/player/magnuscarlsen/stats/

# Get current games
http://localhost:8000/api/player/magnuscarlsen/games/

# Get games from January 2023
http://localhost:8000/api/player/magnuscarlsen/games/archives/?archive=2023/01

# Scrape and store all games for a player
http://localhost:8000/api/player/hikaru/scrape-games/

# Scrape only unprocessed games (5 most recent archives)
http://localhost:8000/api/player/hikaru/scrape-games/?limit=5&only_new=true

# Get rating history in chart format
http://localhost:8000/api/player/hikaru/rating-history/?time_class=rapid&data_format=chart&aggregation=week

# Get all Grandmasters
http://localhost:8000/api/titled/GM/
```

## Features

- RESTful API design
- Local SQLite database for storing player games and ratings
- Rating history tracking and aggregation
- Response caching for improved performance
- Asynchronous game scraping
- Error handling with descriptive messages
- Query parameter support for flexible data retrieval
- Logging to track API requests and errors

## Database Structure

The application uses SQLite locally and can be configured to use MySQL in production:

- **players**: Stores player information and statistics
- **archives**: Tracks which monthly archives have been processed
- **games**: Stores detailed game data including PGN, ratings, results

## Dependencies

- Django and Django REST Framework
- Requests library for API calls
- SQLite (development) / MySQL (production)