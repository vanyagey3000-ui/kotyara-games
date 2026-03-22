from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import math

db = SQLAlchemy()

FACEIT_LEVELS = [
    {'level': 1, 'min_elo': 100, 'max_elo': 500, 'color': '#9ca3af'},
    {'level': 2, 'min_elo': 501, 'max_elo': 750, 'color': '#c08457'},
    {'level': 3, 'min_elo': 751, 'max_elo': 900, 'color': '#cbd5e1'},
    {'level': 4, 'min_elo': 901, 'max_elo': 1050, 'color': '#f59e0b'},
    {'level': 5, 'min_elo': 1051, 'max_elo': 1200, 'color': '#22c55e'},
    {'level': 6, 'min_elo': 1201, 'max_elo': 1350, 'color': '#06b6d4'},
    {'level': 7, 'min_elo': 1351, 'max_elo': 1530, 'color': '#3b82f6'},
    {'level': 8, 'min_elo': 1531, 'max_elo': 1750, 'color': '#8b5cf6'},
    {'level': 9, 'min_elo': 1751, 'max_elo': 2000, 'color': '#f97316'},
    {'level': 10, 'min_elo': 2001, 'max_elo': 2300, 'color': '#f43f5e'},
]


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    elo = db.Column(db.Integer, default=1000)
    peak_elo = db.Column(db.Integer, default=1000)

    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)
    goals_scored = db.Column(db.Integer, default=0)
    goals_conceded = db.Column(db.Integer, default=0)
    total_games = db.Column(db.Integer, default=0)
    win_streak = db.Column(db.Integer, default=0)
    best_streak = db.Column(db.Integer, default=0)

    coins = db.Column(db.Integer, default=500)
    gems = db.Column(db.Integer, default=10)

    active_skin = db.Column(db.String(50), default='kompot')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_game = db.Column(db.DateTime, nullable=True)
    is_online = db.Column(db.Boolean, default=False)
    is_in_game = db.Column(db.Boolean, default=False)

    owned_items = db.relationship('UserItem', backref='owner', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def rank_title(self):
        if self.elo >= 2400: return 'Легенда'
        elif self.elo >= 2000: return 'Мастер'
        elif self.elo >= 1700: return 'Алмаз'
        elif self.elo >= 1400: return 'Платина'
        elif self.elo >= 1200: return 'Золото'
        elif self.elo >= 1000: return 'Серебро'
        elif self.elo >= 800: return 'Бронза'
        else: return 'Новичок'

    @property
    def rank_color(self):
        colors = {
            'Легенда': '#ff4444', 'Мастер': '#aa44ff', 'Алмаз': '#44ddff',
            'Платина': '#44ffaa', 'Золото': '#ffdd44', 'Серебро': '#cccccc',
            'Бронза': '#cc8844', 'Новичок': '#888888'
        }
        return colors.get(self.rank_title, '#888888')

    @property
    def winrate(self):
        if self.total_games == 0: return 0
        return round((self.wins / self.total_games) * 100, 1)

    @property
    def kd_ratio(self):
        if self.goals_conceded == 0: return float(self.goals_scored)
        return round(self.goals_scored / self.goals_conceded, 2)

    def has_item(self, item_id):
        return UserItem.query.filter_by(user_id=self.id, item_id=item_id).first() is not None

    @property
    def unread_messages_count(self):
        return DirectMessage.query.filter_by(recipient_id=self.id, is_read=False).count()

    @property
    def pending_friend_requests_count(self):
        return FriendRequest.query.filter_by(recipient_id=self.id, status='pending').count()

    @property
    def faceit_level_info(self):
        for idx, level in enumerate(FACEIT_LEVELS):
            if self.elo <= level['max_elo'] or idx == len(FACEIT_LEVELS) - 1:
                progress_min = level['min_elo']
                progress_max = level['max_elo']
                progress_value = min(max(self.elo, progress_min), progress_max) - progress_min
                progress_span = max(progress_max - progress_min, 1)
                progress_percent = round((progress_value / progress_span) * 100, 1)
                next_level = FACEIT_LEVELS[idx + 1]['level'] if idx + 1 < len(FACEIT_LEVELS) else None
                next_level_elo = FACEIT_LEVELS[idx + 1]['min_elo'] if idx + 1 < len(FACEIT_LEVELS) else None
                return {
                    'level': level['level'],
                    'color': level['color'],
                    'current_elo': self.elo,
                    'min_elo': progress_min,
                    'max_elo': progress_max,
                    'progress_value': progress_value,
                    'progress_span': progress_span,
                    'progress_percent': 100 if level['level'] == 10 and self.elo >= progress_max else progress_percent,
                    'next_level': next_level,
                    'next_level_elo': next_level_elo,
                }

        last_level = FACEIT_LEVELS[-1]
        return {
            'level': last_level['level'],
            'color': last_level['color'],
            'current_elo': self.elo,
            'min_elo': last_level['min_elo'],
            'max_elo': last_level['max_elo'],
            'progress_value': last_level['max_elo'] - last_level['min_elo'],
            'progress_span': last_level['max_elo'] - last_level['min_elo'],
            'progress_percent': 100,
            'next_level': None,
            'next_level_elo': None,
        }

    @property
    def faceit_level(self):
        return self.faceit_level_info['level']

    def to_dict(self):
        return {
            'id': self.id, 'username': self.username,
            'elo': self.elo, 'rank': self.rank_title,
            'rank_color': self.rank_color,
            'faceit_level': self.faceit_level,
            'faceit_level_info': self.faceit_level_info,
            'wins': self.wins, 'losses': self.losses,
            'total_games': self.total_games, 'winrate': self.winrate,
            'coins': self.coins, 'gems': self.gems,
            'active_skin': self.active_skin,
            'is_online': self.is_online
        }


class ShopItem(db.Model):
    __tablename__ = 'shop_items'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    category = db.Column(db.String(30), nullable=False)
    price_coins = db.Column(db.Integer, default=0)
    price_gems = db.Column(db.Integer, default=0)
    rarity = db.Column(db.String(20), default='common')
    required_elo = db.Column(db.Integer, default=0)
    is_available = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    primary_color = db.Column(db.String(10), default='#ffffff')
    secondary_color = db.Column(db.String(10), default='#000000')
    emoji = db.Column(db.String(10), default='🎮')

    @property
    def rarity_color(self):
        return {'common': '#aaaaaa', 'rare': '#4488ff',
                'epic': '#aa44ff', 'legendary': '#ffaa00'}.get(self.rarity, '#aaaaaa')

    def to_dict(self):
        return {
            'id': self.id, 'item_id': self.item_id, 'name': self.name,
            'description': self.description, 'category': self.category,
            'price_coins': self.price_coins, 'price_gems': self.price_gems,
            'rarity': self.rarity, 'rarity_color': self.rarity_color,
            'required_elo': self.required_elo, 'is_available': self.is_available,
            'primary_color': self.primary_color, 'secondary_color': self.secondary_color,
            'emoji': self.emoji
        }


class UserItem(db.Model):
    __tablename__ = 'user_items'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    item_id = db.Column(db.String(50), nullable=False)
    purchased_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('user_id', 'item_id'),)


class MatchHistory(db.Model):
    __tablename__ = 'match_history'

    id = db.Column(db.Integer, primary_key=True)
    player1_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score_p1 = db.Column(db.Integer, default=0)
    score_p2 = db.Column(db.Integer, default=0)
    winner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    elo_change_p1 = db.Column(db.Integer, default=0)
    elo_change_p2 = db.Column(db.Integer, default=0)
    p1_elo_before = db.Column(db.Integer, default=0)
    p2_elo_before = db.Column(db.Integer, default=0)
    coins_reward_p1 = db.Column(db.Integer, default=0)
    coins_reward_p2 = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Integer, default=0)
    game_mode = db.Column(db.String(10), default='1v1')
    played_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    username = db.Column(db.String(32), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='messages', lazy=True)


class FriendRequest(db.Model):
    __tablename__ = 'friend_requests'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (db.UniqueConstraint('sender_id', 'recipient_id', name='uniq_friend_request'),)


class Friendship(db.Model):
    __tablename__ = 'friendships'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    friend_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('user_id', 'friend_id', name='uniq_friendship'),)


class Party(db.Model):
    __tablename__ = 'parties'

    id = db.Column(db.Integer, primary_key=True)
    leader_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class PartyMember(db.Model):
    __tablename__ = 'party_members'

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey('parties.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('party_id', 'user_id', name='uniq_party_member'),)


class PartyInvite(db.Model):
    __tablename__ = 'party_invites'

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey('parties.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (db.UniqueConstraint('party_id', 'recipient_id', name='uniq_party_invite'),)


class DirectMessage(db.Model):
    __tablename__ = 'direct_messages'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class TeamFinderPost(db.Model):
    __tablename__ = 'team_finder_posts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    mode = db.Column(db.String(10), default='1v1')
    note = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


def calculate_elo(winner_elo, loser_elo, k=32, draw=False):
    expected_w = 1 / (1 + math.pow(10, (loser_elo - winner_elo) / 400))
    expected_l = 1 / (1 + math.pow(10, (winner_elo - loser_elo) / 400))
    if draw:
        new_w = round(winner_elo + k * (0.5 - expected_w))
        new_l = round(loser_elo + k * (0.5 - expected_l))
    else:
        new_w = round(winner_elo + k * (1 - expected_w))
        new_l = round(loser_elo + k * (0 - expected_l))
    return max(100, new_w), max(100, new_l)


def seed_shop_items():
    """Только скины персонажей"""
    items = [
        {'item_id': 'kompot', 'name': 'Компот', 'description': 'Рыжий котёнок — самый старший!',
         'category': 'skin', 'price_coins': 0, 'rarity': 'common',
         'primary_color': '#ff8844', 'secondary_color': '#cc5500', 'emoji': '🐱'},

        {'item_id': 'karamelka', 'name': 'Карамелька', 'description': 'Сестрёнка-умница с бантиком!',
         'category': 'skin', 'price_coins': 0, 'rarity': 'common',
         'primary_color': '#ff69b4', 'secondary_color': '#cc3388', 'emoji': '🎀'},

        {'item_id': 'korzhik', 'name': 'Коржик', 'description': 'Худой и спортивный котик!',
         'category': 'skin', 'price_coins': 0, 'rarity': 'common',
         'primary_color': '#88bb44', 'secondary_color': '#558822', 'emoji': '⚽'},

        {'item_id': 'papa', 'name': 'Папа Кот', 'description': 'Мудрый и опытный.',
         'category': 'skin', 'price_coins': 1500, 'rarity': 'rare',
         'primary_color': '#4466aa', 'secondary_color': '#223366', 'emoji': '👔'},

        {'item_id': 'mama', 'name': 'Мама Кошка', 'description': 'Грация и стиль!',
         'category': 'skin', 'price_coins': 1500, 'rarity': 'rare',
         'primary_color': '#dd66aa', 'secondary_color': '#993366', 'emoji': '💐'},

        {'item_id': 'babushka', 'name': 'Бабушка', 'description': 'Варенье силы!',
         'category': 'skin', 'price_coins': 3000, 'rarity': 'epic',
         'primary_color': '#996688', 'secondary_color': '#664455', 'emoji': '🍪'},

        {'item_id': 'dedushka', 'name': 'Дедушка', 'description': 'Старая школа.',
         'category': 'skin', 'price_coins': 3000, 'rarity': 'epic',
         'primary_color': '#888866', 'secondary_color': '#555544', 'emoji': '🎣'},

        {'item_id': 'nuke_kompot', 'name': 'Компот Ультра', 'description': 'Огненный! Для легенд.',
         'category': 'skin', 'price_coins': 10000, 'price_gems': 50, 'rarity': 'legendary',
         'required_elo': 2000, 'primary_color': '#ff2200', 'secondary_color': '#ff8800', 'emoji': '🔥'},

        {'item_id': 'cyber_karamelka', 'name': 'Кибер-Карамелька', 'description': 'Из будущего!',
         'category': 'skin', 'price_coins': 10000, 'price_gems': 50, 'rarity': 'legendary',
         'required_elo': 2000, 'primary_color': '#00ffcc', 'secondary_color': '#0088aa', 'emoji': '🤖'},
    ]

    for item_data in items:
        existing = ShopItem.query.filter_by(item_id=item_data['item_id']).first()
        if not existing:
            db.session.add(ShopItem(**item_data))
    db.session.commit()
