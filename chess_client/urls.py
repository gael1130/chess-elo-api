# /home/kalel1130/chess-elo-api/chess-elo-api/chess_client/urls.py
from django.urls import path
from .views import (
    PlayerProfileView,
    PlayerStatsView,
    PlayerGamesArchivesView,
    PlayerCurrentGamesView,
    PlayerTitledView,
    PlayerRatingHistoryView,
    ScrapeGamesView,
    APIDocsView,
    PlayerPuzzlesView,
    PuzzleAttemptView,
    PuzzleAttemptActionView,
    # Import new FSRS view classes
    FSRSDuePuzzlesView,
    FSRSPuzzleAttemptView,
    FSRSDiagnosticView,
    DailyPuzzlesView,
    ResetDailyProgressView
)

urlpatterns = [
    path('', APIDocsView.as_view(), name='api-docs'),
    path('player/<str:username>/', PlayerProfileView.as_view(), name='player-profile'),
    path('player/<str:username>/stats/', PlayerStatsView.as_view(), name='player-stats'),
    path('player/<str:username>/games/', PlayerCurrentGamesView.as_view(), name='player-current-games'),
    path('player/<str:username>/games/archives/', PlayerGamesArchivesView.as_view(), name='player-games-archives'),
    path('player/<str:username>/rating-history/', PlayerRatingHistoryView.as_view(), name='player-rating-history'),
    path('player/<str:username>/scrape-games/', ScrapeGamesView.as_view(), name='scrape-games'),
    path('player/<str:username>/due-puzzles/', FSRSDuePuzzlesView.as_view(), name='fsrs-due-puzzles'),
    path('titled/<str:title_abbr>/', PlayerTitledView.as_view(), name='titled-players'),
    path('player/<str:username>/puzzles/', PlayerPuzzlesView.as_view(), name='player-puzzles'),

    # The order of these patterns is critical - most specific first
    path('puzzles/attempts/fsrs/', FSRSPuzzleAttemptView.as_view(), name='fsrs-puzzle-attempt'),
    path('puzzles/attempts/<str:attempt_id>/<str:action>/', PuzzleAttemptActionView.as_view(), name='puzzle-attempt-action'),
    path('puzzles/attempts/<str:attempt_id>/', PuzzleAttemptView.as_view(), name='puzzle-attempt-detail'),
    path('puzzles/attempts/', PuzzleAttemptView.as_view(), name='puzzle-attempts'),

    # Diagnostic endpoint
    path('fsrs-diagnostic/', FSRSDiagnosticView.as_view(), name='fsrs-diagnostic'),
    path('player/<str:username>/daily-puzzles/', DailyPuzzlesView.as_view(), name='daily-puzzles'),
    path('player/<str:username>/reset-daily-progress/', ResetDailyProgressView.as_view(), name='reset-daily-progress'),
]