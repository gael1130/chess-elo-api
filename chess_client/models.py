# /home/kalel1130/chess-elo-api/chess-elo-api/chess_client/models.py
from django.db import models
import uuid
import math

# Create your models here.
from django.utils import timezone

class Player(models.Model):
    username = models.CharField(max_length=100, primary_key=True)
    last_updated = models.DateTimeField(null=True, blank=True)
    total_games = models.IntegerField(default=0)
    archives_processed = models.IntegerField(default=0)

    last_ratings = models.TextField(blank=True, null=True, help_text="JSON string of last recorded ratings")

    def __str__(self):
        return self.username

class Archive(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    year = models.IntegerField()
    month = models.IntegerField()
    url = models.URLField(unique=True)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.player.username} - {self.year}/{self.month}"

class Game(models.Model):
    game_uuid = models.CharField(max_length=100, unique=True)
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    url = models.URLField(unique=True)
    pgn = models.TextField()
    time_control = models.CharField(max_length=50)
    end_time = models.BigIntegerField()
    rated = models.BooleanField()
    white_username = models.CharField(max_length=100)
    white_rating = models.IntegerField()
    white_result = models.CharField(max_length=10)
    black_username = models.CharField(max_length=100)
    black_rating = models.IntegerField()
    black_result = models.CharField(max_length=10)
    time_class = models.CharField(max_length=20)
    eco = models.CharField(max_length=10, null=True, blank=True)
    opening = models.CharField(max_length=255, null=True, blank=True)
    white_accuracy = models.FloatField(null=True, blank=True)
    black_accuracy = models.FloatField(null=True, blank=True)
    fen = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    player_rating = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.white_username} vs {self.black_username} - {self.end_time}"


# Add this to chess_client/models.py

class Puzzle(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    player_username = models.ForeignKey('Player', on_delete=models.CASCADE, to_field='username', db_column='player_username')
    opponent_username = models.CharField(max_length=100)
    game_date = models.DateField()
    player_color = models.CharField(max_length=5)
    start_fen = models.CharField(max_length=100)
    opponent_move_from = models.CharField(max_length=2)
    opponent_move_to = models.CharField(max_length=2)
    solution = models.JSONField()
    rating = models.IntegerField()
    themes = models.JSONField()
    game_url = models.ForeignKey('Game', on_delete=models.CASCADE, to_field='url', db_column='game_url')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chess_client_puzzle'
        managed = True  # Important! Tell Django that we created this table manually


class PuzzleAttempt(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    puzzle = models.ForeignKey('Puzzle', on_delete=models.CASCADE, db_column='puzzle_id')
    player = models.ForeignKey('Player', on_delete=models.CASCADE, to_field='username', db_column='player_username')
    attempt_number = models.PositiveIntegerField()
    tries_count = models.PositiveIntegerField(default=0)
    hint_used = models.BooleanField(default=False)
    solved = models.BooleanField(default=False)
    rating = models.IntegerField(null=True, blank=True)  # New field
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'chess_client_puzzle_attempt'
        managed = True
        unique_together = ('puzzle', 'player', 'attempt_number')

    def determine_rating(self):
        """Auto-determine rating based on performance"""
        if not self.solved:
            return 1  # Again
        elif self.tries_count > 2 or self.hint_used:
            return 2  # Hard
        elif self.tries_count > 0:
            return 3  # Good
        else:
            return 4  # Easy


class FSRSMemory(models.Model):
    id = models.CharField(primary_key=True, max_length=36)
    player_username = models.ForeignKey('Player', on_delete=models.CASCADE, to_field='username', db_column='player_username', null=True, blank=True)
    puzzle_id = models.ForeignKey('Puzzle', on_delete=models.CASCADE, to_field='id', db_column='puzzle_id')
    difficulty = models.FloatField(default=2.0)
    stability = models.FloatField(default=0.5)
    last_review_date = models.DateTimeField(null=True, blank=True)
    next_review_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'chess_client_fsrs_memory'
        managed = True
        unique_together = ('player_username', 'puzzle_id')

    def calculate_retrievability(self):
        """Calculate current retrievability based on stability and time elapsed"""
        if not self.last_review_date:
            return 1.0  # New item, assume 100% retrievability

        elapsed_days = (timezone.now() - self.last_review_date).total_seconds() / (24 * 3600)
        return math.exp(-elapsed_days / self.stability)

    def calculate_next_interval(self, desired_retention=0.9):
        """Calculate days until next review based on stability and desired retention"""
        return -self.stability * math.log(desired_retention)

    def update_memory(self, rating, solved, tries_count, hint_used):
        """Update memory parameters based on performance"""
        now = timezone.now()

        # FSRS parameters
        stability_multipliers = {
            1: 0.2,  # Again
            2: 1.1,  # Hard
            3: 1.5,  # Good
            4: 2.0   # Easy
        }

        difficulty_adjustments = {
            1: 0.1,  # Again
            2: 0.05, # Hard
            3: -0.05,# Good
            4: -0.1  # Easy
        }

        # Calculate elapsed time since last review
        if self.last_review_date:
            elapsed_days = (now - self.last_review_date).total_seconds() / (24 * 3600)
            retrievability = self.calculate_retrievability()
        else:
            # First review, set initial values
            elapsed_days = 0
            retrievability = 1.0
            initial_stability = {
                1: 0.5,  # Again
                2: 1.2,  # Hard
                3: 3.0,  # Good
                4: 7.0   # Easy
            }
            self.stability = initial_stability.get(rating, 1.0)

        # Update difficulty
        self.difficulty += difficulty_adjustments.get(rating, 0)

        # Adjust difficulty based on performance
        if tries_count > 0:
            self.difficulty += 0.1 * min(tries_count, 3)

        if hint_used:
            self.difficulty += 0.2

        # Clamp difficulty between 1.0 and 3.0
        self.difficulty = max(1.0, min(3.0, self.difficulty))

        # Update stability based on rating
        if self.last_review_date:  # Not the first review
            if rating == 1:  # Again
                # Reset stability with penalty
                self.stability *= stability_multipliers[1]
            else:
                # Calculate stability increase with spacing effect
                stability_multiplier = stability_multipliers.get(rating, 1.5)

                # Spacing effect: longer intervals lead to stronger memories
                spacing_multiplier = min(2.0, math.sqrt(elapsed_days / max(self.stability, 0.1)))

                # Update stability
                self.stability *= stability_multiplier * spacing_multiplier

        # Calculate next review date
        next_interval = self.calculate_next_interval()
        self.next_review_date = now + timezone.timedelta(days=next_interval)

        # Update timestamps
        self.last_review_date = now
        self.updated_at = now

        # Save changes
        self.save()

        return self.next_review_date

class UserDailyProgress(models.Model):
    """Tracks a user's daily puzzle progress and limits"""
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    new_puzzles_seen = models.IntegerField(default=0)
    reviews_done = models.IntegerField(default=0)

    class Meta:
        unique_together = ['player', 'date']

    @property
    def total_puzzles_done(self):
        return self.new_puzzles_seen + self.reviews_done

    def is_new_limit_reached(self, new_limit=25):
        return self.new_puzzles_seen >= new_limit

    def is_total_limit_reached(self, total_limit=50):
        return self.total_puzzles_done >= total_limit


