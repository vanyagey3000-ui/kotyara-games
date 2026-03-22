import os
import time
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, timezone
from sqlalchemy import or_, and_

from database import (
    db, User, ShopItem, UserItem, MatchHistory, ChatMessage,
    FriendRequest, Friendship, Party, PartyMember, PartyInvite,
    DirectMessage, TeamFinderPost,
    calculate_elo, seed_shop_items
)
from game_logic import GameRoom
from image_processor import process_all_images, get_processed_url, has_processed_image

app = Flask(__name__)
app.config['SECRET_KEY'] = 'kotyara-games-secret-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hockey.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
        image_url=lambda n: get_processed_url(n)
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


# ═══ HTTP ═══

@app.route('/')
def index():
    top_players = User.query.order_by(User.elo.desc()).limit(10).all()
    return render_template('index.html',
                           top_players=top_players,
                           online_count=len(online_users),
                           games_count=len([r for r in game_rooms.values() if r.state == 'playing']))


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
                'babushka', 'dedushka', 'nuke_kompot', 'cyber_karamelka']:
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
            ps = None
            for s, p in list(room.players.items()):
                if p['user_id'] == uid:
                    ps = s
                    break
            if ps:
                disc = room.remove_player(ps)
                if disc and room.state == 'finished':
                    try: _handle_game_end(room, forfeit_player=disc)
                    except Exception as e: print('forfeit err:', e)
                socketio.emit('player_disconnected', {}, to=room_id)
            if len(room.players) == 0:
                del game_rooms[room_id]
        user_id_to_room.pop(uid, None)

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
        'skin': current_user.active_skin
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


def _handle_game_end(room, forfeit_player=None):
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

    c1 = _coin_reward(winner_num == 1, room.score[0], goal_diff, u1.win_streak, close_game)
    c2 = _coin_reward(winner_num == 2, room.score[1], goal_diff, u2.win_streak, close_game)
    u1.coins += c1; u2.coins += c2
    u1.is_in_game = False; u2.is_in_game = False
    now = datetime.now(timezone.utc)
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
            'faceit_level': u1.faceit_level,
            'faceit_progress': u1.faceit_level_info
        },
        'player2': {
            'username': u2.username,
            'elo': u2.elo,
            'elo_change': ec2,
            'coins_earned': c2,
            'new_rank': u2.rank_title,
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
                if room.state in ('playing', 'countdown'):
                    # Сервер ВСЕГДА считает физику и шлёт ПОЛНЫЙ state
                    state = room.update()
                    socketio.emit('game_state', state, to=rid)

                    if room.state == 'finished':
                        finished.append(rid)
            for rid in finished:
                if rid in game_rooms:
                    try:
                        _handle_game_end(game_rooms[rid])
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
    from migrate import auto_migrate
    auto_migrate(app)
    with app.app_context():
        db.create_all()
        seed_shop_items()
    process_all_images()
    return app


if __name__ == '__main__':
    create_app()
    socketio.start_background_task(game_loop)
    socketio.start_background_task(cleanup_loop)
    print("=" * 50)
    print("🏒 KotyaraGames запущен!")
    print("🌐 http://localhost:5000")
    print("=" * 50)
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
