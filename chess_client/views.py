# New version
# /home/kalel1130/chess-elo-api/chess-elo-api/chess_client/views.py
import requests
import logging
import json
import time
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.conf import settings
from django.db import transaction, models
from django.utils import timezone
import math

# Import your models
from chess_client.models import Player, Archive, Game, Puzzle, PuzzleAttempt, FSRSMemory, UserDailyProgress
import uuid

# Set up logging
logger = logging.getLogger(__name__)


class ChessComAPIView(APIView):
    """Base class for Chess.com API views with common functionality"""

    def handle_response(self, response, error_message="API error"):
        """Handle API response and return appropriate REST response"""
        if response.status_code == 200:
            return Response(response.json())

        logger.error(f"Chess.com API error: {response.status_code} - {response.text[:100]}...")

        if response.status_code == 429:
            return Response(
                {"error": "Rate limit exceeded, please try again later"},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        elif response.status_code == 403:
            return Response(
                {"error": "Access forbidden", "details": error_message},
                status=status.HTTP_403_FORBIDDEN
            )
        elif response.status_code == 404:
            return Response(
                {"error": error_message},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {"error": error_message, "status_code": response.status_code},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    def get_chess_api(self, url, headers=None):
        """Make request to Chess.com API with proper headers"""
        if headers is None:
            headers = {
                'User-Agent': 'Django Chess.com API Client',
                'Accept': 'application/json',
            }
        try:
            return requests.get(url, headers=headers, timeout=10)
        except requests.RequestException as e:
            logger.error(f"Request error for {url}: {str(e)}")
            return type('obj', (object,), {
                'status_code': 500,
                'text': str(e),
                'json': lambda: {'error': str(e)}
            })


class PlayerProfileView(ChessComAPIView):
    """View to retrieve Chess.com player profile information"""

    @method_decorator(cache_page(60*60))  # Cache for 1 hour
    def get(self, request, username):
        url = f"https://api.chess.com/pub/player/{username}"
        logger.info(f"Fetching player profile for {username}")
        response = self.get_chess_api(url)
        return self.handle_response(response, f"Player '{username}' not found or API error")


class PlayerStatsView(ChessComAPIView):
    """View to retrieve Chess.com player statistics"""

    @method_decorator(cache_page(60*15))  # Cache for 15 minutes
    def get(self, request, username):
        url = f"https://api.chess.com/pub/player/{username}/stats"
        logger.info(f"Fetching player stats for {username}")
        response = self.get_chess_api(url)
        return self.handle_response(response, f"Stats for player '{username}' not found or API error")


class PlayerGamesArchivesView(ChessComAPIView):
    """View to retrieve Chess.com player games archives"""

    @method_decorator(cache_page(60*5))  # Cache for 5 minutes
    def get(self, request, username):
        year_month = request.query_params.get('archive')
        logger.info(f"Fetching games for {username}, archive: {year_month}")

        # Get archives first
        archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
        archives_response = self.get_chess_api(archives_url)

        if archives_response.status_code != 200:
            return self.handle_response(
                archives_response,
                f"Player '{username}' not found or API error"
            )

        archives = archives_response.json().get('archives', [])

        # Return empty list if no archives
        if not archives:
            logger.info(f"No game archives found for {username}")
            return Response({"games": []})

        # Use specific archive if provided
        if year_month:
            try:
                # Find matching archive
                target_archive = next(
                    (a for a in archives if a.endswith(year_month)),
                    None
                )
                if not target_archive:
                    logger.warning(f"No archive found for {year_month}, using most recent")
                    target_archive = archives[-1]
            except (ValueError, IndexError):
                logger.warning(f"Error finding archive for {year_month}, using most recent")
                target_archive = archives[-1]
        else:
            # Use most recent archive
            target_archive = archives[-1]

        logger.info(f"Fetching games from archive: {target_archive}")
        games_response = self.get_chess_api(target_archive)
        return self.handle_response(
            games_response,
            f"Games for player '{username}' not found or API error"
        )


class PlayerCurrentGamesView(ChessComAPIView):
    """View to retrieve Chess.com player's current games directly"""

    @method_decorator(cache_page(60*1))  # Cache for 1 minute
    def get(self, request, username):
        logger.info(f"Fetching current games for {username}")
        url = f"https://api.chess.com/pub/player/{username}/games"
        response = self.get_chess_api(url)
        return self.handle_response(response, f"Current games for player '{username}' not found or API error")


class PlayerTitledView(ChessComAPIView):
    """View to get a list of titled players"""

    @method_decorator(cache_page(60*60*24))  # Cache for 24 hours
    def get(self, request, title_abbr):
        # Valid titles: GM, WGM, IM, WIM, FM, WFM, NM, WNM, CM, WCM
        valid_titles = ["GM", "WGM", "IM", "WIM", "FM", "WFM", "NM", "WNM", "CM", "WCM"]
        title_upper = title_abbr.upper()

        logger.info(f"Fetching titled players with title: {title_upper}")

        if title_upper not in valid_titles:
            logger.warning(f"Invalid title requested: {title_upper}")
            return Response(
                {"error": f"Invalid title. Valid titles are: {', '.join(valid_titles)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        url = f"https://api.chess.com/pub/titled/{title_upper}"
        response = self.get_chess_api(url)
        return self.handle_response(
            response,
            f"No {title_upper} players found or API error"
        )


class ScrapeGamesView(APIView):
    """View to scrape and store Chess.com games for a player"""

    def get(self, request, username):
        """Scrape games for a username and save to the database"""
        # Get query parameters for options
        limit_str = request.query_params.get('limit', None)
        only_new_str = request.query_params.get('only_new', 'false')

        # Parse parameters
        limit = int(limit_str) if limit_str and limit_str.isdigit() else None
        only_new = only_new_str.lower() in ('true', 't', 'yes', 'y', '1')

        try:
            logger.info(f"Starting game scrape for {username}")
            success = self.get_all_games(username, limit, only_new)

            if success:
                # Get player stats
                player = Player.objects.get(username=username)

                # Get win/loss record
                win_count = Game.objects.filter(
                    player__username=username,
                    white_username=username,
                    white_result='win'
                ).count() + Game.objects.filter(
                    player__username=username,
                    black_username=username,
                    black_result='win'
                ).count()

                loss_count = Game.objects.filter(
                    player__username=username,
                    white_username=username,
                    white_result='checkmated'
                ).count() + Game.objects.filter(
                    player__username=username,
                    black_username=username,
                    black_result='checkmated'
                ).count()

                draw_count = Game.objects.filter(
                    player__username=username
                ).filter(
                    white_result__in=['agreed', 'repetition', 'stalemate', '50move',
                                      'insufficient', 'timevsinsufficient']
                ).count()

                # Get time controls
                time_controls = Game.objects.filter(
                    player__username=username
                ).values('time_control').annotate(
                    count=models.Count('time_control')
                ).order_by('-count')[:5]

                # Get latest rating
                latest_game = Game.objects.filter(
                    player__username=username,
                    player_rating__isnull=False,
                    player_rating__gt=0
                ).order_by('-end_time').first()

                latest_rating = latest_game.player_rating if latest_game else None
                latest_date = datetime.fromtimestamp(latest_game.end_time).isoformat() if latest_game else None

                return Response({
                    "username": username,
                    "success": True,
                    "total_games": player.total_games,
                    "archives_processed": player.archives_processed,
                    "latest_rating": latest_rating,
                    "latest_game_date": latest_date,
                    "record": {
                        "wins": win_count,
                        "losses": loss_count,
                        "draws": draw_count
                    },
                    "most_played_time_controls": [
                        {"time_control": tc['time_control'], "count": tc['count']}
                        for tc in time_controls
                    ],
                    "scrape_details": {
                        "limit": limit,
                        "only_new": only_new
                    }
                })
            else:
                return Response({
                    "success": False,
                    "error": "Failed to scrape games. Check logs for details."
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Error scraping games for {username}: {str(e)}")
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def parse_archive_url(self, url):
        """Extract year and month from archive URL"""
        parts = url.strip('/').split('/')
        year = int(parts[-2])
        month = int(parts[-1])
        return year, month

    def save_archives_to_db(self, username, archives, player_obj):
        """Save archives list to database"""
        # Check for existing archives
        existing_urls = set(Archive.objects.filter(
            player__username=username
        ).values_list('url', flat=True))

        # Insert new archives
        new_archives = []
        for archive_url in archives:
            if archive_url not in existing_urls:
                year, month = self.parse_archive_url(archive_url)
                new_archives.append(Archive(
                    player=player_obj,
                    year=year,
                    month=month,
                    url=archive_url
                ))

        # Bulk create new archives if any
        if new_archives:
            Archive.objects.bulk_create(new_archives)

    def process_game(self, game_data, username):
        """Extract relevant fields from game data for database storage"""
        game = {}

        # Extract UUID or create one from URL if missing
        game['game_uuid'] = game_data.get('uuid', game_data.get('url', '').split('/')[-1])
        game['player_username'] = username
        game['url'] = game_data.get('url', '')
        game['pgn'] = game_data.get('pgn', '')
        game['time_control'] = game_data.get('time_control', '')
        game['end_time'] = game_data.get('end_time', 0)
        game['rated'] = game_data.get('rated', False)

        # Extract white player info
        white = game_data.get('white', {})
        game['white_username'] = white.get('username', '')
        game['white_rating'] = white.get('rating', 0)
        game['white_result'] = white.get('result', '')

        # Extract black player info
        black = game_data.get('black', {})
        game['black_username'] = black.get('username', '')
        game['black_rating'] = black.get('rating', 0)
        game['black_result'] = black.get('result', '')

        # Determine player's rating for this game
        if game['white_username'].lower() == username.lower():
            game['player_rating'] = game['white_rating']
        else:
            game['player_rating'] = game['black_rating']

        # Other game details
        game['time_class'] = game_data.get('time_class', '')
        game['eco'] = game_data.get('eco', '')

        # Extract opening name from ECO URL if available
        eco_url = game_data.get('eco_url', '')
        if eco_url:
            opening = eco_url.split('/')[-1].replace('-', ' ')
            game['opening'] = opening
        else:
            game['opening'] = ''

        # Accuracies
        accuracies = game_data.get('accuracies', {})
        game['white_accuracy'] = accuracies.get('white', 0)
        game['black_accuracy'] = accuracies.get('black', 0)

        # Final position
        game['fen'] = game_data.get('fen', '')

        return game

    def save_game_to_db(self, game_dict, player_obj, is_active=False):
        """Save a processed game to the database"""
        try:
            # Check if game already exists
            try:
                game_obj = Game.objects.get(game_uuid=game_dict['game_uuid'])
                # Update existing game
                for key, value in game_dict.items():
                    if key != 'game_uuid' and key != 'player_username':
                        setattr(game_obj, key, value)
                game_obj.is_active = is_active
                game_obj.save()
            except Game.DoesNotExist:
                # Create new game
                Game.objects.create(
                    game_uuid=game_dict['game_uuid'],
                    player=player_obj,
                    url=game_dict['url'],
                    pgn=game_dict['pgn'],
                    time_control=game_dict['time_control'],
                    end_time=game_dict['end_time'],
                    rated=game_dict['rated'],
                    white_username=game_dict['white_username'],
                    white_rating=game_dict['white_rating'],
                    white_result=game_dict['white_result'],
                    black_username=game_dict['black_username'],
                    black_rating=game_dict['black_rating'],
                    black_result=game_dict['black_result'],
                    time_class=game_dict['time_class'],
                    eco=game_dict['eco'],
                    opening=game_dict['opening'],
                    white_accuracy=game_dict['white_accuracy'],
                    black_accuracy=game_dict['black_accuracy'],
                    fen=game_dict['fen'],
                    is_active=is_active,
                    player_rating=game_dict['player_rating']
                )
            return True
        except Exception as e:
            logger.error(f"Database error: {e}")
            return False

    def update_player_stats(self, username, archives_processed=0):
        """Update player's statistics in the database"""
        try:
            player, created = Player.objects.get_or_create(username=username)

            # Get the current total number of games
            total_games = Game.objects.filter(player=player).count()

            # Get the current total number of archives processed if not provided
            if archives_processed == 0:
                archives_processed = Archive.objects.filter(
                    player=player,
                    processed=True
                ).count()

            player.last_updated = timezone.now()
            player.total_games = total_games
            player.archives_processed = archives_processed
            player.save()

            return player
        except Exception as e:
            logger.error(f"Error updating player stats: {e}")
            return None

    def mark_archive_processed(self, archive_url):
        """Mark an archive as processed in the database"""
        try:
            archive = Archive.objects.get(url=archive_url)
            archive.processed = True
            archive.processed_at = timezone.now()
            archive.save()
            return True
        except Archive.DoesNotExist:
            logger.error(f"Archive not found: {archive_url}")
            return False
        except Exception as e:
            logger.error(f"Error marking archive as processed: {e}")
            return False

    @transaction.atomic
    def get_all_games(self, username, limit=None, only_new=False):
        """
        Get all games for a username and save to database

        Args:
            username: Chess.com username
            limit: Optional limit on number of archives to process
            only_new: If True, only process archives not already in the database
        """
        logger.info(f"Fetching games for user: {username}")

        # Step 1: Get all archives
        archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
        logger.info(f"Fetching archives from: {archives_url}")

        archives_response = requests.get(
            archives_url,
            headers={"User-Agent": "Chess.com Game Scraper 1.0"}
        )

        if archives_response.status_code != 200:
            logger.error(f"Error fetching archives: {archives_response.status_code}")
            return False

        archives_data = archives_response.json()
        archives = archives_data.get("archives", [])

        if not archives:
            logger.info("No archives found")
            return False

        # Sort archives by date (newest first)
        archives.sort(reverse=True)

        # Get the most recent archive URL
        most_recent_archive = archives[0] if archives else None

        # Create or get player
        player, created = Player.objects.get_or_create(username=username)

        # Save archives to database
        self.save_archives_to_db(username, archives, player)

        # If only processing new archives, filter out already processed ones
        # BUT always include the most recent archive
        if only_new:
            processed_archive_urls = set(Archive.objects.filter(
                player=player,
                processed=True
            ).values_list('url', flat=True))

            # Create a list of unprocessed archives plus the most recent archive
            unprocessed_archives = [a for a in archives if a not in processed_archive_urls]

            # Make sure the most recent archive is included
            if most_recent_archive and most_recent_archive not in unprocessed_archives:
                unprocessed_archives.insert(0, most_recent_archive)

            logger.info(f"Found {len(unprocessed_archives)} unprocessed archives (including most recent)")
            archives_to_process = unprocessed_archives
        else:
            logger.info(f"Found {len(archives)} total archives")
            archives_to_process = archives

        # Apply limit if specified
        if limit is not None and limit > 0 and limit < len(archives_to_process):
            logger.info(f"Limiting to {limit} most recent archives")
            archives_to_process = archives_to_process[:limit]

        # Step 2: Fetch games from each archive
        total_games_added = 0
        total_archives = len(archives_to_process)
        archives_processed_count = 0

        for i, archive_url in enumerate(archives_to_process):
            logger.info(f"Fetching games from archive {i+1}/{total_archives}: {archive_url}")

            # Rate limiting to avoid hitting API limits
            if i > 0:
                time.sleep(1)  # Sleep 1 second between requests

            games_response = requests.get(
                archive_url,
                headers={"User-Agent": "Chess.com Game Scraper 1.0"}
            )

            if games_response.status_code != 200:
                logger.error(f"Error fetching games from {archive_url}: {games_response.status_code}")
                continue

            games_data = games_response.json()
            games = games_data.get("games", [])

            logger.info(f"Found {len(games)} games in archive")

            # Process and save each game to the database
            saved_count = 0
            for game_data in games:
                game = self.process_game(game_data, username)
                if self.save_game_to_db(game, player):
                    saved_count += 1

            total_games_added += saved_count
            logger.info(f"Saved {saved_count} games to database")

            # Mark this archive as processed
            self.mark_archive_processed(archive_url)
            archives_processed_count += 1

        # Step 3: Add current games if there are any
        current_games_url = f"https://api.chess.com/pub/player/{username}/games"
        logger.info(f"Fetching current games from: {current_games_url}")

        current_games_response = requests.get(
            current_games_url,
            headers={"User-Agent": "Chess.com Game Scraper 1.0"}
        )

        if current_games_response.status_code == 200:
            current_games_data = current_games_response.json()
            current_games = current_games_data.get("games", [])
            logger.info(f"Found {len(current_games)} current games")

            # Save current games to database
            active_games_count = 0
            for game_data in current_games:
                game = self.process_game(game_data, username)
                if self.save_game_to_db(game, player, is_active=True):
                    active_games_count += 1

            total_games_added += active_games_count
            logger.info(f"Saved {active_games_count} active games to database")
        else:
            logger.error(f"Error fetching current games: {current_games_response.status_code}")

        # Update player stats
        self.update_player_stats(username, archives_processed_count)

        logger.info(f"Successfully saved {total_games_added} games to database for {username}")
        return True


class PlayerRatingHistoryView(APIView):
    """View to retrieve a player's rating history from the database"""

    def get(self, request, username):
        # Get query parameters for filtering
        time_class = request.query_params.get('time_class', None)
        year = request.query_params.get('year', None)
        month = request.query_params.get('month', None)
        aggregation = request.query_params.get('aggregation', 'game')  # 'game', 'day', 'week', 'month'
        # Use data_format instead of format to avoid conflicts with Django's built-in format parameter
        format_type = request.query_params.get('data_format', 'detailed')  # 'detailed', 'simple', 'chart'

        try:
            # Build the base query
            games_query = Game.objects.filter(player__username=username)

            # Add filters if specified
            if time_class:
                games_query = games_query.filter(time_class=time_class)

            if year:
                # Instead of using Django's extract functions, filter in Python
                # This is more reliable across different database backends
                games_query = games_query.filter(player_rating__isnull=False, player_rating__gt=0)

            # Order by end_time
            games_query = games_query.order_by('end_time')

            # Execute the query
            games = list(games_query)

            # Apply year/month filtering in Python if needed
            if year:
                year = int(year)
                filtered_games = []
                for game in games:
                    game_date = datetime.fromtimestamp(game.end_time)
                    if game_date.year == year:
                        if month:
                            if game_date.month == int(month):
                                filtered_games.append(game)
                        else:
                            filtered_games.append(game)
                games = filtered_games

            if not games:
                return Response({"error": f"No rating data found for player '{username}' with the specified filters"},
                               status=status.HTTP_404_NOT_FOUND)

            # Process the data based on aggregation type
            if aggregation == 'game':
                # No aggregation, return all games
                games_data = self._process_games(games, format_type, username)
            else:
                # Aggregate by time period
                games_data = self._aggregate_ratings(games, aggregation, format_type, username)

            # Get available time classes for this player
            time_classes = Game.objects.filter(
                player__username=username
            ).values_list('time_class', flat=True).distinct().order_by('time_class')

            # Get player's highest and lowest ratings
            from django.db.models import Max, Min

            rating_range = Game.objects.filter(
                player__username=username,
                player_rating__isnull=False,
                player_rating__gt=0
            ).aggregate(
                max_rating=Max('player_rating'),
                min_rating=Min('player_rating')
            )

            # Get yearly data for the player - do this in Python instead of database
            all_games = Game.objects.filter(player__username=username).order_by('end_time')

            # Group by year
            years_data = {}
            for game in all_games:
                game_date = datetime.fromtimestamp(game.end_time)
                year_str = str(game_date.year)
                if year_str not in years_data:
                    years_data[year_str] = 0
                years_data[year_str] += 1

            # Format years data
            years_data_formatted = [
                {"year": year, "games": count}
                for year, count in sorted(years_data.items())
            ]

            # Build the response
            response_data = {
                "username": username,
                "total_games": len(games),
                "max_rating": rating_range['max_rating'] or "unknown",
                "min_rating": rating_range['min_rating'] or "unknown",
                "available_time_classes": list(time_classes),
                "available_years": years_data_formatted
            }

            # Add data based on format type
            if format_type == 'detailed':
                response_data["games"] = games_data
            elif format_type == 'simple':
                response_data["ratings"] = games_data
            elif format_type == 'chart':
                response_data["chart_data"] = games_data

            return Response(response_data)

        except Exception as e:
            logger.error(f"Database error: {e}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _process_games(self, games, format_type='detailed', username=None):
        """Process games into the required output format"""
        result = []

        for game in games:
            game_date = datetime.fromtimestamp(game.end_time)

            # Get the correct player rating based on which color the player played
            player_rating = 0
            if game.white_username.lower() == username.lower():
                player_rating = game.white_rating
            elif game.black_username.lower() == username.lower():
                player_rating = game.black_rating
            else:
                # Fallback to the stored player_rating field if color-specific rating is unavailable
                player_rating = game.player_rating

            # Determine result
            if (game.white_username.lower() == username.lower() and game.white_result == 'win') or \
               (game.black_username.lower() == username.lower() and game.black_result == 'win'):
                result_str = 'win'
            elif game.white_result in ['agreed', 'repetition', 'stalemate', '50move',
                                     'insufficient', 'timevsinsufficient']:
                result_str = 'draw'
            else:
                result_str = 'loss'

            if format_type == 'detailed':
                # Detailed format with all game information
                result.append({
                    "game_id": game.game_uuid,
                    "url": game.url,
                    "rating": player_rating,
                    "date": game_date.isoformat(),
                    "timestamp": game.end_time,
                    "time_class": game.time_class,
                    "time_control": game.time_control,
                    "result": result_str,
                    "white": game.white_username,
                    "black": game.black_username
                })
            elif format_type == 'simple':
                # Simple format with just essential data
                result.append({
                    "rating": player_rating,
                    "date": game_date.isoformat(),
                    "timestamp": game.end_time,
                    "result": result_str
                })
            elif format_type == 'chart':
                # Format for charts (e.g., date and rating only)
                result.append({
                    "x": game_date.isoformat(),  # x-axis (date)
                    "y": player_rating,          # y-axis (rating)
                    "result": result_str         # For coloring points
                })

        return result

    def _aggregate_ratings(self, games, aggregation, format_type, username=None):
        """Aggregate ratings by the specified time period"""
        result = []

        # Group games by the aggregation period
        grouped_games = {}

        for game in games:
            game_date = datetime.fromtimestamp(game.end_time)

            # Get the correct player rating based on which color the player played
            player_rating = 0
            if game.white_username.lower() == username.lower():
                player_rating = game.white_rating
            elif game.black_username.lower() == username.lower():
                player_rating = game.black_rating
            else:
                # Fallback to the stored player_rating field if color-specific rating is unavailable
                player_rating = game.player_rating

            if aggregation == 'day':
                # Group by day
                key = game_date.strftime('%Y-%m-%d')
            elif aggregation == 'week':
                # Group by week (ISO week date format)
                key = game_date.strftime('%Y-W%W')
            elif aggregation == 'month':
                # Group by month
                key = game_date.strftime('%Y-%m')
            else:
                # Default to no aggregation
                key = str(game.end_time)  # Use timestamp as key

            if key not in grouped_games:
                grouped_games[key] = {
                    'ratings': [],
                    'timestamps': [],
                    'results': {'win': 0, 'loss': 0, 'draw': 0},
                    'time_classes': set()
                }

            # Determine result
            if (game.white_username.lower() == username.lower() and game.white_result == 'win') or \
               (game.black_username.lower() == username.lower() and game.black_result == 'win'):
                result_str = 'win'
            elif game.white_result in ['agreed', 'repetition', 'stalemate', '50move',
                                     'insufficient', 'timevsinsufficient']:
                result_str = 'draw'
            else:
                result_str = 'loss'

            # Only include valid ratings in calculations
            if player_rating is not None and player_rating > 0:
                grouped_games[key]['ratings'].append(player_rating)

            grouped_games[key]['timestamps'].append(game.end_time)
            grouped_games[key]['results'][result_str] += 1

            # Handle NULL time_class
            if game.time_class is not None:
                grouped_games[key]['time_classes'].add(game.time_class)

        # Process grouped data
        for key, data in sorted(grouped_games.items()):
            # Calculate average rating for the period if there are valid ratings
            if data['ratings']:
                avg_rating = sum(data['ratings']) / len(data['ratings'])
            else:
                avg_rating = None
            # Get latest timestamp in the group
            latest_timestamp = max(data['timestamps'])
            latest_date = datetime.fromtimestamp(latest_timestamp)

            if format_type == 'detailed':
                result_item = {
                    "period": key,
                    "rating": round(avg_rating) if avg_rating is not None else "unknown",
                    "date": latest_date.isoformat(),
                    "timestamp": latest_timestamp,
                    "games_count": len(data['ratings']),
                    "win_count": data['results']['win'],
                    "loss_count": data['results']['loss'],
                    "draw_count": data['results']['draw'],
                    "time_classes": list(data['time_classes'])
                }
                result.append(result_item)
            elif format_type == 'simple':
                result_item = {
                    "period": key,
                    "rating": round(avg_rating) if avg_rating is not None else "unknown",
                    "date": latest_date.isoformat(),
                    "games_count": len(data['ratings'])
                }
                result.append(result_item)
            elif format_type == 'chart':
                result_item = {
                    "x": key,  # x-axis (period)
                    "y": round(avg_rating) if avg_rating is not None else None,  # y-axis (rating)
                    "games_count": len(data['ratings'])
                }
                result.append(result_item)

        return result


# To access Puzzles
class PlayerPuzzlesView(APIView):
    """View to retrieve Chess puzzles for a player"""

    @method_decorator(cache_page(60*5))  # Cache for 5 minutes
    def get(self, request, username):
        # Get query parameters for filtering
        themes = request.query_params.get('themes', None)
        rating_min = request.query_params.get('rating_min', None)
        rating_max = request.query_params.get('rating_max', None)
        limit = request.query_params.get('limit', None)
        page = request.query_params.get('page', 1)
        page_size = request.query_params.get('page_size', 25)  # Default to 25 per page

        try:
            logger.info(f"Fetching puzzles for {username}")

            # Build the base query
            puzzles_query = Puzzle.objects.filter(player_username=username)

            # Add filters if specified
            if themes:
                theme_list = themes.split(',')
                # Filter puzzles that contain any of the specified themes
                # Since themes is stored as JSON, we need special handling
                for theme in theme_list:
                    # Using the __contains lookup for JSONB field
                    puzzles_query = puzzles_query.filter(themes__contains=theme)

            if rating_min:
                puzzles_query = puzzles_query.filter(rating__gte=int(rating_min))

            if rating_max:
                puzzles_query = puzzles_query.filter(rating__lte=int(rating_max))

            # Get total count before applying limit/pagination
            total_count = puzzles_query.count()

            # Order by rating
            puzzles_query = puzzles_query.order_by('-rating')

            # Apply limit if specified, otherwise use pagination
            if limit:
                puzzles = list(puzzles_query[:int(limit)])
            else:
                # Apply pagination
                page = int(page)
                page_size = int(page_size)
                start = (page - 1) * page_size
                end = start + page_size
                puzzles = list(puzzles_query[start:end])

            if not puzzles:
                return Response(
                    {"error": f"No puzzles found for player '{username}' with the specified filters"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Format the puzzle data for the response
            puzzles_data = []
            for puzzle in puzzles:
                # Parse JSON fields
                try:
                    solution = json.loads(puzzle.solution) if isinstance(puzzle.solution, str) else puzzle.solution
                    themes_list = json.loads(puzzle.themes) if isinstance(puzzle.themes, str) else puzzle.themes
                except json.JSONDecodeError:
                    logger.error(f"Error decoding JSON for puzzle {puzzle.id}")
                    solution = puzzle.solution
                    themes_list = puzzle.themes

                puzzles_data.append({
                    "id": puzzle.id,
                    "player_username": username,
                    "opponent_username": puzzle.opponent_username,
                    "game_date": puzzle.game_date.isoformat(),
                    "player_color": puzzle.player_color,
                    "start_fen": puzzle.start_fen,
                    "opponent_move_from": puzzle.opponent_move_from,
                    "opponent_move_to": puzzle.opponent_move_to,
                    "solution": solution,
                    "rating": puzzle.rating,
                    "themes": themes_list,
                    "game_url": puzzle.game_url.url
                })

            # Build the response
            response_data = {
                "username": username,
                "total_puzzles": total_count,
                "displayed_puzzles": len(puzzles),
                "puzzles": puzzles_data
            }

            # Add pagination info if not using limit
            if not limit:
                response_data["pagination"] = {
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_count + page_size - 1) // page_size
                }

            return Response(response_data)

        except Exception as e:
            logger.error(f"Database error: {e}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# add this for the puzzle attempts
# Then add both view classes
class PuzzleAttemptView(APIView):
    """View to manage chess puzzle attempts by players"""

    def get(self, request):
        """Retrieve puzzle attempt history"""
        player_username = request.query_params.get('player_username')
        puzzle_id = request.query_params.get('puzzle_id')

        # Input validation
        if not player_username and not puzzle_id:
            return Response(
                {"error": "Either player_username or puzzle_id parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Build query based on parameters
            query = PuzzleAttempt.objects.all()

            if player_username:
                query = query.filter(player_id=player_username)

            if puzzle_id:
                query = query.filter(puzzle_id=puzzle_id)

            # Order by most recent first
            attempts = query.order_by('-created_at')

            # Format results
            results = []
            for attempt in attempts:
                results.append({
                    "id": attempt.id,
                    "puzzle_id": attempt.puzzle.id,
                    "player_username": attempt.player.username,
                    "attempt_number": attempt.attempt_number,
                    "tries_count": attempt.tries_count,
                    "hint_used": attempt.hint_used,
                    "solved": attempt.solved,
                    "created_at": attempt.created_at.isoformat(),
                    "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None
                })

            return Response({
                "attempts": results,
                "count": len(results)
            })

        except Exception as e:
            logger.error(f"Error retrieving puzzle attempts: {str(e)}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        """Start a new puzzle attempt"""
        puzzle_id = request.data.get('puzzle_id')
        player_username = request.data.get('player_username')

        # Validate input
        if not puzzle_id or not player_username:
            return Response(
                {"error": "puzzle_id and player_username are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Verify puzzle and player exist
            try:
                puzzle = Puzzle.objects.get(id=puzzle_id)
                player = Player.objects.get(username=player_username)
            except (Puzzle.DoesNotExist, Player.DoesNotExist) as e:
                logger.error(f"Invalid puzzle or player: {str(e)}")
                return Response(
                    {"error": "Invalid puzzle_id or player_username"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Find the highest attempt_number for this player/puzzle
            max_attempt = PuzzleAttempt.objects.filter(
                puzzle_id=puzzle_id,
                player_id=player_username
            ).aggregate(models.Max('attempt_number'))

            # Determine next attempt number
            next_attempt = 1
            if max_attempt['attempt_number__max'] is not None:
                next_attempt = max_attempt['attempt_number__max'] + 1

            # Create new attempt
            attempt = PuzzleAttempt(
                id=str(uuid.uuid4()),
                puzzle=puzzle,
                player=player,
                attempt_number=next_attempt,
                tries_count=0,
                hint_used=False,
                solved=False,
                created_at=timezone.now()
            )
            attempt.save()

            return Response({
                "id": attempt.id,
                "puzzle_id": puzzle_id,
                "player_username": player_username,
                "attempt_number": next_attempt,
                "created_at": attempt.created_at.isoformat()
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error creating puzzle attempt: {str(e)}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def put(self, request, attempt_id):
        """Update an existing puzzle attempt"""
        try:
            # Verify attempt exists
            try:
                attempt = PuzzleAttempt.objects.get(id=attempt_id)
            except PuzzleAttempt.DoesNotExist:
                return Response(
                    {"error": f"Attempt with ID {attempt_id} not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Update attempt fields if provided
            if 'tries_count' in request.data:
                attempt.tries_count = request.data['tries_count']

            if 'hint_used' in request.data:
                attempt.hint_used = request.data['hint_used']

            if 'solved' in request.data:
                attempt.solved = request.data['solved']
                # Set completed_at timestamp when solved
                if attempt.solved and not attempt.completed_at:
                    attempt.completed_at = timezone.now()

            attempt.save()

            return Response({
                "id": attempt.id,
                "puzzle_id": attempt.puzzle.id,
                "player_username": attempt.player.username,
                "attempt_number": attempt.attempt_number,
                "tries_count": attempt.tries_count,
                "hint_used": attempt.hint_used,
                "solved": attempt.solved,
                "created_at": attempt.created_at.isoformat(),
                "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None
            })

        except Exception as e:
            logger.error(f"Error updating puzzle attempt: {str(e)}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PuzzleAttemptActionView(APIView):
    """View to handle specific actions on puzzle attempts"""

    def post(self, request, attempt_id, action):
        """Record an action for a puzzle attempt"""
        if action not in ['record_try', 'use_hint', 'mark_solved']:
            return Response(
                {"error": f"Invalid action: {action}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Verify attempt exists
            try:
                attempt = PuzzleAttempt.objects.get(id=attempt_id)
            except PuzzleAttempt.DoesNotExist:
                return Response(
                    {"error": f"Attempt with ID {attempt_id} not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Perform the requested action
            if action == 'record_try':
                attempt.tries_count += 1
            elif action == 'use_hint':
                attempt.hint_used = True
            elif action == 'mark_solved':
                attempt.solved = True
                attempt.completed_at = timezone.now()

            attempt.save()

            return Response({
                "id": attempt.id,
                "puzzle_id": attempt.puzzle.id,
                "player_username": attempt.player.username,
                "attempt_number": attempt.attempt_number,
                "tries_count": attempt.tries_count,
                "hint_used": attempt.hint_used,
                "solved": attempt.solved,
                "created_at": attempt.created_at.isoformat(),
                "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
                "action_performed": action
            })

        except Exception as e:
            logger.error(f"Error performing action {action} on attempt: {str(e)}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



class FSRSMemoryService:
    """FSRS algorithm implementation for chess puzzles"""

    # FSRS parameters (optimized for spaced repetition)
    FSRS_PARAMS = {
        # Initial stability values for each rating
        'initial_stability': {
            1: 0.5,  # Again
            2: 1.2,  # Hard
            3: 3.0,  # Good
            4: 7.0   # Easy
        },
        # Stability multipliers for subsequent reviews
        'stability_multiplier': {
            1: 0.2,  # Again (lapses)
            2: 1.1,  # Hard
            3: 1.5,  # Good
            4: 2.0   # Easy
        },
        # Difficulty adjustments
        'difficulty_adjustment': {
            1: 0.1,   # Again (increase difficulty)
            2: 0.05,  # Hard (small increase)
            3: -0.05, # Good (small decrease)
            4: -0.1   # Easy (decrease difficulty)
        },
        # Target retention rate
        'desired_retention': 0.9,
        # Performance-based adjustments
        'performance_adjustment': {
            'tries_penalty': 0.1,  # Per incorrect try
            'hint_penalty': 0.2    # Penalty for using hints
        }
    }

    @staticmethod
    def calculate_retrievability(stability, elapsed_days):
        """Calculate probability of recall based on stability and time elapsed"""
        return math.exp(-elapsed_days / stability)

    @staticmethod
    def calculate_next_interval(stability, desired_retention=0.9):
        """Calculate days until next review based on stability and desired retention"""
        return -stability * math.log(desired_retention)

    @classmethod
    def update_memory(cls, memory, rating, solved, tries_count, hint_used):
        """Update memory parameters based on performance"""
        now = timezone.now()

        # Get parameter sets
        params = cls.FSRS_PARAMS

        # Calculate elapsed time since last review
        if memory.last_review_date:
            elapsed_days = (now - memory.last_review_date).total_seconds() / (24 * 3600)
            retrievability = cls.calculate_retrievability(memory.stability, elapsed_days)
        else:
            # First review, set initial values
            elapsed_days = 0
            retrievability = 1.0
            memory.stability = params['initial_stability'].get(rating, 1.0)

        # Update difficulty
        memory.difficulty += params['difficulty_adjustment'].get(rating, 0)

        # Adjust difficulty based on performance
        if tries_count > 0:
            memory.difficulty += params['performance_adjustment']['tries_penalty'] * min(tries_count, 3)

        if hint_used:
            memory.difficulty += params['performance_adjustment']['hint_penalty']

        # Clamp difficulty between 1.0 and 3.0
        memory.difficulty = max(1.0, min(3.0, memory.difficulty))

        # Update stability based on rating
        if memory.last_review_date:  # Not the first review
            if rating == 1:  # Again
                # Reset stability with penalty
                memory.stability *= params['stability_multiplier'][1]
            else:
                # Calculate stability increase with spacing effect
                stability_multiplier = params['stability_multiplier'].get(rating, 1.5)

                # Spacing effect: longer intervals lead to stronger memories
                spacing_multiplier = min(2.0, math.sqrt(elapsed_days / max(memory.stability, 0.1)))

                # Update stability
                memory.stability *= stability_multiplier * spacing_multiplier

        # Calculate next review date
        next_interval = cls.calculate_next_interval(memory.stability, params['desired_retention'])
        memory.next_review_date = now + timezone.timedelta(days=next_interval)

        # Update timestamps
        memory.last_review_date = now
        memory.updated_at = now

        # Save changes
        memory.save()

        return memory.next_review_date


class FSRSDuePuzzlesView(APIView):
    """View to retrieve puzzles due for review based on FSRS"""

    def get(self, request, username):
        """Get puzzles due for review for a user"""
        try:
            # Verify player exists
            try:
                player = Player.objects.get(username=username)
            except Player.DoesNotExist:
                return Response(
                    {"error": f"Player '{username}' not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get all memory items for this user
            memories = FSRSMemory.objects.filter(player_username=username)

            # Filter for due puzzles (where next_review_date <= now)
            now = timezone.now()
            due_puzzles = []

            for memory in memories:
                if memory.next_review_date and memory.next_review_date <= now:
                    # Calculate current retrievability
                    retrievability = memory.calculate_retrievability()

                    # Include puzzle details
                    puzzle = memory.puzzle_id

                    due_puzzles.append({
                        'memory_id': memory.id,
                        'puzzle_id': puzzle.id,
                        'retrievability': retrievability,
                        'difficulty': memory.difficulty,
                        'stability': memory.stability,
                        'last_review_date': memory.last_review_date.isoformat() if memory.last_review_date else None,
                        'puzzle_details': {
                            'rating': puzzle.rating,
                            'themes': json.loads(puzzle.themes) if isinstance(puzzle.themes, str) else puzzle.themes,
                            'player_color': puzzle.player_color,
                            'start_fen': puzzle.start_fen
                        }
                    })

            # Sort by retrievability (lowest first - most urgent)
            due_puzzles.sort(key=lambda x: x['retrievability'])

            return Response({
                'username': username,
                'due_puzzles_count': len(due_puzzles),
                'due_puzzles': due_puzzles
            })

        except Exception as e:
            logger.error(f"Error retrieving due puzzles: {str(e)}")
            return Response(
                {"error": f"Database error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class FSRSPuzzleAttemptView(APIView):
    """View to submit puzzle attempts with FSRS updates"""

    @transaction.atomic
    def post(self, request):
        """Submit a puzzle attempt and update FSRS memory"""
        try:
            # Extract data from request
            puzzle_id = request.data.get('puzzle_id')
            player_username = request.data.get('player_username')
            tries_count = int(request.data.get('tries_count', 0))
            hint_used = request.data.get('hint_used', False) in [True, 'true', 'True', '1', 1]
            solved = request.data.get('solved', False) in [True, 'true', 'True', '1', 1]
            rating = request.data.get('rating')

            if rating and isinstance(rating, str) and rating.isdigit():
                rating = int(rating)

            logger.error(f"FSRS attempt data: puzzle_id={puzzle_id}, player={player_username}, solved={solved}")

            # Input validation
            if not puzzle_id or not player_username:
                return Response(
                    {"error": "puzzle_id and player_username are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Verify puzzle and player exist
            try:
                puzzle = Puzzle.objects.get(id=puzzle_id)
                player = Player.objects.get(username=player_username)
            except (Puzzle.DoesNotExist, Player.DoesNotExist):
                return Response(
                    {"error": "Invalid puzzle_id or player_username"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Find the next attempt number
            max_attempt = PuzzleAttempt.objects.filter(
                puzzle=puzzle,
                player=player
            ).aggregate(models.Max('attempt_number'))

            next_attempt = 1
            if max_attempt['attempt_number__max'] is not None:
                next_attempt = max_attempt['attempt_number__max'] + 1

            # Create attempt record
            attempt = PuzzleAttempt(
                id=str(uuid.uuid4()),
                puzzle=puzzle,
                player=player,
                attempt_number=next_attempt,
                tries_count=tries_count,
                hint_used=hint_used,
                solved=solved,
                created_at=timezone.now()
            )

            # Auto-determine rating if not provided
            if rating is None:
                rating = attempt.determine_rating()

            attempt.rating = rating

            # If solved, set completed_at
            if solved:
                attempt.completed_at = timezone.now()

            attempt.save()

            # Update daily progress
            today = timezone.now().date()
            progress, created = UserDailyProgress.objects.get_or_create(
                player=player,
                date=today
            )

            # Check if this is a new puzzle (first attempt)
            is_first_attempt = next_attempt == 1

            if is_first_attempt:
                progress.new_puzzles_seen += 1
            else:
                progress.reviews_done += 1

            progress.save()

            # Create or update FSRS memory
            try:
                # Let's see what's happening with direct field names
                logger.error(f"Looking for memory with puzzle_id={puzzle.id} and player_username={player.username}")

                # Try filter first to see what happens
                memory_query = FSRSMemory.objects.filter(puzzle_id=puzzle, player_username=player)
                logger.error(f"Found {memory_query.count()} memory records")

                memory = memory_query.first()
                if not memory:
                    # Create new memory record
                    logger.error("No memory found, creating a new record")
                    now = timezone.now()
                    memory = FSRSMemory(
                        id=str(uuid.uuid4()),
                        puzzle_id=puzzle,
                        player_username=player,
                        difficulty=2.0,
                        stability=0.5,
                        created_at=now,
                        updated_at=now
                    )
                    # Save the new memory record before updating
                    memory.save()
            except Exception as mem_error:
                # Log the specific memory-related error
                logger.error(f"Memory retrieval/creation error: {str(mem_error)}")
                import traceback
                logger.error(traceback.format_exc())

                # Create new memory record as fallback
                now = timezone.now()
                memory = FSRSMemory(
                    id=str(uuid.uuid4()),
                    puzzle_id=puzzle,
                    player_username=player,
                    difficulty=2.0,
                    stability=0.5,
                    created_at=now,
                    updated_at=now
                )
                memory.save()

            try:
                # Update memory parameters with FSRS algorithm
                next_review_date = FSRSMemoryService.update_memory(
                    memory, rating, solved, tries_count, hint_used
                )

                # Calculate current retrievability
                retrievability = memory.calculate_retrievability()
            except Exception as algo_error:
                logger.error(f"FSRS algorithm error: {str(algo_error)}")
                logger.error(traceback.format_exc())

                # Provide default values if algorithm fails
                next_review_date = timezone.now() + timezone.timedelta(days=1)
                retrievability = 1.0

            return Response({
                "success": True,
                "attempt_id": attempt.id,
                "puzzle_id": puzzle_id,
                "rating": rating,
                "fsrs_status": {
                    "difficulty": round(memory.difficulty, 2),
                    "stability": round(memory.stability, 2),
                    "retrievability": round(retrievability, 2),
                    "next_review_date": next_review_date.isoformat() if next_review_date else None
                },
                "daily_progress": {
                    "new_puzzles_seen": progress.new_puzzles_seen,
                    "reviews_done": progress.reviews_done,
                    "total_done": progress.new_puzzles_seen + progress.reviews_done
                }
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Log the specific exception with traceback
            import traceback
            logger.error(f"FSRS error: {str(e)}")
            logger.error(traceback.format_exc())
            return Response(
                {"error": f"Server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



class FSRSDiagnosticView(APIView):
    """Diagnostic view to identify issues"""

    def post(self, request):
        try:
            logger.error("Diagnostic endpoint called")

            # Check basic model access
            logger.error(f"Found {FSRSMemory._meta.db_table} table with fields: {[f.name for f in FSRSMemory._meta.fields]}")

            # Try a simple query
            memory_count = FSRSMemory.objects.count()
            logger.error(f"Total FSRSMemory records: {memory_count}")

            # Return success
            return Response({
                "success": True,
                "message": "Diagnostic passed",
                "memory_count": memory_count
            })
        except Exception as e:
            logger.error(f"Diagnostic error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Add this to views.py

class DailyPuzzlesView(APIView):
    """View to get a daily mix of new and review puzzles for a user"""

    def get(self, request, username):
        try:
            # Verify player exists
            try:
                player = Player.objects.get(username=username)
            except Player.DoesNotExist:
                return Response(
                    {"error": f"Player '{username}' not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Get or create today's progress
            today = timezone.now().date()
            progress, created = UserDailyProgress.objects.get_or_create(
                player=player,
                date=today
            )

            # Define limits
            new_limit = int(request.query_params.get('new_limit', 25))
            total_limit = int(request.query_params.get('total_limit', 50))
            per_page = int(request.query_params.get('per_page', 10))

            # Check if daily limits are reached
            new_remaining = max(0, new_limit - progress.new_puzzles_seen)
            total_remaining = max(0, total_limit - progress.total_puzzles_done)

            if total_remaining <= 0:
                return Response({
                    "username": username,
                    "message": "You've reached your daily puzzle limit!",
                    "progress": {
                        "new_puzzles_seen": progress.new_puzzles_seen,
                        "reviews_done": progress.reviews_done,
                        "total_done": progress.total_puzzles_done,
                        "new_limit": new_limit,
                        "total_limit": total_limit
                    },
                    "puzzles": []
                })

            # Step 1: Get puzzles due for review
            now = timezone.now()
            due_memories = FSRSMemory.objects.filter(
                player_username=username,
                next_review_date__lte=now
            ).order_by('next_review_date')

            # Get puzzle IDs from memories
            due_puzzle_ids = [memory.puzzle_id_id for memory in due_memories]
            due_puzzles = list(Puzzle.objects.filter(id__in=due_puzzle_ids))

            # Step 2: Get new puzzles (puzzles without attempts by this user)
            attempted_puzzle_ids = PuzzleAttempt.objects.filter(
                player_id=username
            ).values_list('puzzle_id', flat=True).distinct()

            new_puzzles_query = Puzzle.objects.filter(
                player_username=username
            ).exclude(
                id__in=attempted_puzzle_ids
            ).order_by('?')  # Random order for new puzzles

            # Limit new puzzles based on new_remaining
            new_puzzles = list(new_puzzles_query[:new_remaining])

            # Step 3: Combine due reviews and new puzzles, prioritizing reviews
            combined_puzzles = []
            reviews_to_use = min(len(due_puzzles), total_remaining)

            # Add due reviews first
            combined_puzzles.extend(due_puzzles[:reviews_to_use])

            # If we have room for new puzzles, add them
            new_to_use = min(len(new_puzzles), total_remaining - reviews_to_use)
            combined_puzzles.extend(new_puzzles[:new_to_use])

            # Format the puzzles for the response
            puzzles_data = []
            for puzzle in combined_puzzles:
                # Determine if puzzle is new or review
                is_new = puzzle not in due_puzzles

                # Parse JSON fields
                try:
                    solution = json.loads(puzzle.solution) if isinstance(puzzle.solution, str) else puzzle.solution
                    themes_list = json.loads(puzzle.themes) if isinstance(puzzle.themes, str) else puzzle.themes
                except json.JSONDecodeError:
                    logger.error(f"Error decoding JSON for puzzle {puzzle.id}")
                    solution = puzzle.solution
                    themes_list = puzzle.themes

                puzzles_data.append({
                    "id": puzzle.id,
                    "player_username": username,
                    "opponent_username": puzzle.opponent_username,
                    "game_date": puzzle.game_date.isoformat(),
                    "player_color": puzzle.player_color,
                    "start_fen": puzzle.start_fen,
                    "opponent_move_from": puzzle.opponent_move_from,
                    "opponent_move_to": puzzle.opponent_move_to,
                    "solution": solution,
                    "rating": puzzle.rating,
                    "themes": themes_list,
                    "game_url": puzzle.game_url.url,
                    "is_new": is_new
                })

            # Return the response
            return Response({
                "username": username,
                "progress": {
                    "new_puzzles_seen": progress.new_puzzles_seen,
                    "reviews_done": progress.reviews_done,
                    "total_done": progress.total_puzzles_done,
                    "new_remaining": new_remaining,
                    "reviews_remaining": total_remaining - new_remaining,
                    "total_remaining": total_remaining,
                    "new_limit": new_limit,
                    "total_limit": total_limit
                },
                "puzzles_count": len(puzzles_data),
                "puzzles": puzzles_data[:per_page]  # Paginate results
            })

        except Exception as e:
            logger.error(f"Error retrieving daily puzzles: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return Response(
                {"error": f"Server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Add this to views.py

class ResetDailyProgressView(APIView):
    """Reset daily puzzle progress for a user"""

    def post(self, request, username):
        try:
            # Verify player exists
            try:
                player = Player.objects.get(username=username)
            except Player.DoesNotExist:
                return Response(
                    {"error": f"Player '{username}' not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Delete today's progress
            today = timezone.now().date()
            UserDailyProgress.objects.filter(
                player=player,
                date=today
            ).delete()

            return Response({
                "success": True,
                "message": f"Daily progress for {username} has been reset"
            })

        except Exception as e:
            logger.error(f"Error resetting daily progress: {str(e)}")
            return Response(
                {"error": f"Server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# Add a simple home/docs view
class APIDocsView(APIView):
    """View that shows API documentation"""
    def get(self, request):
        return Response({
            "api_name": "Chess.com API Client",
            "version": "1.1",
            "endpoints": {
                "player_profile": "/api/player/{username}/",
                "player_stats": "/api/player/{username}/stats/",
                "player_current_games": "/api/player/{username}/games/",
                "player_games_archives": "/api/player/{username}/games/archives/?archive=YYYY/MM",
                "player_rating_history": {
                    "base_url": "/api/player/{username}/rating-history/",
                    "parameters": {
                        "time_class": "Filter by time class (e.g. bullet, blitz, rapid, daily)",
                        "year": "Filter by year (e.g. 2023)",
                        "month": "Filter by month (1-12, requires year parameter)",
                        "aggregation": "Aggregate data by [game, day, week, month]",
                        "data_format": "Response format [detailed, simple, chart]"
                    },
                    "example": "/api/player/kalel1130/rating-history/?time_class=rapid&year=2024&data_format=chart&aggregation=week"
                },
                "titled_players": "/api/titled/{title_abbr}/",
                "scrape_games": {
                    "base_url": "/api/player/{username}/scrape-games/",
                    "description": "Scrape and store all games for a player",
                    "parameters": {
                        "limit": "Optional: Limit the number of archives to process",
                        "only_new": "Optional: Only process new archives (true/false)"
                    },
                    "example": "/api/player/hikaru/scrape-games/?limit=5&only_new=true"
                },
                "player_puzzles": {
                    "base_url": "/api/player/{username}/puzzles/",
                    "description": "Retrieve puzzles generated from a player's games",
                    "parameters": {
                        "themes": "Optional: Filter by comma-separated themes (e.g. tactical,capture,queen)",
                        "rating_min": "Optional: Minimum puzzle rating",
                        "rating_max": "Optional: Maximum puzzle rating",
                        "limit": "Optional: Limit the number of puzzles returned (overrides pagination)",
                        "page": "Optional: Page number for pagination (default: 1)",
                        "page_size": "Optional: Number of puzzles per page (default: 25)"
                    },
                    "example": "/api/player/kalel1130/puzzles/?themes=tactical,knight&rating_min=950&page=1&page_size=50"
                },
                "daily_puzzles": {
                    "base_url": "/api/player/{username}/daily-puzzles/",
                    "description": "Get a daily mix of new puzzles and reviews with Anki-like spaced repetition",
                    "methods": {
                        "GET": {
                            "description": "Retrieve daily puzzles with respect to limits and scheduling",
                            "parameters": {
                                "new_limit": "Optional: Maximum number of new puzzles per day (default: 25)",
                                "total_limit": "Optional: Maximum total puzzles per day (default: 50)",
                                "per_page": "Optional: Number of puzzles to return per request (default: 10)"
                            },
                            "examples": [
                                "/api/player/kalel1130/daily-puzzles/",
                                "/api/player/kalel1130/daily-puzzles/?new_limit=10&total_limit=30&per_page=5"
                            ]
                        }
                    },
                    "response": {
                        "username": "Player's username",
                        "progress": "Daily progress tracking information",
                        "puzzles_count": "Total number of puzzles available for today",
                        "puzzles": "List of puzzle objects, with is_new flag indicating new vs review"
                    }
                },
                "reset_daily_progress": {
                    "base_url": "/api/player/{username}/reset-daily-progress/",
                    "description": "Reset a player's daily puzzle progress",
                    "methods": {
                        "POST": {
                            "description": "Reset the daily counters and limits for the current day",
                            "examples": [
                                "/api/player/kalel1130/reset-daily-progress/"
                            ]
                        }
                    },
                    "response": {
                        "success": "Boolean indicating if the reset was successful",
                        "message": "Confirmation message"
                    }
                },
                "puzzle_attempts": {
                    "base_url": "/api/puzzles/attempts/",
                    "description": "Manage puzzle attempt tracking for players",
                    "methods": {
                        "GET": {
                            "description": "Retrieve puzzle attempt history",
                            "parameters": {
                                "player_username": "Filter attempts by player username",
                                "puzzle_id": "Filter attempts for a specific puzzle"
                            },
                            "examples": [
                                "/api/puzzles/attempts/?player_username=kalel1130",
                                "/api/puzzles/attempts/?puzzle_id=0226896e-04f1-4c13-b8d4-a026bacedf73"
                            ]
                        },
                        "POST": {
                            "description": "Start a new puzzle attempt",
                            "required_fields": {
                                "puzzle_id": "ID of the puzzle being attempted",
                                "player_username": "Username of the player making the attempt"
                            },
                            "example_request": {
                                "puzzle_id": "0226896e-04f1-4c13-b8d4-a026bacedf73",
                                "player_username": "kalel1130"
                            }
                        }
                    }
                },
                "puzzle_attempt_detail": {
                    "base_url": "/api/puzzles/attempts/{attempt_id}/",
                    "description": "Manage a specific puzzle attempt",
                    "methods": {
                        "PUT": {
                            "description": "Update an existing puzzle attempt",
                            "optional_fields": {
                                "tries_count": "Number of incorrect moves before solving",
                                "hint_used": "Boolean flag indicating if a hint was used (true/false)",
                                "solved": "Boolean flag indicating if the puzzle was solved (true/false)"
                            },
                            "example_request": {
                                "tries_count": 3,
                                "hint_used": True,
                                "solved": True
                            }
                        }
                    }
                },
                "puzzle_attempt_action": {
                    "base_url": "/api/puzzles/attempts/{attempt_id}/{action}/",
                    "description": "Perform a specific action on a puzzle attempt",
                    "actions": {
                        "record_try": "Increment the tries_count for an attempt",
                        "use_hint": "Mark that a hint was used for this attempt",
                        "mark_solved": "Mark the puzzle as solved and record completion time"
                    },
                    "examples": [
                        "/api/puzzles/attempts/3a7c53e9-f835-4a7c-9d20-feac6d199e2b/record_try/",
                        "/api/puzzles/attempts/3a7c53e9-f835-4a7c-9d20-feac6d199e2b/use_hint/",
                        "/api/puzzles/attempts/3a7c53e9-f835-4a7c-9d20-feac6d199e2b/mark_solved/"
                    ]
                },
                "fsrs_due_puzzles": {
                    "base_url": "/api/player/{username}/due-puzzles/",
                    "description": "Get puzzles due for review based on FSRS spaced repetition algorithm",
                    "methods": {
                        "GET": {
                            "description": "Retrieve a list of puzzles due for review, sorted by urgency (lowest retrievability first)",
                            "examples": [
                                "/api/player/kalel1130/due-puzzles/"
                            ]
                        }
                    },
                    "response": {
                        "due_puzzles_count": "Number of puzzles due for review",
                        "due_puzzles": "List of puzzles with memory parameters and retrievability"
                    }
                },
                "fsrs_puzzle_attempt": {
                    "base_url": "/api/puzzles/attempts/fsrs/",
                    "description": "Submit a puzzle attempt and update FSRS memory parameters",
                    "methods": {
                        "POST": {
                            "description": "Record a puzzle attempt with FSRS parameters",
                            "required_fields": {
                                "puzzle_id": "ID of the puzzle being attempted",
                                "player_username": "Username of the player making the attempt"
                            },
                            "optional_fields": {
                                "tries_count": "Number of incorrect moves before solving (default: 0)",
                                "hint_used": "Boolean flag indicating if a hint was used (default: false)",
                                "solved": "Boolean flag indicating if the puzzle was solved (default: false)",
                                "rating": "FSRS rating (1=Again, 2=Hard, 3=Good, 4=Easy). If omitted, determined automatically."
                            },
                            "example_request": {
                                "puzzle_id": "0226896e-04f1-4c13-b8d4-a026bacedf73",
                                "player_username": "kalel1130",
                                "tries_count": 2,
                                "hint_used": False,
                                "solved": True
                            }
                        }
                    },
                    "response": {
                        "success": "Boolean indicating if the attempt was recorded successfully",
                        "attempt_id": "Unique identifier for the created attempt",
                        "fsrs_status": {
                            "difficulty": "Current difficulty parameter (1.0-3.0)",
                            "stability": "Current stability value in days",
                            "retrievability": "Current probability of recall (0.0-1.0)",
                            "next_review_date": "Scheduled date for next review"
                        },
                        "daily_progress": {
                            "new_puzzles_seen": "Number of new puzzles seen today",
                            "reviews_done": "Number of review puzzles completed today",
                            "total_done": "Total puzzles completed today"
                        }
                    }
                }
            },
            "documentation": "See README.md for details"
        })