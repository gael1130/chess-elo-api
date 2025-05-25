# chess_client/management/commands/check_elo_ratings.py
import json
import logging
from django.core.management.base import BaseCommand
from chess_client.models import Player
from django.utils import timezone

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check if players in the database have their last Elo ratings recorded'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Check specific username only'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information about ratings'
        )
        parser.add_argument(
            '--missing-only',
            action='store_true',
            help='Show only players with missing ratings'
        )

    def handle(self, *args, **options):
        start_time = timezone.now()
        username_filter = options.get('username')
        verbose = options.get('verbose', False)
        missing_only = options.get('missing_only', False)

        self.stdout.write(self.style.SUCCESS(
            f"Starting Elo ratings check at {start_time}"
        ))

        # Filter players if username is specified
        if username_filter:
            players = Player.objects.filter(username=username_filter)
        else:
            players = Player.objects.all()

        self.stdout.write(f"Checking {players.count()} players")

        # Track statistics
        players_with_ratings = 0
        players_without_ratings = 0
        players_with_invalid_data = 0

        # Process each player
        for player in players:
            try:
                # Get last recorded ratings for player
                last_ratings = self.get_last_recorded_ratings(player)
                
                has_ratings = len(last_ratings) > 0
                
                if has_ratings:
                    players_with_ratings += 1
                    if not missing_only:
                        self.stdout.write(
                            self.style.SUCCESS(f"✓ {player.username}: Has ratings")
                        )
                        if verbose:
                            self.display_ratings(player.username, last_ratings)
                else:
                    players_without_ratings += 1
                    self.stdout.write(
                        self.style.ERROR(f"✗ {player.username}: Missing ratings")
                    )
                
            except Exception as e:
                players_with_invalid_data += 1
                logger.error(f"Error checking {player.username}: {str(e)}")
                self.stdout.write(
                    self.style.ERROR(f"! {player.username}: Error - {str(e)}")
                )

        # Calculate elapsed time
        end_time = timezone.now()
        elapsed = end_time - start_time

        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"\nElo ratings check completed in {elapsed.total_seconds():.2f} seconds\n"
            f"Total players: {players.count()}\n"
            f"Players with ratings: {players_with_ratings} ({(players_with_ratings/players.count())*100 if players.count() > 0 else 0:.1f}%)\n"
            f"Players without ratings: {players_without_ratings} ({(players_without_ratings/players.count())*100 if players.count() > 0 else 0:.1f}%)\n"
            f"Players with data errors: {players_with_invalid_data}"
        ))

    def get_last_recorded_ratings(self, player):
        """Get last recorded ratings from player's metadata or attributes"""
        # Check if player has a last_ratings field, if not return an empty dict
        if not hasattr(player, 'last_ratings') or not player.last_ratings:
            return {}

        # If player.last_ratings is a string (JSON), parse it
        if isinstance(player.last_ratings, str):
            try:
                return json.loads(player.last_ratings)
            except json.JSONDecodeError:
                return {}

        # If it's already a dict, return it
        return player.last_ratings

    def display_ratings(self, username, ratings):
        """Display ratings in a formatted way"""
        self.stdout.write(f"  Ratings for {username}:")
        for game_type, rating in ratings.items():
            self.stdout.write(f"    • {game_type.replace('chess_', '').capitalize()}: {rating}")