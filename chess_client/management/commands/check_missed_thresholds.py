# chess_client/management/commands/check_missed_thresholds.py
import json
import logging
from django.core.management.base import BaseCommand
from chess_client.models import Player, Game
from django.db.models import Max
from django.utils import timezone
from django.conf import settings
from datetime import datetime

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check for missed Elo rating thresholds based on game history'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Check specific username only'
        )
        parser.add_argument(
            '--threshold',
            type=int,
            default=50,
            help='Elo threshold interval (default: 50)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Look back period in days (default: 30)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information about thresholds'
        )

    def handle(self, *args, **options):
        start_time = timezone.now()
        username_filter = options.get('username')
        threshold = options.get('threshold', 50)
        days = options.get('days', 30)
        verbose = options.get('verbose', False)

        self.stdout.write(self.style.SUCCESS(
            f"Starting missed Elo thresholds check with threshold={threshold}, days={days} at {start_time}"
        ))

        # Filter players if username is specified
        if username_filter:
            players = Player.objects.filter(username=username_filter)
        else:
            players = Player.objects.all()

        self.stdout.write(f"Checking {players.count()} players")

        # Track statistics
        players_processed = 0
        players_with_thresholds = 0
        total_thresholds_found = 0

        # Process each player
        for player in players:
            try:
                players_processed += 1
                self.stdout.write(f"Processing player: {player.username}")
                
                # Get current ratings from player.last_ratings
                current_ratings = self.get_current_ratings(player)
                
                if not current_ratings:
                    self.stdout.write(self.style.WARNING(
                        f"No current ratings found for {player.username}"
                    ))
                    continue
                
                # Get historical ratings from games
                historical_ratings = self.get_historical_ratings(player.username, days)
                
                if not historical_ratings:
                    self.stdout.write(self.style.WARNING(
                        f"No historical game data found for {player.username} in last {days} days"
                    ))
                    continue
                
                # Check for threshold crossings
                thresholds_crossed = self.check_historical_thresholds(
                    historical_ratings, current_ratings, threshold
                )
                
                if thresholds_crossed:
                    players_with_thresholds += 1
                    total_thresholds_found += len(thresholds_crossed)
                    
                    self.stdout.write(self.style.SUCCESS(
                        f"Found {len(thresholds_crossed)} threshold crossings for {player.username}"
                    ))
                    
                    if verbose:
                        for cross in thresholds_crossed:
                            self.stdout.write(
                                f"  • {cross['game_type'].replace('chess_', '').capitalize()}: "
                                f"From {cross['start_rating']} to {cross['end_rating']} "
                                f"({cross['direction']}, crossed {cross['old_threshold']} → {cross['new_threshold']})"
                            )
                else:
                    self.stdout.write(f"No threshold crossings found for {player.username}")
                
            except Exception as e:
                logger.error(f"Error processing {player.username}: {str(e)}")
                self.stdout.write(self.style.ERROR(
                    f"Error processing {player.username}: {str(e)}"
                ))

        # Calculate elapsed time
        end_time = timezone.now()
        elapsed = end_time - start_time

        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"\nMissed Elo thresholds check completed in {elapsed.total_seconds():.2f} seconds\n"
            f"Players processed: {players_processed}\n"
            f"Players with threshold crossings: {players_with_thresholds}\n"
            f"Total threshold crossings found: {total_thresholds_found}"
        ))

    def get_current_ratings(self, player):
        """Get current ratings from player's last_ratings field"""
        if not player.last_ratings:
            return {}
        
        try:
            return json.loads(player.last_ratings)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in last_ratings for {player.username}")
            return {}

    def get_historical_ratings(self, username, days):
        """Get historical ratings from game history"""
        # Calculate cutoff timestamp (days ago)
        now = datetime.now()
        cutoff_timestamp = int((now - timezone.timedelta(days=days)).timestamp())
        
        # Query games for this player in the time period
        games = Game.objects.filter(
            player__username=username,
            end_time__gte=cutoff_timestamp
        ).order_by('end_time')
        
        if not games:
            return {}
        
        # Organize ratings by game type
        ratings_by_type = {}
        
        for game in games:
            # Determine game type
            time_class = game.time_class or 'unknown'
            
            if time_class not in ratings_by_type:
                ratings_by_type[time_class] = []
            
            # Get player's rating for this game
            if game.player_rating:
                ratings_by_type[time_class].append({
                    'timestamp': game.end_time,
                    'rating': game.player_rating,
                    'date': datetime.fromtimestamp(game.end_time).strftime('%Y-%m-%d')
                })
        
        # Convert to format that matches current_ratings
        historical_ratings = {}
        for time_class, ratings in ratings_by_type.items():
            if ratings:
                # Get oldest rating
                key = f"chess_{time_class}"
                historical_ratings[key] = ratings[0]['rating']
        
        return historical_ratings

    def check_historical_thresholds(self, historical_ratings, current_ratings, threshold):
        """Check if thresholds have been crossed between historical and current ratings"""
        thresholds_crossed = []
        
        for game_type, current_rating in current_ratings.items():
            # Check if we have historical data for this game type
            if game_type not in historical_ratings:
                continue
            
            historical_rating = historical_ratings[game_type]
            
            # Skip if we don't have both ratings or they're the same
            if historical_rating == 0 or current_rating == 0 or historical_rating == current_rating:
                continue
            
            # Calculate thresholds
            historical_threshold = (historical_rating // threshold) * threshold
            current_threshold = (current_rating // threshold) * threshold
            
            # Check if threshold was crossed
            if historical_threshold != current_threshold:
                direction = "increased" if current_rating > historical_rating else "decreased"
                thresholds_crossed.append({
                    "game_type": game_type,
                    "start_rating": historical_rating,
                    "end_rating": current_rating,
                    "old_threshold": historical_threshold,
                    "new_threshold": current_threshold,
                    "direction": direction
                })
        
        return thresholds_crossed