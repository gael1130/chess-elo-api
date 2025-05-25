# chess_client/management/commands/update_chess_games.py
import logging
import requests
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from chess_client.models import Player, Game, Archive
from chess_client.views import ScrapeGamesView, ChessComAPIView

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Update chess games for all players in the database from the current month'

    def handle(self, *args, **options):
        start_time = timezone.now()
        self.stdout.write(self.style.SUCCESS(f"Starting chess game update at {start_time}"))
        
        # Get all players from the database
        players = Player.objects.all()
        self.stdout.write(f"Found {players.count()} players to update")
        
        # Get current year and month for the archive
        current_date = datetime.now()
        year = current_date.year
        month = current_date.month
        
        # We'll reuse some methods from ScrapeGamesView but won't instantiate it
        # since it's a DRF view that expects request objects
        
        success_count = 0
        failed_count = 0
        total_new_games = 0
        
        # Process each player
        for player in players:
            self.stdout.write(f"Processing player: {player.username}")
            
            try:
                # We'll pass a custom parameter to only get the current month's games
                # This uses direct API calls rather than the view methods
                success = self._process_player(player.username, year, month)
                
                if success and isinstance(success, dict):
                    success_count += 1
                    new_games = success.get('new_games', 0)
                    total_new_games += new_games
                    self.stdout.write(self.style.SUCCESS(
                        f"Successfully updated {player.username}, added {new_games} new games"
                    ))
                else:
                    failed_count += 1
                    self.stdout.write(self.style.ERROR(
                        f"Failed to update {player.username}"
                    ))
            except Exception as e:
                failed_count += 1
                logger.error(f"Error updating games for {player.username}: {str(e)}")
                self.stdout.write(self.style.ERROR(
                    f"Error updating {player.username}: {str(e)}"
                ))
        
        # Calculate elapsed time
        end_time = timezone.now()
        elapsed = end_time - start_time
        
        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"Update completed in {elapsed.total_seconds():.2f} seconds\n"
            f"Players processed: {players.count()}\n"
            f"Successful updates: {success_count}\n"
            f"Failed updates: {failed_count}\n"
            f"Total new games added: {total_new_games}"
        ))
    
    def _process_player(self, username, year, month):
        """Process a single player, getting only games from the specified year/month"""
        # Format month with leading zero if needed
        month_str = f"{month:02d}"
        year_str = str(year)
        
        try:
            # Build URL for the specific archive
            archive_url = f"https://api.chess.com/pub/player/{username}/games/{year_str}/{month_str}"
            
            # Check if we should process this archive
            if not self._should_process_archive(username, year, month):
                logger.info(f"Skipping archive for {username} ({year_str}/{month_str}) - already fully processed recently")
                return {'new_games': 0, 'status': 'skipped'}
            
            # Fetch archive data
            try:
                games_response = requests.get(
                    archive_url,
                    headers={"User-Agent": "Chess.com Game Scraper 1.0"},
                    timeout=10
                )
            except requests.RequestException as e:
                logger.error(f"Request error for {archive_url}: {str(e)}")
                return False
            
            if games_response.status_code != 200:
                logger.error(f"Error fetching games for {username} from {archive_url}: {games_response.status_code}")
                return False
                
            games_data = games_response.json()
            games = games_data.get("games", [])
            
            if not games:
                logger.info(f"No games found for {username} in {year_str}/{month_str}")
                return {'new_games': 0, 'status': 'no_games'}
            
            # Get player
            player = Player.objects.get(username=username)
            
            # Process games and save to database
            new_games_count = 0
            
            for game_data in games:
                # Convert chess.com game data to our model format
                game = self._process_game(game_data, username)
                game_uuid = game.get('game_uuid')
                
                # Skip if we already have this game
                if Game.objects.filter(game_uuid=game_uuid).exists():
                    continue
                
                # Save to database
                if self._save_game_to_db(game, player):
                    new_games_count += 1
            
            # Update player stats
            self._update_player_stats(username)
            
            # Update archive status if we have it in our database
            try:
                archive = Archive.objects.get(
                    player=player,
                    year=year,
                    month=month
                )
                archive.processed = True
                archive.processed_at = timezone.now()
                archive.save()
            except Archive.DoesNotExist:
                # Create it
                try:
                    Archive.objects.create(
                        player=player,
                        year=year,
                        month=month,
                        url=archive_url,
                        processed=True,
                        processed_at=timezone.now()
                    )
                except Exception as e:
                    logger.error(f"Error creating archive: {e}")
            
            logger.info(f"Added {new_games_count} new games for {username} from {year_str}/{month_str}")
            return {'new_games': new_games_count, 'status': 'success'}
            
        except Exception as e:
            logger.error(f"Error processing {username} for {year}/{month}: {str(e)}")
            return False
    
    def _process_game(self, game_data, username):
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

    def _save_game_to_db(self, game_dict, player_obj, is_active=False):
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

    def _update_player_stats(self, username, archives_processed=0):
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
    
    def _should_process_archive(self, username, year, month):
        """
        Determine if we should process this archive
        - Always process current month's archive
        - Skip if archive was processed less than 1 hour ago
        """
        current_date = datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        
        # Always process current month
        if year == current_year and month == current_month:
            return True
            
        try:
            # Check if we've processed this archive recently
            archive = Archive.objects.filter(
                player__username=username,
                year=year,
                month=month,
                processed=True
            ).first()
            
            if archive and archive.processed_at:
                # Skip if processed less than an hour ago
                one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
                if archive.processed_at > one_hour_ago:
                    return False
            
            return True
        except Exception as e:
            logger.error(f"Error checking archive status: {e}")
            # Process if we're not sure
            return True