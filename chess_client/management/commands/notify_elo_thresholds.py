# chess_client/management/commands/realtime_notify_elo_thresholds.py
import logging
import requests
import json
import time
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.mail import EmailMessage
from chess_client.models import Player
from django.conf import settings

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check Elo thresholds for all players and send email notifications in real-time if thresholds are crossed'

    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold',
            type=int,
            default=50,
            help='Elo threshold interval (default: 50)'
        )
        parser.add_argument(
            '--test-mode',
            action='store_true',
            help='Run in test mode without sending emails'
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Check specific username only'
        )
        parser.add_argument(
            '--admin-email',
            type=str,
            default='your@email.com',
            help='Admin email to receive notifications'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5,
            help='Number of players to process in a batch before pausing (default: 5)'
        )
        parser.add_argument(
            '--batch-delay',
            type=int,
            default=3,
            help='Delay in seconds between batches to avoid rate limiting (default: 3)'
        )
        parser.add_argument(
            '--priority',
            choices=['recent', 'oldest', 'random', 'alphabetical'],
            default='recent',
            help='Player processing priority (default: recent)'
        )

    def handle(self, *args, **options):
        start_time = timezone.now()
        threshold = options.get('threshold', 50)
        test_mode = options.get('test_mode', False)
        username_filter = options.get('username')
        admin_email = options.get('admin_email')
        batch_size = options.get('batch_size', 5)
        batch_delay = options.get('batch_delay', 3)
        priority = options.get('priority', 'recent')

        self.stdout.write(self.style.SUCCESS(
            f"Starting real-time Elo threshold check with threshold={threshold} at {start_time}"
        ))

        # Filter players if username is specified
        if username_filter:
            players = Player.objects.filter(username=username_filter)
        else:
            # Order players based on priority
            if priority == 'recent':
                # Check most recently updated first (might have played new games)
                players = Player.objects.all().order_by('-last_updated')
            elif priority == 'oldest':
                # Check least recently updated first
                players = Player.objects.all().order_by('last_updated')
            elif priority == 'random':
                # Random order
                players = Player.objects.all().order_by('?')
            else:  # alphabetical
                players = Player.objects.all().order_by('username')

        total_player_count = players.count()
        self.stdout.write(f"Checking {total_player_count} players with {priority} priority")

        # Process each player
        players_processed = 0
        notifications_sent = 0
        errors = 0
        no_rating_changes = 0

        for i, player in enumerate(players):
            try:
                # Check if we should rate limit (process in batches)
                if i > 0 and i % batch_size == 0:
                    self.stdout.write(f"Processed {i}/{total_player_count} players. Pausing for {batch_delay} seconds...")
                    time.sleep(batch_delay)

                self.stdout.write(f"Processing player: {player.username}")

                # Get current ratings from Chess.com API
                current_ratings = self.fetch_current_ratings(player.username)

                if not current_ratings:
                    self.stdout.write(self.style.WARNING(
                        f"Could not fetch ratings for {player.username}"
                    ))
                    errors += 1
                    continue

                # Get last recorded ratings for player
                last_ratings = self.get_last_recorded_ratings(player)

                # Check if ratings have changed at all
                ratings_changed = self.have_ratings_changed(last_ratings, current_ratings)
                
                if not ratings_changed:
                    self.stdout.write(f"No rating changes for {player.username}")
                    no_rating_changes += 1
                    players_processed += 1
                    continue

                # Check for threshold crossings
                thresholds_crossed = self.check_thresholds(last_ratings, current_ratings, threshold)

                if thresholds_crossed:
                    self.stdout.write(self.style.SUCCESS(
                        f"Player {player.username} crossed Elo thresholds: {len(thresholds_crossed)} thresholds"
                    ))

                    # Show details of thresholds crossed
                    for crossing in thresholds_crossed:
                        game_type = crossing["game_type"].replace('chess_', '').capitalize()
                        self.stdout.write(
                            f"  • {game_type}: {crossing['last_rating']} → {crossing['current_rating']} "
                            f"({crossing['direction']}, crossed {crossing['last_threshold']} → {crossing['current_threshold']})"
                        )

                    # Send notification
                    if not test_mode:
                        success = self.send_notification(
                            player,
                            thresholds_crossed,
                            admin_email
                        )

                        if success:
                            notifications_sent += 1
                            self.stdout.write(self.style.SUCCESS(
                                f"Email notification sent for {player.username}"
                            ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"Test mode: Would send notification to {admin_email}"
                        ))
                        notifications_sent += 1
                else:
                    self.stdout.write(f"Ratings changed but no thresholds crossed for {player.username}")

                # Update stored ratings
                self.update_last_ratings(player, current_ratings)
                players_processed += 1

            except Exception as e:
                errors += 1
                logger.error(f"Error processing {player.username}: {str(e)}")
                self.stdout.write(self.style.ERROR(
                    f"Error processing {player.username}: {str(e)}"
                ))

        # Calculate elapsed time
        end_time = timezone.now()
        elapsed = end_time - start_time

        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"Real-time Elo threshold check completed in {elapsed.total_seconds():.2f} seconds\n"
            f"Players processed: {players_processed}\n"
            f"Players with no rating changes: {no_rating_changes}\n"
            f"Notifications sent: {notifications_sent}\n"
            f"Errors: {errors}"
        ))

    def have_ratings_changed(self, last_ratings, current_ratings):
        """Check if any ratings have changed"""
        # If we have no previous ratings, consider it changed
        if not last_ratings:
            return True
            
        # Check each time control
        for game_type, current_rating in current_ratings.items():
            last_rating = last_ratings.get(game_type, 0)
            if last_rating != current_rating:
                return True
                
        return False

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

    def get_last_recorded_ratings(self, player):
        """Get last recorded ratings from player's metadata or attributes"""
        # Check if player has a last_ratings field, if not create an empty dict
        if not hasattr(player, 'last_ratings') or not player.last_ratings:
            return {}

        # If player.last_ratings is a string (JSON), parse it
        if isinstance(player.last_ratings, str):
            try:
                return json.loads(player.last_ratings)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON in last_ratings for {player.username}")
                return {}

        # If it's already a dict, return it
        return player.last_ratings

    def update_last_ratings(self, player, ratings):
        """Update player's last recorded ratings and timestamp"""
        # Store as JSON string
        player.last_ratings = json.dumps(ratings)
        player.last_updated = timezone.now()
        player.save()
        logger.info(f"Updated last ratings for {player.username}: {ratings}")

    def check_thresholds(self, last_ratings, current_ratings, threshold):
        """Check if any rating has crossed a threshold"""
        thresholds_crossed = []

        for game_type, current_rating in current_ratings.items():
            # Skip if rating is 0 (indicates no real rating)
            if current_rating == 0:
                continue
                
            # Check if we have a previous rating to compare with
            last_rating = last_ratings.get(game_type, 0)
            
            # Skip if we don't have a valid previous rating to compare with
            if last_rating == 0:
                continue

            # Calculate thresholds
            last_threshold = (last_rating // threshold) * threshold
            current_threshold = (current_rating // threshold) * threshold

            # Check if threshold was crossed
            if last_threshold != current_threshold:
                direction = "increased" if current_threshold > last_threshold else "decreased"
                thresholds_crossed.append({
                    "game_type": game_type,
                    "last_rating": last_rating,
                    "current_rating": current_rating,
                    "last_threshold": last_threshold,
                    "current_threshold": current_threshold,
                    "direction": direction
                })

        return thresholds_crossed

    def send_notification(self, player, thresholds_crossed, admin_email):
        """Send email notification about threshold crossing"""
        try:
            # Build email subject
            subject = f"Chess fren Elo Alert: {player.username}"

            # Get current timestamp for the notification
            current_time = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

            # Build email body
            html_body = f"""
            <h2>Chess.com Elo Rating Threshold Alert</h2>
            <p>Player: <strong>{player.username}</strong></p>
            <p>Time: {current_time}</p>
            <p>The following rating thresholds have been crossed:</p>
            <ul>
            """

            for threshold in thresholds_crossed:
                game_type_display = threshold['game_type'].replace('chess_', '').capitalize()
                direction_color = "green" if threshold['direction'] == "increased" else "red"
                html_body += f"""
                <li>
                    <strong>{game_type_display}</strong>:
                    Rating has <span style="color: {direction_color};">{threshold['direction']}</span> 
                    from {threshold['last_rating']} to {threshold['current_rating']},
                    crossing the {threshold['last_threshold']} threshold to {threshold['current_threshold']}.
                </li>
                """

            html_body += f"""
            </ul>
            <p>Check player profile: <a href="https://www.chess.com/member/{player.username}">https://www.chess.com/member/{player.username}</a></p>
            <hr>
            <p><small>This is an automated notification from your chess frens</small></p>
            """

            # Create email
            email = EmailMessage(
                subject=subject,
                body=html_body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'chess_fren@chessfriends.xyz'),
                to=[admin_email],
            )
            email.content_subtype = "html"  # Set content type to HTML

            # Send email
            email.send(fail_silently=False)
            logger.info(f"Sent notification email to {admin_email} about {player.username}")

            return True

        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            return False