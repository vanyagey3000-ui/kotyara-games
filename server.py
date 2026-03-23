import os
import time
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, timezone, timedelta
from sqlalchemy import or_, and_
from werkzeug.security import check_password_hash

from database import (
    db, User, ShopItem, UserItem, MatchHistory, ChatMessage,
    FriendRequest, Friendship, Party, PartyMember, PartyInvite,
    DirectMessage, TeamFinderPost, SiteAnnouncement, PlayerReport,
    calculate_elo, seed_shop_items
)
from game_logic import GameRoom
from image_processor import process_all_images, get_processed_url, has_processed_image


def _normalize_database_uri(database_url):
    if not database_url:
        return ''

    database_url = database_url.strip()

    if database_url.startswith('postgres://'):
        database_url = 'postgresql://' + database_url[len('postgres://'):]
    if database_url.startswith('postgresql://') and '+psycopg2' not in database_url:
        database_url = 'postgresql+psycopg2://' + database_url[len('postgresql://'):]

    if database_url.startswith('mysql://'):
        database_url = 'mysql+pymysql://' + database_url[len('mysql://'):]
    if database_url.startswith('mysql+pymysql://') and 'charset=' not in database_url.lower():
        separator = '&' if '?' in database_url else '?'
        database_url += separator + 'charset=utf8mb4'

    return database_url


def _build_database_uri():
    for env_name in ('DATABASE_URL', 'MYSQL_URL', 'MYSQL_PUBLIC_URL'):
        database_url = os.environ.get(env_name, '').strip()
        if database_url:
            return _normalize_database_uri(database_url)
    return 'sqlite:///hockey.db'


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'kotyara-games-secret-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = _build_database_uri()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
}

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet',
                    ping_timeout=60, ping_interval=25)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

game_rooms = {}
matchmaking_queues = {'1v1': [], '2v2': [], '5v5': []}
sid_to_user_id = {}
user_id_to_room = {}
online_users = set()
users_transitioning = set()

ADMIN_PANEL_PATH = '/admin_jasdhhyhYYHDYd6767676767672736771711484887148757767141875757181486788676767176123661236747'
ADMIN_PASSWORD_HASH = 'scrypt:32768:8:1$KXZenV3xkPLbTNfC$7bc6abcc7d7fab697e4d8bf484fb483b8cb079ea3708197389d5a3f41f267754b063e1227a3caeeee08d86a58580532ce54aa381ab6d9e7c565c4208e21fc125'
ADMIN_SESSION_KEY = 'kotayra_admin_unlock'
ADMIN_SESSION_TTL_SECONDS = 60 * 60 * 8
MATCH_BAN_SECONDS = 90
ANNOUNCEMENT_TTL_SECONDS = 10
APP_BOOTSTRAPPED = False


def _matchmaking_window(waited_seconds):
    return min(420, 120 + int(waited_seconds * 14))


def _coin_reward(is_winner, goals_scored, goal_diff, streak, is_close_game):
    base = 70
    win_bonus = 90 if is_winner else 0
    goals_bonus = min(goals_scored, 7) * 8
    close_bonus = 18 if is_close_game else 0
    streak_bonus = min(streak, 5) * 12 if is_winner else 0
    diff_bonus = min(goal_diff, 5) * 7 if is_winner else min(goal_diff, 3) * 3
    return base + win_bonus + goals_bonus + close_bonus + streak_bonus + diff_bonus


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_helpers():
    return dict(
        has_image=lambda n: has_processed_image(n),
        image_url=lambda n: get_processed_url(n),
        active_announcement=_get_active_announcement()
    )


def _get_or_create_party(user):
    existing_member = PartyMember.query.filter_by(user_id=user.id).first()
    if existing_member:
        party = db.session.get(Party, existing_member.party_id)
        if party and party.status == 'active':
            return party

    party = Party(leader_id=user.id, status='active')
    db.session.add(party)
    db.session.flush()
    db.session.add(PartyMember(party_id=party.id, user_id=user.id))
    db.session.commit()
    return party


def _get_user_party(user_id):
    member = PartyMember.query.filter_by(user_id=user_id).first()
    if not member:
        return None
    party = db.session.get(Party, member.party_id)
    if not party or party.status != 'active':
        return None
    return party


def _serialize_party(party):
    if not party:
        return None
    members = PartyMember.query.filter_by(party_id=party.id).all()
    serialized = []
    for member in members:
        user = db.session.get(User, member.user_id)
        if user:
            serialized.append({
                'id': user.id,
                'username': user.username,
                'elo': user.elo,
                'faceit_level': user.faceit_level,
                'is_leader': party.leader_id == user.id,
            })
    return {'id': party.id, 'leader_id': party.leader_id, 'members': serialized}


def _friend_ids_for(user_id):
    return [f.friend_id for f in Friendship.query.filter_by(user_id=user_id).all()]


def _conversation_partners(user_id):
    msgs = DirectMessage.query.filter(
        or_(DirectMessage.sender_id == user_id, DirectMessage.recipient_id == user_id)
    ).order_by(DirectMessage.created_at.desc()).all()
    ordered_ids = []
    seen = set()
    for msg in msgs:
        other_id = msg.recipient_id if msg.sender_id == user_id else msg.sender_id
        if other_id not in seen:
            seen.add(other_id)
            ordered_ids.append(other_id)
    return [db.session.get(User, uid) for uid in ordered_ids if db.session.get(User, uid)]


def _leaderboard_rows(players, current_user_id=None):
    rows = []
    for position, player in enumerate(players, start=1):
        rows.append({
            'position': position,
            'user': player,
            'is_me': current_user_id == player.id
        })
    return rows


def _get_active_announcement():
    announcement = SiteAnnouncement.query.filter_by(is_active=True).order_by(SiteAnnouncement.created_at.desc()).first()
    if not announcement or not announcement.created_at:
        return announcement

    created_at = announcement.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    if (datetime.now(timezone.utc) - created_at).total_seconds() > ANNOUNCEMENT_TTL_SECONDS:
        announcement.is_active = False
        db.session.commit()
        return None

    return announcement


def _admin_unlocked():
    unlocked_at = session.get(ADMIN_SESSION_KEY)
    if not unlocked_at:
        return False
    if time.time() - unlocked_at > ADMIN_SESSION_TTL_SECONDS:
        session.pop(ADMIN_SESSION_KEY, None)
        return False
    return True


def _parse_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _serialize_active_rooms():
    rooms = []
    for room_id, room in game_rooms.items():
        rooms.append({
            'room_id': room_id,
            'state': room.state,
            'score': f"{room.score[0]}:{room.score[1]}",
            'mode': getattr(room, 'game_mode', '1v1'),
            'disconnect_username': room.disconnected_username,
            'players': [p['username'] for p in room.players.values()]
        })
    return rooms


def _broadcast_announcement_payload(announcement):
    return {
        'id': announcement.id,
        'title': announcement.title,
        'message': announcement.message,
        'level': announcement.level,
        'created_by': announcement.created_by,
        'created_at': announcement.created_at.strftime('%H:%M'),
        'duration_seconds': ANNOUNCEMENT_TTL_SECONDS
    }


def _delete_user_account(user):
    if user.id in user_id_to_room and user_id_to_room[user.id] in game_rooms:
        raise ValueError('Нельзя удалить игрока во время активного матча')

    for sid, uid in list(sid_to_user_id.items()):
        if uid == user.id:
            sid_to_user_id.pop(sid, None)

    online_users.discard(user.id)
    user_id_to_room.pop(user.id, None)
    users_transitioning.discard(user.id)

    for membership in PartyMember.query.filter_by(user_id=user.id).all():
        party = db.session.get(Party, membership.party_id)
        db.session.delete(membership)
        db.session.flush()
        if party:
            remaining = PartyMember.query.filter_by(party_id=party.id).all()
            if not remaining:
                party.status = 'closed'
            elif party.leader_id == user.id:
                party.leader_id = remaining[0].user_id

    FriendRequest.query.filter(
        or_(FriendRequest.sender_id == user.id, FriendRequest.recipient_id == user.id)
    ).delete(synchronize_session=False)
    Friendship.query.filter(
        or_(Friendship.user_id == user.id, Friendship.friend_id == user.id)
    ).delete(synchronize_session=False)
    PartyInvite.query.filter(
        or_(PartyInvite.sender_id == user.id, PartyInvite.recipient_id == user.id)
    ).delete(synchronize_session=False)
    DirectMessage.query.filter(
        or_(DirectMessage.sender_id == user.id, DirectMessage.recipient_id == user.id)
    ).delete(synchronize_session=False)
    TeamFinderPost.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    ChatMessage.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    MatchHistory.query.filter(
        or_(
            MatchHistory.player1_id == user.id,
            MatchHistory.player2_id == user.id,
            MatchHistory.winner_id == user.id
        )
    ).delete(synchronize_session=False)
    UserItem.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    db.session.delete(user)


# ═══ HTTP ═══

@app.route('/')
def index():
    top_players = User.query.order_by(User.elo.desc()).limit(10).all()
    return render_template('index.html',
                           top_players=top_players,
                           online_count=len(online_users),
                           games_count=len([r for r in game_rooms.values() if r.state == 'playing']))


@app.route('/tops')
def tops_page():
    current_user_id = current_user.id if current_user.is_authenticated else None

    global_players = User.query.order_by(
        User.elo.desc(),
        User.wins.desc(),
        User.username.asc()
    ).limit(50).all()

    skin_boards = []
    for skin_id, title, accent in [
        ('kompot', 'Топ Компот', '#ff934d'),
        ('karamelka', 'Топ Карамелька', '#ff6fb5'),
        ('korzhik', 'Топ Коржик', '#8fe35a'),
    ]:
        skin_players = User.query.filter_by(active_skin=skin_id).order_by(
            User.elo.desc(),
            User.wins.desc(),
            User.username.asc()
        ).limit(10).all()
        skin_boards.append({
            'skin_id': skin_id,
            'title': title,
            'accent': accent,
            'rows': _leaderboard_rows(skin_players, current_user_id)
        })

    return render_template(
        'tops.html',
        global_rows=_leaderboard_rows(global_players, current_user_id),
        skin_boards=skin_boards
    )


@app.route('/trust')
def trust_page():
    return render_template('trust.html')


@app.route('/rules')
def rules_page():
    return render_template('rules.html')


@app.route(ADMIN_PANEL_PATH, methods=['GET', 'POST'])
def admin_panel():
    unlocked = _admin_unlocked()

    if request.method == 'POST' and request.form.get('action') == 'unlock_admin_panel':
        password = request.form.get('admin_password', '')
        if check_password_hash(ADMIN_PASSWORD_HASH, password):
            session[ADMIN_SESSION_KEY] = time.time()
            flash('Админ-панель разблокирована', 'success')
        else:
            flash('Неверный пароль для админ-панели', 'error')
        return redirect(request.path)

    if request.method == 'POST' and not unlocked:
        flash('Сначала введи пароль для входа в админ-панель', 'error')
        return redirect(request.path)

    if request.method == 'POST':
        action = request.form.get('action', '')
        try:
            if action == 'lock_admin_panel':
                session.pop(ADMIN_SESSION_KEY, None)
                flash('Админ-панель заблокирована', 'success')
                return redirect(request.path)

            if action == 'broadcast_announcement':
                title = request.form.get('title', '').strip()[:140]
                message = request.form.get('message', '').strip()[:1000]
                level = request.form.get('level', 'info')
                if not title or not message:
                    raise ValueError('Заполни заголовок и текст объявления')
                SiteAnnouncement.query.filter_by(is_active=True).update({'is_active': False})
                announcement = SiteAnnouncement(
                    title=title,
                    message=message,
                    level=level if level in ('info', 'warning', 'alert') else 'info',
                    created_by=current_user.username if current_user.is_authenticated else 'Admin Panel'
                )
                db.session.add(announcement)
                db.session.commit()
                socketio.emit('admin_announcement', _broadcast_announcement_payload(announcement))
                flash('Глобальное объявление отправлено всем игрокам', 'success')

            elif action == 'clear_active_announcement':
                active = _get_active_announcement()
                if active:
                    active.is_active = False
                    db.session.commit()
                    flash('Активное объявление снято', 'success')
                else:
                    flash('Активных объявлений нет', 'error')

            elif action == 'set_staff_role':
                username = request.form.get('username', '').strip()
                role = request.form.get('staff_role', 'player')
                target = User.query.filter_by(username=username).first()
                if not target:
                    raise ValueError('Игрок с таким username не найден')
                if role not in ('player', 'moderator', 'creator'):
                    raise ValueError('Некорректная роль')
                target.staff_role = role
                db.session.commit()
                flash(f'Роль для {target.username} обновлена', 'success')

            elif action == 'update_user':
                user = db.session.get(User, _parse_int(request.form.get('user_id')))
                if not user:
                    raise ValueError('Игрок не найден')
                role = request.form.get('staff_role', user.staff_role)
                if role not in ('player', 'moderator', 'creator'):
                    role = 'player'
                user.username = request.form.get('username', user.username).strip()[:32] or user.username
                user.email = request.form.get('email', user.email).strip()[:120] or user.email
                user.elo = max(100, _parse_int(request.form.get('elo'), user.elo))
                user.peak_elo = max(user.peak_elo, user.elo)
                user.coins = max(0, _parse_int(request.form.get('coins'), user.coins))
                user.gems = max(0, _parse_int(request.form.get('gems'), user.gems))
                user.active_skin = request.form.get('active_skin', user.active_skin).strip()[:50] or user.active_skin
                user.staff_role = role
                ban_seconds = max(0, _parse_int(request.form.get('match_ban_seconds'), 0))
                user.match_ban_until = datetime.now(timezone.utc) + timedelta(seconds=ban_seconds) if ban_seconds else None
                db.session.commit()
                flash(f'Данные игрока {user.username} сохранены', 'success')

            elif action == 'delete_user':
                user = db.session.get(User, _parse_int(request.form.get('user_id')))
                if not user:
                    raise ValueError('Игрок не найден')
                if current_user.is_authenticated and user.id == current_user.id:
                    raise ValueError('Себя из панели удалять нельзя')
                username = user.username
                _delete_user_account(user)
                db.session.commit()
                flash(f'Игрок {username} удалён', 'success')

            elif action == 'save_shop_item':
                item_db_id = _parse_int(request.form.get('shop_db_id'))
                item = db.session.get(ShopItem, item_db_id) if item_db_id else None
                item_id = request.form.get('item_id', '').strip()[:50]
                if not item_id:
                    raise ValueError('Нужен item_id для предмета')
                existing = ShopItem.query.filter_by(item_id=item_id).first()
                if existing and item and existing.id != item.id:
                    raise ValueError('Такой item_id уже существует')
                if existing and not item:
                    item = existing
                if not item:
                    item = ShopItem(item_id=item_id)
                    db.session.add(item)
                item.name = request.form.get('name', item.name or item_id).strip()[:100] or item_id
                item.description = request.form.get('description', item.description or '').strip()[:300]
                item.category = request.form.get('category', item.category or 'skin').strip()[:30] or 'skin'
                item.price_coins = max(0, _parse_int(request.form.get('price_coins'), item.price_coins or 0))
                item.price_gems = max(0, _parse_int(request.form.get('price_gems'), item.price_gems or 0))
                item.rarity = request.form.get('rarity', item.rarity or 'common').strip()[:20] or 'common'
                item.required_elo = max(0, _parse_int(request.form.get('required_elo'), item.required_elo or 0))
                item.sort_order = _parse_int(request.form.get('sort_order'), item.sort_order or 0)
                item.primary_color = request.form.get('primary_color', item.primary_color or '#ffffff').strip()[:10] or '#ffffff'
                item.secondary_color = request.form.get('secondary_color', item.secondary_color or '#000000').strip()[:10] or '#000000'
                item.emoji = request.form.get('emoji', item.emoji or '🎮').strip()[:10] or '🎮'
                item.is_available = request.form.get('is_available') in ('1', 'on', 'true', 'yes')
                db.session.commit()
                flash(f'Предмет {item.name} сохранён', 'success')

            elif action == 'delete_shop_item':
                item = db.session.get(ShopItem, _parse_int(request.form.get('shop_db_id')))
                if not item:
                    raise ValueError('Предмет не найден')
                UserItem.query.filter_by(item_id=item.item_id).delete(synchronize_session=False)
                db.session.delete(item)
                db.session.commit()
                flash('Предмет удалён из маркета', 'success')

            elif action == 'delete_chat_message':
                msg = db.session.get(ChatMessage, _parse_int(request.form.get('message_id')))
                if not msg:
                    raise ValueError('Сообщение уже удалено')
                db.session.delete(msg)
                db.session.commit()
                flash('Сообщение удалено из общего чата', 'success')

            elif action == 'resolve_report':
                report = db.session.get(PlayerReport, _parse_int(request.form.get('report_id')))
                if not report:
                    raise ValueError('Жалоба не найдена')
                report.status = 'resolved'
                db.session.commit()
                flash('Жалоба помечена как обработанная', 'success')

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        except Exception as exc:
            db.session.rollback()
            flash(f'Ошибка админ-панели: {exc}', 'error')

        return redirect(request.path)

    users = User.query.order_by(User.elo.desc(), User.username.asc()).all()
    shop_items = ShopItem.query.order_by(ShopItem.sort_order.asc(), ShopItem.id.asc()).all()
    recent_chat = ChatMessage.query.order_by(ChatMessage.created_at.desc()).limit(30).all()
    reports = PlayerReport.query.order_by(PlayerReport.created_at.desc()).limit(40).all()
    announcements = SiteAnnouncement.query.order_by(SiteAnnouncement.created_at.desc()).limit(12).all()
    stats = {
        'users': User.query.count(),
        'online': len(online_users),
        'active_games': len([room for room in game_rooms.values() if room.state in ('playing', 'countdown', 'disconnect_pause')]),
        'pending_friend_requests': FriendRequest.query.filter_by(status='pending').count()
    }

    return render_template(
        'admin_panel.html',
        unlocked=unlocked,
        users=users,
        shop_items=shop_items,
        recent_chat=recent_chat,
        reports=reports,
        announcements=announcements,
        active_announcement=_get_active_announcement(),
        active_rooms=_serialize_active_rooms(),
        queue_snapshot={mode: len(entries) for mode, entries in matchmaking_queues.items()},
        stats=stats,
        admin_path=request.path
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        errors = []
        if len(username) < 3 or len(username) > 20: errors.append('Имя от 3 до 20 символов')
        if not email or '@' not in email: errors.append('Некорректный email')
        if len(password) < 6: errors.append('Пароль минимум 6 символов')
        if password != password2: errors.append('Пароли не совпадают')
        if User.query.filter_by(username=username).first(): errors.append('Имя занято')
        if User.query.filter_by(email=email).first(): errors.append('Email занят')
        if errors:
            for e in errors: flash(e, 'error')
            return render_template('register.html')
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        for iid in ['kompot', 'karamelka', 'korzhik']:
            db.session.add(UserItem(user_id=user.id, item_id=iid))
        db.session.commit()
        login_user(user)
        flash('Добро пожаловать в KotyaraGames! 🐱', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('index'))
        flash('Неверный логин или пароль', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    current_user.is_online = False
    db.session.commit()
    logout_user()
    return redirect(url_for('index'))


@app.route('/profile')
@app.route('/profile/<int:user_id>')
@login_required
def profile(user_id=None):
    user = db.session.get(User, user_id) if user_id else current_user
    if not user:
        flash('Не найден', 'error')
        return redirect(url_for('index'))
    matches = MatchHistory.query.filter(
        (MatchHistory.player1_id == user.id) | (MatchHistory.player2_id == user.id)
    ).order_by(MatchHistory.played_at.desc()).limit(20).all()
    match_data = []
    for m in matches:
        oid = m.player2_id if m.player1_id == user.id else m.player1_id
        opp = db.session.get(User, oid)
        ip1 = m.player1_id == user.id
        match_data.append({
            'opponent': opp.username if opp else '???', 'opponent_id': oid,
            'my_score': m.score_p1 if ip1 else m.score_p2,
            'opp_score': m.score_p2 if ip1 else m.score_p1,
            'won': m.winner_id == user.id,
            'elo_change': m.elo_change_p1 if ip1 else m.elo_change_p2,
            'coins': m.coins_reward_p1 if ip1 else m.coins_reward_p2,
            'date': m.played_at, 'mode': m.game_mode or '1v1'
        })
    return render_template('profile.html', user=user, matches=match_data)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_username':
            new_name = request.form.get('new_username', '').strip()
            if len(new_name) < 3 or len(new_name) > 20:
                flash('Имя от 3 до 20 символов', 'error')
            elif User.query.filter_by(username=new_name).first():
                flash('Имя занято', 'error')
            else:
                current_user.username = new_name
                db.session.commit()
                flash('Никнейм изменён!', 'success')

        elif action == 'change_password':
            old_pw = request.form.get('old_password', '')
            new_pw = request.form.get('new_password', '')
            new_pw2 = request.form.get('new_password2', '')
            if not current_user.check_password(old_pw):
                flash('Неверный старый пароль', 'error')
            elif len(new_pw) < 6:
                flash('Новый пароль минимум 6 символов', 'error')
            elif new_pw != new_pw2:
                flash('Пароли не совпадают', 'error')
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash('Пароль изменён!', 'success')

        return redirect(url_for('settings'))

    return render_template('settings.html')


@app.route('/shop')
@login_required
def shop():
    items = ShopItem.query.filter_by(is_available=True, category='skin').order_by(ShopItem.sort_order).all()
    owned = [ui.item_id for ui in UserItem.query.filter_by(user_id=current_user.id).all()]
    item_list = []
    for item in items:
        item_list.append({
            **item.to_dict(), 'owned': item.item_id in owned,
            'equipped': current_user.active_skin == item.item_id,
            'can_afford': current_user.coins >= item.price_coins and current_user.gems >= item.price_gems,
            'elo_enough': current_user.elo >= item.required_elo,
            'has_image': has_processed_image(item.item_id + '_ui'),
            'image_url': get_processed_url(item.item_id + '_ui')
        })
    return render_template('shop.html', items=item_list)


@app.route('/api/buy', methods=['POST'])
@login_required
def buy_item():
    data = request.get_json()
    item = ShopItem.query.filter_by(item_id=data.get('item_id')).first()
    if not item: return jsonify({'error': 'Не найден'}), 404
    if current_user.has_item(item.item_id): return jsonify({'error': 'Уже куплено'}), 400
    if current_user.coins < item.price_coins: return jsonify({'error': 'Мало монет'}), 400
    if current_user.gems < item.price_gems: return jsonify({'error': 'Мало гемов'}), 400
    if current_user.elo < item.required_elo: return jsonify({'error': 'ELO ' + str(item.required_elo) + '+'}), 400
    current_user.coins -= item.price_coins
    current_user.gems -= item.price_gems
    db.session.add(UserItem(user_id=current_user.id, item_id=item.item_id))
    db.session.commit()
    return jsonify({'success': True, 'coins': current_user.coins, 'gems': current_user.gems, 'message': 'Куплено: ' + item.name + '!'})


@app.route('/api/equip', methods=['POST'])
@login_required
def equip_item():
    data = request.get_json()
    item_id = data.get('item_id')
    if not current_user.has_item(item_id): return jsonify({'error': 'Нет'}), 400
    current_user.active_skin = item_id
    db.session.commit()
    return jsonify({'success': True, 'message': 'Надето!'})


@app.route('/lobby')
@login_required
def lobby():
    return render_template('lobby.html')


@app.route('/chat')
@login_required
def chat_page():
    messages = ChatMessage.query.order_by(ChatMessage.created_at.desc()).limit(50).all()
    messages.reverse()
    return render_template('chat.html', messages=messages)


@app.route('/social')
@login_required
def social_page():
    friend_ids = _friend_ids_for(current_user.id)
    friends = User.query.filter(User.id.in_(friend_ids)).order_by(User.username).all() if friend_ids else []
    incoming_requests = []
    for req in FriendRequest.query.filter_by(recipient_id=current_user.id, status='pending').order_by(FriendRequest.created_at.desc()).all():
        sender = db.session.get(User, req.sender_id)
        incoming_requests.append({'id': req.id, 'sender': sender})
    outgoing_requests = FriendRequest.query.filter_by(sender_id=current_user.id, status='pending').order_by(FriendRequest.created_at.desc()).all()
    party = _get_user_party(current_user.id)
    party_data = _serialize_party(party)
    party_invites = []
    for invite in PartyInvite.query.filter_by(recipient_id=current_user.id, status='pending').order_by(PartyInvite.created_at.desc()).all():
        sender = db.session.get(User, invite.sender_id)
        party_invites.append({'id': invite.id, 'party_id': invite.party_id, 'sender': sender})
    team_posts = []
    for post in TeamFinderPost.query.filter(
        TeamFinderPost.status == 'active',
        TeamFinderPost.user_id != current_user.id
    ).order_by(TeamFinderPost.created_at.desc()).limit(20).all():
        author = db.session.get(User, post.user_id)
        team_posts.append({'id': post.id, 'mode': post.mode, 'note': post.note, 'author': author})
    recent_users = User.query.filter(User.id != current_user.id).order_by(User.elo.desc()).limit(20).all()
    conversations = _conversation_partners(current_user.id)
    own_post = TeamFinderPost.query.filter_by(user_id=current_user.id, status='active').order_by(TeamFinderPost.created_at.desc()).first()

    return render_template(
        'social.html',
        friends=friends,
        incoming_requests=incoming_requests,
        outgoing_requests=outgoing_requests,
        party=party_data,
        party_invites=party_invites,
        team_posts=team_posts,
        recent_users=recent_users,
        conversations=conversations,
        own_post=own_post
    )


@app.route('/messages')
@app.route('/messages/<int:user_id>')
@login_required
def messages_page(user_id=None):
    partners = _conversation_partners(current_user.id)
    selected_user = db.session.get(User, user_id) if user_id else (partners[0] if partners else None)
    messages = []
    if selected_user:
        messages = DirectMessage.query.filter(
            or_(
                and_(DirectMessage.sender_id == current_user.id, DirectMessage.recipient_id == selected_user.id),
                and_(DirectMessage.sender_id == selected_user.id, DirectMessage.recipient_id == current_user.id)
            )
        ).order_by(DirectMessage.created_at.asc()).all()
        unread = DirectMessage.query.filter_by(sender_id=selected_user.id, recipient_id=current_user.id, is_read=False).all()
        for msg in unread:
            msg.is_read = True
        if unread:
            db.session.commit()
    return render_template('messages.html', partners=partners, selected_user=selected_user, messages=messages)


@app.route('/social/friends/add', methods=['POST'])
@login_required
def add_friend():
    username = request.form.get('username', '').strip()
    target = User.query.filter_by(username=username).first()
    if not target or target.id == current_user.id:
        flash('Игрок не найден', 'error')
        return redirect(url_for('social_page'))
    if Friendship.query.filter_by(user_id=current_user.id, friend_id=target.id).first():
        flash('Вы уже друзья', 'error')
        return redirect(url_for('social_page'))
    existing_request = FriendRequest.query.filter_by(sender_id=current_user.id, recipient_id=target.id).first()
    if existing_request and existing_request.status == 'pending':
        flash('Заявка уже отправлена', 'error')
        return redirect(url_for('social_page'))
    if FriendRequest.query.filter_by(sender_id=target.id, recipient_id=current_user.id, status='pending').first():
        flash('У этого игрока уже есть заявка к тебе', 'error')
        return redirect(url_for('social_page'))
    if existing_request:
        existing_request.status = 'pending'
        existing_request.created_at = datetime.now(timezone.utc)
        existing_request.responded_at = None
    else:
        db.session.add(FriendRequest(sender_id=current_user.id, recipient_id=target.id))
    db.session.commit()
    flash('Заявка в друзья отправлена', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/friends/respond', methods=['POST'])
@login_required
def respond_friend_request():
    req = db.session.get(FriendRequest, int(request.form.get('request_id', 0)))
    action = request.form.get('action')
    if not req or req.recipient_id != current_user.id or req.status != 'pending':
        flash('Заявка не найдена', 'error')
        return redirect(url_for('social_page'))
    req.status = 'accepted' if action == 'accept' else 'declined'
    req.responded_at = datetime.now(timezone.utc)
    if action == 'accept':
        if not Friendship.query.filter_by(user_id=req.sender_id, friend_id=req.recipient_id).first():
            db.session.add(Friendship(user_id=req.sender_id, friend_id=req.recipient_id))
        if not Friendship.query.filter_by(user_id=req.recipient_id, friend_id=req.sender_id).first():
            db.session.add(Friendship(user_id=req.recipient_id, friend_id=req.sender_id))
    db.session.commit()
    flash('Ответ на заявку сохранен', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/party/create', methods=['POST'])
@login_required
def create_party():
    _get_or_create_party(current_user)
    flash('Пати создана', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/party/invite', methods=['POST'])
@login_required
def invite_to_party():
    target_id = int(request.form.get('user_id', 0))
    party = _get_or_create_party(current_user)
    if party.leader_id != current_user.id:
        flash('Только лидер пати может приглашать', 'error')
        return redirect(url_for('social_page'))
    target = db.session.get(User, target_id)
    if not target or target.id == current_user.id:
        flash('Игрок не найден', 'error')
        return redirect(url_for('social_page'))
    if _get_user_party(target.id):
        flash('Игрок уже состоит в пати', 'error')
        return redirect(url_for('social_page'))
    existing = PartyInvite.query.filter_by(party_id=party.id, recipient_id=target.id).first()
    if existing and existing.status == 'pending':
        flash('Приглашение уже отправлено', 'error')
        return redirect(url_for('social_page'))
    if existing:
        existing.status = 'pending'
        existing.sender_id = current_user.id
        existing.created_at = datetime.now(timezone.utc)
        existing.responded_at = None
    else:
        db.session.add(PartyInvite(party_id=party.id, sender_id=current_user.id, recipient_id=target.id))
    db.session.commit()
    flash('Инвайт в пати отправлен', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/party/respond', methods=['POST'])
@login_required
def respond_party_invite():
    invite = db.session.get(PartyInvite, int(request.form.get('invite_id', 0)))
    action = request.form.get('action')
    if not invite or invite.recipient_id != current_user.id or invite.status != 'pending':
        flash('Инвайт не найден', 'error')
        return redirect(url_for('social_page'))
    if action == 'accept':
        if _get_user_party(current_user.id):
            flash('Сначала выйди из текущей пати', 'error')
            return redirect(url_for('social_page'))
        party = db.session.get(Party, invite.party_id)
        if party and party.status == 'active':
            db.session.add(PartyMember(party_id=party.id, user_id=current_user.id))
    invite.status = 'accepted' if action == 'accept' else 'declined'
    invite.responded_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Ответ на инвайт сохранен', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/party/leave', methods=['POST'])
@login_required
def leave_party():
    party = _get_user_party(current_user.id)
    if not party:
        flash('Ты не состоишь в пати', 'error')
        return redirect(url_for('social_page'))
    member = PartyMember.query.filter_by(party_id=party.id, user_id=current_user.id).first()
    if member:
        db.session.delete(member)
    db.session.flush()
    remaining = PartyMember.query.filter_by(party_id=party.id).all()
    if not remaining:
        party.status = 'closed'
    elif party.leader_id == current_user.id:
        party.leader_id = remaining[0].user_id
    db.session.commit()
    flash('Ты вышел из пати', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/teamfinder/post', methods=['POST'])
@login_required
def create_teamfinder_post():
    mode = request.form.get('mode', '1v1')
    note = request.form.get('note', '').strip()
    if not note:
        flash('Добавь описание для поиска тиммейта', 'error')
        return redirect(url_for('social_page'))
    old_posts = TeamFinderPost.query.filter_by(user_id=current_user.id, status='active').all()
    for old_post in old_posts:
        old_post.status = 'closed'
    db.session.add(TeamFinderPost(user_id=current_user.id, mode=mode, note=note[:300], status='active'))
    db.session.commit()
    flash('Поиск тиммейта опубликован', 'success')
    return redirect(url_for('social_page'))


@app.route('/social/teamfinder/close', methods=['POST'])
@login_required
def close_teamfinder_post():
    post = TeamFinderPost.query.filter_by(user_id=current_user.id, status='active').first()
    if post:
        post.status = 'closed'
        db.session.commit()
    flash('Поиск тиммейта закрыт', 'success')
    return redirect(url_for('social_page'))


@app.route('/messages/send', methods=['POST'])
@login_required
def send_direct_message():
    recipient_id = int(request.form.get('recipient_id', 0))
    text = request.form.get('message', '').strip()
    recipient = db.session.get(User, recipient_id)
    if not recipient or recipient.id == current_user.id:
        flash('Получатель не найден', 'error')
        return redirect(url_for('messages_page'))
    if not text:
        flash('Сообщение пустое', 'error')
        return redirect(url_for('messages_page', user_id=recipient.id))
    db.session.add(DirectMessage(sender_id=current_user.id, recipient_id=recipient.id, message=text[:1000]))
    db.session.commit()
    return redirect(url_for('messages_page', user_id=recipient.id))


@app.route('/report/player', methods=['POST'])
@login_required
def report_player():
    reason = request.form.get('reason', '').strip()[:500]
    target_user_id = _parse_int(request.form.get('target_user_id'))
    target_username = request.form.get('target_username', '').strip()
    target = db.session.get(User, target_user_id) if target_user_id else User.query.filter_by(username=target_username).first()

    if not target or target.id == current_user.id:
        flash('Игрок для жалобы не найден', 'error')
        return redirect(request.referrer or url_for('rules_page'))
    if not reason:
        flash('Опиши причину жалобы', 'error')
        return redirect(request.referrer or url_for('rules_page'))

    db.session.add(PlayerReport(
        reporter_id=current_user.id,
        reporter_username=current_user.username,
        target_user_id=target.id,
        target_username=target.username,
        reason=reason,
        status='open'
    ))
    db.session.commit()
    flash('Жалоба отправлена в админ-панель', 'success')
    return redirect(request.referrer or url_for('profile', user_id=target.id))


@app.route('/game/<room_id>')
@login_required
def game(room_id):
    if room_id not in game_rooms:
        flash('Комната не найдена', 'error')
        return redirect(url_for('lobby'))
    room = game_rooms[room_id]
    if not any(p['user_id'] == current_user.id for p in room.players.values()):
        flash('Вы не участник', 'error')
        return redirect(url_for('lobby'))
    users_transitioning.add(current_user.id)
    skin_images = {}
    for sid in ['kompot', 'karamelka', 'korzhik', 'papa', 'mama',
                'babushka', 'dedushka', 'nuke_kompot', 'cyber_karamelka',
                'lyapochka', 'bantik']:
        url = get_processed_url(sid + '_paddle')
        if url: skin_images[sid] = url
    return render_template('game.html', room_id=room_id,
                           skin_images=skin_images,
                           bg_url=get_processed_url('table_bg'))


@app.route('/api/leaderboard')
def leaderboard_api():
    players = User.query.order_by(User.elo.desc()).limit(50).all()
    return jsonify([{'rank': i + 1, 'username': p.username, 'elo': p.elo,
                     'rank_title': p.rank_title, 'rank_color': p.rank_color,
                     'wins': p.wins, 'losses': p.losses, 'winrate': p.winrate
                     } for i, p in enumerate(players)])


# ═══ WEBSOCKET ═══

@socketio.on('connect')
def handle_connect(auth=None):
    try:
        if current_user.is_authenticated:
            uid = current_user.id
            sid = request.sid
            current_user.is_online = True
            db.session.commit()
            online_users.add(uid)
            sid_to_user_id[sid] = uid
    except Exception as e:
        print('connect err:', e)
    socketio.emit('online_count', {'count': len(online_users)})
    active = _get_active_announcement()
    if active:
        emit('admin_announcement', _broadcast_announcement_payload(active))


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    uid = sid_to_user_id.get(sid)

    global matchmaking_queues
    for mode in matchmaking_queues:
        matchmaking_queues[mode] = [(s, d) for s, d in matchmaking_queues[mode] if s != sid]

    if sid in sid_to_user_id:
        del sid_to_user_id[sid]

    if uid is None: return

    if uid in users_transitioning: return

    has_other = any(u == uid for u in sid_to_user_id.values())
    if has_other: return

    if uid in user_id_to_room:
        room_id = user_id_to_room[uid]
        if room_id in game_rooms:
            room = game_rooms[room_id]
            disconnect_info = room.mark_disconnected(uid)
            if disconnect_info:
                socketio.emit('player_disconnected', disconnect_info, to=room_id)
            elif len(room.players) == 0:
                del game_rooms[room_id]

    try:
        user = db.session.get(User, uid)
        if user:
            user.is_online = False
            user.is_in_game = False
            db.session.commit()
    except: pass

    online_users.discard(uid)
    socketio.emit('online_count', {'count': len(online_users)})


@socketio.on('find_match')
def handle_find_match(data=None):
    if not current_user.is_authenticated: return
    if current_user.has_active_match_ban:
        emit('queue_status', {
            'status': 'banned',
            'remaining_seconds': current_user.match_ban_seconds_left
        })
        return
    sid = request.sid
    uid = current_user.id
    mode = '1v1'
    if data and data.get('mode') in ('1v1', '2v2', '5v5'):
        mode = data['mode']

    queue = matchmaking_queues[mode]
    for s, d in queue:
        if d['id'] == uid:
            emit('queue_status', {'status': 'already_searching'})
            return

    if uid in user_id_to_room:
        rid = user_id_to_room[uid]
        if rid in game_rooms:
            emit('match_found', {'room_id': rid, 'opponent': '...', 'opponent_elo': 0})
            return

    user_data = current_user.to_dict()
    user_data['mode'] = mode
    user_data['search_started_at'] = time.time()
    queue.append((sid, user_data))
    emit('queue_status', {
        'status': 'searching',
        'queue_size': len(queue),
        'mode': mode,
        'search_started_at': user_data['search_started_at']
    })
    _try_matchmaking(mode)


@socketio.on('cancel_search')
def handle_cancel_search():
    global matchmaking_queues
    sid = request.sid
    uid = sid_to_user_id.get(sid)
    for mode in matchmaking_queues:
        matchmaking_queues[mode] = [(s, d) for s, d in matchmaking_queues[mode]
                                     if s != sid and (uid is None or d['id'] != uid)]
    emit('queue_status', {'status': 'cancelled'})


@socketio.on('join_game')
def handle_join_game(data):
    if not current_user.is_authenticated:
        emit('game_error', {'error': 'Не авторизован'})
        return
    room_id = data.get('room_id')
    sid = request.sid
    uid = current_user.id
    users_transitioning.discard(uid)

    if room_id not in game_rooms:
        emit('game_error', {'error': 'Комната не найдена'})
        return

    room = game_rooms[room_id]
    player_num = None
    old_sid = None
    for s, p in list(room.players.items()):
        if p['user_id'] == uid:
            player_num = p['number']
            old_sid = s
            break

    if player_num is None:
        emit('game_error', {'error': 'Вы не участник'})
        return

    if old_sid and old_sid != sid:
        pd = room.players.pop(old_sid)
        room.players[sid] = pd

    join_room(room_id)
    sid_to_user_id[sid] = uid
    user_id_to_room[uid] = room_id
    current_user.is_in_game = True
    db.session.commit()

    if room.restore_disconnected(uid):
        socketio.emit('player_reconnected', {'username': current_user.username}, to=room_id)

    emit('game_joined', {'player_number': player_num, 'state': room.get_state()})


@socketio.on('paddle_move')
def handle_paddle_move(data):
    sid = request.sid
    uid = sid_to_user_id.get(sid)
    if not uid: return
    rid = user_id_to_room.get(uid)
    if not rid or rid not in game_rooms: return
    room = game_rooms[rid]
    if sid not in room.players: return
    room.move_paddle(sid, float(data.get('x', 0)), float(data.get('y', 0)))


# ═══ CHAT ═══

@socketio.on('chat_message')
def handle_chat_message(data):
    if not current_user.is_authenticated: return
    msg = data.get('message', '').strip()
    if not msg or len(msg) > 500: return

    chat_msg = ChatMessage(
        user_id=current_user.id,
        username=current_user.username,
        message=msg
    )
    db.session.add(chat_msg)
    db.session.commit()

    socketio.emit('new_chat_message', {
        'username': current_user.username,
        'user_id': current_user.id,
        'message': msg,
        'time': chat_msg.created_at.strftime('%H:%M'),
        'skin': current_user.active_skin,
        'staff_role': current_user.staff_role
    })


# ═══ Matchmaking ═══

def _try_matchmaking(mode='1v1'):
    queue = matchmaking_queues[mode]
    needed = 2  # Для всех режимов пока 2 (1v1 = 2 игрока)

    if len(queue) < needed: return

    now = time.time()
    queue.sort(key=lambda x: x[1]['elo'])
    best = None
    bd = float('inf')
    for i in range(len(queue) - 1):
        p1 = queue[i][1]
        p2 = queue[i + 1][1]
        d = abs(p1['elo'] - p2['elo'])
        waited = min(now - p1.get('search_started_at', now), now - p2.get('search_started_at', now))
        if d <= _matchmaking_window(waited) and d < bd:
            bd = d
            best = (i, i + 1)
    if not best: return

    p1s, p1d = queue[best[0]]
    p2s, p2d = queue[best[1]]
    matchmaking_queues[mode] = [(s, d) for j, (s, d) in enumerate(queue) if j not in best]

    room = GameRoom()
    room.game_mode = mode
    room.add_player(p1s, p1d)
    room.add_player(p2s, p2d)
    game_rooms[room.room_id] = room
    user_id_to_room[p1d['id']] = room.room_id
    user_id_to_room[p2d['id']] = room.room_id
    users_transitioning.add(p1d['id'])
    users_transitioning.add(p2d['id'])

    socketio.emit('match_found', {
        'room_id': room.room_id, 'opponent': p2d['username'],
        'opponent_elo': p2d['elo'], 'mode': mode
    }, to=p1s)
    socketio.emit('match_found', {
        'room_id': room.room_id, 'opponent': p1d['username'],
        'opponent_elo': p1d['elo'], 'mode': mode
    }, to=p2s)


def _handle_game_end(room, forfeit_player=None, banned_user_id=None):
    winner_num = room.get_winner()
    if forfeit_player: winner_num = 3 - forfeit_player

    p1d = p2d = None
    for s, p in room.players.items():
        if p['number'] == 1: p1d = p
        elif p['number'] == 2: p2d = p
    if not p1d or not p2d: return

    u1 = db.session.get(User, p1d['user_id'])
    u2 = db.session.get(User, p2d['user_id'])
    if not u1 or not u2: return

    oe1, oe2 = u1.elo, u2.elo
    goal_diff = abs(room.score[0] - room.score[1])
    close_game = goal_diff <= 1
    if winner_num == 1:
        ne1, ne2 = calculate_elo(u1.elo, u2.elo)
        u1.wins += 1; u2.losses += 1; u1.win_streak += 1; u2.win_streak = 0
    elif winner_num == 2:
        ne2, ne1 = calculate_elo(u2.elo, u1.elo)
        u2.wins += 1; u1.losses += 1; u2.win_streak += 1; u1.win_streak = 0
    else:
        ne1, ne2 = calculate_elo(u1.elo, u2.elo, draw=True)
        u1.draws += 1; u2.draws += 1; u1.win_streak = 0; u2.win_streak = 0

    ec1, ec2 = ne1 - oe1, ne2 - oe2
    u1.elo = ne1; u2.elo = ne2
    u1.peak_elo = max(u1.peak_elo, ne1); u2.peak_elo = max(u2.peak_elo, ne2)
    u1.best_streak = max(u1.best_streak, u1.win_streak)
    u2.best_streak = max(u2.best_streak, u2.win_streak)
    u1.total_games += 1; u2.total_games += 1
    u1.goals_scored += room.score[0]; u1.goals_conceded += room.score[1]
    u2.goals_scored += room.score[1]; u2.goals_conceded += room.score[0]

    now = datetime.now(timezone.utc)
    c1 = _coin_reward(winner_num == 1, room.score[0], goal_diff, u1.win_streak, close_game)
    c2 = _coin_reward(winner_num == 2, room.score[1], goal_diff, u2.win_streak, close_game)
    u1.coins += c1; u2.coins += c2
    u1.is_in_game = False; u2.is_in_game = False
    if banned_user_id == u1.id:
        u1.match_ban_until = now + timedelta(seconds=MATCH_BAN_SECONDS)
    elif banned_user_id == u2.id:
        u2.match_ban_until = now + timedelta(seconds=MATCH_BAN_SECONDS)
    u1.last_game = now; u2.last_game = now

    dur = int(time.time() - room.start_time) if room.start_time else 0
    m = MatchHistory(
        player1_id=u1.id, player2_id=u2.id,
        score_p1=room.score[0], score_p2=room.score[1],
        winner_id=u1.id if winner_num == 1 else (u2.id if winner_num == 2 else None),
        elo_change_p1=ec1, elo_change_p2=ec2,
        p1_elo_before=oe1, p2_elo_before=oe2,
        coins_reward_p1=c1, coins_reward_p2=c2,
        duration_seconds=dur,
        game_mode=getattr(room, 'game_mode', '1v1')
    )
    db.session.add(m)
    db.session.commit()

    user_id_to_room.pop(u1.id, None)
    user_id_to_room.pop(u2.id, None)

    result = {
        'winner': winner_num, 'score': room.score,
        'player1': {
            'username': u1.username,
            'elo': u1.elo,
            'elo_change': ec1,
            'coins_earned': c1,
            'new_rank': u1.rank_title,
            'active_skin': u1.active_skin,
            'faceit_level': u1.faceit_level,
            'faceit_progress': u1.faceit_level_info
        },
        'player2': {
            'username': u2.username,
            'elo': u2.elo,
            'elo_change': ec2,
            'coins_earned': c2,
            'new_rank': u2.rank_title,
            'active_skin': u2.active_skin,
            'faceit_level': u2.faceit_level,
            'faceit_progress': u2.faceit_level_info
        }
    }
    socketio.emit('game_finished', result, to=room.room_id)

# ═══ P2P Signaling ═══

@socketio.on('p2p_established')
def handle_p2p_established(data):
    """Хост сообщает что P2P соединение установлено"""
    if not current_user.is_authenticated:
        return
    room_id = data.get('room_id')
    if room_id and room_id in game_rooms:
        room = game_rooms[room_id]
        room.p2p_active = True
        print(f'P2P established for room {room_id}')

@socketio.on('p2p_goal')
def handle_p2p_goal(data):
    """P2P хост сообщает серверу о голе — сервер обновляет комнату"""
    if not current_user.is_authenticated:
        return
    room_id = data.get('room_id')
    if not room_id or room_id not in game_rooms:
        return
    room = game_rooms[room_id]
    new_score = data.get('score', [0, 0])
    room.score = [new_score[0], new_score[1]]

    if room.score[0] >= room.MAX_SCORE or room.score[1] >= room.MAX_SCORE:
        room.state = 'finished'
        try:
            _handle_game_end(room)
        except Exception as e:
            print('p2p_goal end err:', e)

@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    """Просто пересылаем сигнал другому игроку в комнате"""
    if not current_user.is_authenticated:
        return
    room_id = data.get('room_id')
    if not room_id:
        return
    # Отправляем всем В КОМНАТЕ кроме отправителя
    emit('p2p_signal', {
        'type': data.get('type'),
        'data': data.get('data')
    }, to=room_id, include_self=False)

def game_loop():
    with app.app_context():
        while True:
            finished = []
            for rid, room in list(game_rooms.items()):
                if room.state in ('playing', 'countdown', 'disconnect_pause'):
                    # Сервер ВСЕГДА считает физику и шлёт ПОЛНЫЙ state
                    state = room.update()
                    socketio.emit('game_state', state, to=rid)

                    if room.state == 'finished':
                        finished.append(rid)
            for rid in finished:
                if rid in game_rooms:
                    try:
                        room = game_rooms[rid]
                        _handle_game_end(
                            room,
                            forfeit_player=room.forfeit_player_num,
                            banned_user_id=room.disconnected_user_id if room.forfeit_player_num else None
                        )
                    except Exception as e:
                        print('end err:', e)
                        import traceback; traceback.print_exc()
            eventlet.sleep(1 / 60)


def cleanup_loop():
    with app.app_context():
        while True:
            to_del = []
            for rid, room in list(game_rooms.items()):
                if room.state == 'finished' and (time.time() - room.last_update > 60):
                    to_del.append(rid)
            for rid in to_del:
                game_rooms.pop(rid, None)
            users_transitioning.clear()
            eventlet.sleep(30)


def create_app():
    global APP_BOOTSTRAPPED
    if APP_BOOTSTRAPPED:
        return app
    from migrate import auto_migrate
    auto_migrate(app)
    with app.app_context():
        db.create_all()
        seed_shop_items()
    process_all_images()
    APP_BOOTSTRAPPED = True
    return app


create_app()


if __name__ == '__main__':
    socketio.start_background_task(game_loop)
    socketio.start_background_task(cleanup_loop)
    print("=" * 50)
    print("🏒 KotyaraGames запущен!")
    print("🌐 http://localhost:5000")
    print("=" * 50)
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
