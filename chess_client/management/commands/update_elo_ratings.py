# chess_client/management/commands/update_elo_ratings.py
import json
import logging
import requests
import time
from django.core.management.base import BaseCommand
from chess_client.models import Player
from django.utils import timezone

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Update last Elo ratings for players in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Update specific username only'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Update all players, not just those with missing ratings'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of players to process in a batch before pausing'
        )
        parser.add_argument(
            '--batch-delay',
            type=int,
            default=5,
            help='Delay in seconds between batches to avoid rate limiting'
        )

    def handle(self, *args, **options):
        start_time = timezone.now()
        username_filter = options.get('username')
        update_all = options.get('all', False)
        batch_size = options.get('batch_size', 10)
        batch_delay = options.get('batch_delay', 5)

        self.stdout.write(self.style.SUCCESS(
            f"Starting Elo ratings update at {start_time}"
        ))

        # Determine which players to update
        if username_filter:
            players = Player.objects.filter(username=username_filter)
        elif not update_all:
            # Get players with missing ratings
            players = []
            for player in Player.objects.all():
                try:
                    ratings = json.loads(player.last_ratings) if player.last_ratings else {}
                    if not ratings:
                        players.append(player)
                except json.JSONDecodeError:
                    players.append(player)
            self.stdout.write(f"Found {len(players)} players with missing ratings")
        else:
            players = Player.objects.all()
            self.stdout.write(f"Updating all {players.count()} players")

        # Track statistics
        updates_succeeded = 0
        updates_failed = 0
        players_processed = 0

        # Process players in batches
        for i, player in enumerate(players):
            players_processed += 1
            
            try:
                self.stdout.write(f"Updating ratings for {player.username}...")
                
                # Fetch current ratings from Chess.com API
                current_ratings = self.fetch_current_ratings(player.username)
                
                if current_ratings:
                    # Update player's last_ratings
                    player.last_ratings = json.dumps(current_ratings)
                    player.save()
                    
                    self.stdout.write(self.style.SUCCESS(
                        f"✓ Updated ratings for {player.username}"
                    ))
                    
                    # Display the ratings
                    for game_type, rating in current_ratings.items():
                        self.stdout.write(f"  • {game_type.replace('chess_', '').capitalize()}: {rating}")
                    
                    updates_succeeded += 1
                else:
                    self.stdout.write(self.style.ERROR(
                        f"✗ Failed to fetch ratings for {player.username}"
                    ))
                    updates_failed += 1
                
                # Pause between batches to avoid rate limiting
                if (i + 1) % batch_size == 0 and i < len(players) - 1:
                    self.stdout.write(f"Processed {i + 1} players. Pausing for {batch_delay} seconds...")
                    time.sleep(batch_delay)
                
            except Exception as e:
                updates_failed += 1
                logger.error(f"Error updating {player.username}: {str(e)}")
                self.stdout.write(self.style.ERROR(
                    f"! Error updating {player.username}: {str(e)}"
                ))

        # Calculate elapsed time
        end_time = timezone.now()
        elapsed = end_time - start_time

        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"\nElo ratings update completed in {elapsed.total_seconds():.2f} seconds\n"
            f"Players processed: {players_processed}\n"
            f"Successful updates: {updates_succeeded}\n"
            f"Failed updates: {updates_failed}"
        ))

    def fetch_current_ratings(self, username):
        """Fetch current ratings from Chess.com API"""
        try:
            stats_url = f"https://api.chess.com/pub/player/{username}/stats"
            response = requests.get(
                stats_url,
                headers={"User-Agent": "Chess.com Game Scraper 1.0"},
                timeout=10
            )

            if response.status_code != 200:
                logger.error(f"API error: {response.status_code} when fetching stats for {username}")
                return None

            data = response.json()

            # Extract ratings from various game types
            ratings = {}

            # Daily chess
            if 'chess_daily' in data and 'last' in data['chess_daily']:
                ratings['chess_daily'] = data['chess_daily']['last'].get('rating', 0)

            # Rapid chess
            if 'chess_rapid' in data and 'last' in data['chess_rapid']:
                ratings['chess_rapid'] = data['chess_rapid']['last'].get('rating', 0)

            # Blitz chess
            if 'chess_blitz' in data and 'last' in data['chess_blitz']:
                ratings['chess_blitz'] = data['chess_blitz']['last'].get('rating', 0)

            # Bullet chess
            if 'chess_bullet' in data and 'last' in data['chess_bullet']:
                ratings['chess_bullet'] = data['chess_bullet']['last'].get('rating', 0)

            return ratings

        except Exception as e:
            logger.error(f"Error fetching ratings for {username}: {e}")
            return None