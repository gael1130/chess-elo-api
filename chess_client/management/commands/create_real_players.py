# chess_client/management/commands/create_real_players.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from chess_client.models import Player, Archive, Game, UserDailyProgress
import json
from datetime import datetime, timedelta
import random

class Command(BaseCommand):
    help = 'Create test data using real chess.com players'

    def add_arguments(self, parser):
        parser.add_argument('--games-per-player', type=int, default=10, help='Number of games per player')
        parser.add_argument('--with-games', action='store_true', help='Create sample games for players')

    def handle(self, *args, **options):
        self.stdout.write("Creating real chess.com players...")
        
        # Real players data from your production database
        real_players = [
            {
                'username': 'alexandrechambre',
                'last_updated': '2025-05-13 14:49:11.893898',
                'total_games': 1084,
                'archives_processed': 31,
                'last_ratings': {"chess_daily": 1316, "chess_rapid": 1498, "chess_blitz": 1406}
            },
            {
                'username': 'drittman13',
                'last_updated': '2025-05-25 14:49:12.745470',
                'total_games': 23407,
                'archives_processed': 78,
                'last_ratings': {"chess_daily": 1470, "chess_rapid": 1743, "chess_blitz": 1657, "chess_bullet": 1360}
            },
            {
                'username': 'eddybabe',
                'last_updated': '2025-05-23 17:23:13.139705',
                'total_games': 224,
                'archives_processed': 6,
                'last_ratings': {"chess_daily": 1200, "chess_rapid": 505, "chess_blitz": 264, "chess_bullet": 129}
            },
            {
                'username': 'eddybabe75',
                'last_updated': '2025-05-25 14:42:13.809868',
                'total_games': 3347,
                'archives_processed': 31,
                'last_ratings': {"chess_daily": 1200, "chess_rapid": 963, "chess_blitz": 476, "chess_bullet": 372}
            },
            {
                'username': 'Hkaolin',
                'last_updated': '2025-05-13 14:30:39.986105',
                'total_games': 3474,
                'archives_processed': 1,
                'last_ratings': {"chess_daily": 1056, "chess_rapid": 1225, "chess_blitz": 1035, "chess_bullet": 1032}
            },
            {
                'username': 'kalel1130',
                'last_updated': '2025-05-25 14:42:14.813762',
                'total_games': 2429,
                'archives_processed': 11,
                'last_ratings': {"chess_daily": 827, "chess_rapid": 1067}
            },
            {
                'username': 'kelevraslevin',
                'last_updated': '2025-05-25 14:42:15.193569',
                'total_games': 8107,
                'archives_processed': 63,
                'last_ratings': {"chess_daily": 1272, "chess_rapid": 1543, "chess_blitz": 1555, "chess_bullet": 1169}
            },
            {
                'username': 'Mad_Mart',
                'last_updated': '2025-05-25 14:42:16.231586',
                'total_games': 5712,
                'archives_processed': 32,
                'last_ratings': {"chess_daily": 1104, "chess_rapid": 1812, "chess_blitz": 1206, "chess_bullet": 964}
            }
        ]

        created_players = []
        
        # Create players
        for player_data in real_players:
            # Parse the datetime string
            last_updated = datetime.strptime(player_data['last_updated'], '%Y-%m-%d %H:%M:%S.%f')
            last_updated = timezone.make_aware(last_updated)
            
            player, created = Player.objects.get_or_create(
                username=player_data['username'],
                defaults={
                    'last_updated': last_updated,
                    'total_games': player_data['total_games'],
                    'archives_processed': player_data['archives_processed'],
                    'last_ratings': json.dumps(player_data['last_ratings'])
                }
            )
            
            if created:
                self.stdout.write(f"✓ Created player: {player.username} (Rating: {self.get_main_rating(player_data['last_ratings'])})")
                created_players.append(player)
            else:
                self.stdout.write(f"- Player already exists: {player.username}")
                created_players.append(player)

        # Create realistic archives for each player
        current_year = datetime.now().year
        for player in created_players:
            # Create archives based on their archives_processed count
            months_to_create = min(player.archives_processed, 12)
            for month in range(1, months_to_create + 1):
                archive, created = Archive.objects.get_or_create(
                    player=player,
                    year=current_year,
                    month=month,
                    defaults={
                        'url': f'https://api.chess.com/pub/player/{player.username}/games/{current_year}/{month:02d}',
                        'processed': True,
                        'processed_at': timezone.now() - timedelta(days=random.randint(1, 30))
                    }
                )

        # Create sample games if requested
        if options['with_games']:
            self.create_sample_games(created_players, options['games_per_player'])

        # Create daily progress for recent days
        for player in created_players[:4]:  # For first 4 players
            for days_ago in range(0, 5):  # Last 5 days
                date = (timezone.now() - timedelta(days=days_ago)).date()
                progress, created = UserDailyProgress.objects.get_or_create(
                    player=player,
                    date=date,
                    defaults={
                        'new_puzzles_seen': random.randint(0, 15),
                        'reviews_done': random.randint(0, 25)
                    }
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n🎉 Real players data created successfully!\n"
                f"   Players: {len(created_players)}\n"
                f"   Archives: {Archive.objects.count()}\n"
                f"   Games: {Game.objects.count()}\n"
                f"   Daily Progress: {UserDailyProgress.objects.count()}"
            )
        )
        
        # Show player summary
        self.stdout.write("\n📊 Player Summary:")
        for player in created_players:
            ratings = json.loads(player.last_ratings)
            main_rating = self.get_main_rating(ratings)
            self.stdout.write(f"   {player.username}: {player.total_games} games, {main_rating}")

    def get_main_rating(self, ratings):
        """Get the main rating to display (prefer rapid, then blitz, then daily)"""
        if 'chess_rapid' in ratings:
            return f"Rapid: {ratings['chess_rapid']}"
        elif 'chess_blitz' in ratings:
            return f"Blitz: {ratings['chess_blitz']}"
        elif 'chess_daily' in ratings:
            return f"Daily: {ratings['chess_daily']}"
        else:
            return "No rating"

    def create_sample_games(self, players, games_per_player):
        """Create sample games for the players"""
        self.stdout.write(f"\nCreating sample games ({games_per_player} per player)...")
        
        time_controls = ['600', '300', '180', '60', '30', '900+10', '600+5', '300+3']
        time_classes = ['rapid', 'blitz', 'bullet', 'daily']
        results = ['win', 'checkmated', 'timeout', 'resigned', 'draw', 'stalemate']
        openings = [
            'Sicilian Defense', 'French Defense', 'Italian Game', 'Ruy Lopez',
            'Queen\'s Gambit', 'English Opening', 'Caro-Kann Defense', 
            'Scandinavian Defense', 'King\'s Indian Defense', 'Nimzo-Indian Defense'
        ]
        
        opponent_names = [
            'ChessMaster2024', 'RookiePlayer', 'KnightRider', 'QueenBee123',
            'PawnStorm', 'CastleKing', 'CheckmateArtist', 'BlitzWarrior',
            'TacticalGenius', 'EndgameExpert', 'PositionalPlayer', 'AttackingStyle'
        ]

        game_counter = 0
        for player in players:
            # Get player's ratings for realistic game ratings
            ratings = json.loads(player.last_ratings)
            
            for _ in range(games_per_player):
                opponent_name = random.choice(opponent_names)
                is_white = random.choice([True, False])
                
                # Choose time class based on player's available ratings
                available_time_classes = []
                if 'chess_rapid' in ratings:
                    available_time_classes.extend(['rapid'] * 3)  # More likely
                if 'chess_blitz' in ratings:
                    available_time_classes.extend(['blitz'] * 2)
                if 'chess_bullet' in ratings:
                    available_time_classes.append('bullet')
                if 'chess_daily' in ratings:
                    available_time_classes.append('daily')
                
                time_class = random.choice(available_time_classes or ['rapid'])
                
                # Get player rating for this time class
                rating_key = f'chess_{time_class}'
                player_rating = ratings.get(rating_key, 1200)
                opponent_rating = player_rating + random.randint(-200, 200)
                
                # Assign colors and ratings
                white_username = player.username if is_white else opponent_name
                black_username = opponent_name if is_white else player.username
                white_rating = player_rating if is_white else opponent_rating
                black_rating = opponent_rating if is_white else player_rating
                
                # Generate realistic result based on rating difference
                rating_diff = player_rating - opponent_rating if is_white else opponent_rating - player_rating
                
                # Higher rated player more likely to win
                if rating_diff > 100:
                    player_result = random.choices(['win', 'draw', 'checkmated'], weights=[60, 25, 15])[0]
                elif rating_diff > 0:
                    player_result = random.choices(['win', 'draw', 'checkmated'], weights=[45, 30, 25])[0]
                else:
                    player_result = random.choices(['win', 'draw', 'checkmated'], weights=[35, 25, 40])[0]
                
                # Determine opponent result
                if player_result == 'win':
                    opponent_result = random.choice(['checkmated', 'timeout', 'resigned'])
                elif player_result in ['checkmated', 'timeout', 'resigned']:
                    opponent_result = 'win'
                else:
                    opponent_result = 'draw'
                
                white_result = player_result if is_white else opponent_result
                black_result = opponent_result if is_white else player_result
                
                # Create unique game identifier
                game_uuid = f"{player.username}_{random.randint(100000, 999999)}"
                end_time = int((timezone.now() - timedelta(days=random.randint(1, 90))).timestamp())
                
                # Match time control to time class
                if time_class == 'bullet':
                    time_control = random.choice(['60', '30', '120'])
                elif time_class == 'blitz':
                    time_control = random.choice(['180', '300', '300+3'])
                elif time_class == 'rapid':
                    time_control = random.choice(['600', '900+10', '1800'])
                else:  # daily
                    time_control = '259200'  # 3 days
                
                game, created = Game.objects.get_or_create(
                    game_uuid=game_uuid,
                    defaults={
                        'player': player,
                        'url': f'https://chess.com/game/live/{game_uuid}',
                        'pgn': self.generate_sample_pgn(white_username, black_username),
                        'time_control': time_control,
                        'end_time': end_time,
                        'rated': True,
                        'white_username': white_username,
                        'white_rating': white_rating,
                        'white_result': white_result,
                        'black_username': black_username,
                        'black_rating': black_rating,
                        'black_result': black_result,
                        'time_class': time_class,
                        'eco': f"{random.choice(['A', 'B', 'C', 'D', 'E'])}{random.randint(10, 99)}",
                        'opening': random.choice(openings),
                        'white_accuracy': round(random.uniform(75, 95), 1),
                        'black_accuracy': round(random.uniform(75, 95), 1),
                        'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
                        'is_active': False,
                        'created_at': timezone.now() - timedelta(days=random.randint(1, 30)),
                        'player_rating': player_rating
                    }
                )
                
                if created:
                    game_counter += 1

        self.stdout.write(f"Created {game_counter} sample games")

    def generate_sample_pgn(self, white, black):
        """Generate a sample PGN for the game"""
        sample_games = [
            f'[White "{white}"][Black "{black}"] 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6 5.O-O Be7 *',
            f'[White "{white}"][Black "{black}"] 1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.cxd5 exd5 5.Bg5 c6 *',
            f'[White "{white}"][Black "{black}"] 1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 *',
            f'[White "{white}"][Black "{black}"] 1.Nf3 d5 2.g3 c5 3.Bg2 Nc6 4.O-O e6 5.d3 Nf6 *',
            f'[White "{white}"][Black "{black}"] 1.e4 e6 2.d4 d5 3.Nd2 Nf6 4.e5 Nfd7 5.Bd3 c5 *'
        ]
        return random.choice(sample_games)