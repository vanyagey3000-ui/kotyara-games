import time
import math
import random
import uuid


class GameRoom:
    WIDTH = 800
    HEIGHT = 500
    GOAL_WIDTH = 140
    PUCK_RADIUS = 15
    PADDLE_RADIUS = 30
    MAX_SCORE = 7
    FRICTION = 0.995
    PUCK_SPEED_LIMIT = 18
    PADDLE_SPEED_LIMIT = 16
    BOUNCE_ENERGY = 0.85
    FACEOFF_SPEED = 6.2
    MIN_PUCK_SPEED = 2.1
    STALL_PUSH = 3.8
    DISCONNECT_GRACE_SECONDS = 30
    DISPLAY_COUNTDOWN_SECONDS = 3
    GAME_START_SOUND_SECONDS = 3.2
    ROUND_START_SOUND_SECONDS = 3.05

    def __init__(self, room_id=None):
        self.room_id = room_id or str(uuid.uuid4())[:8]
        self.players = {}
        self.state = 'waiting'
        self.score = [0, 0]
        self.countdown = self.DISPLAY_COUNTDOWN_SECONDS
        self.countdown_duration = self.GAME_START_SOUND_SECONDS
        self.countdown_kind = 'game'
        self.p2p_active = False  # ← добавить
        self.start_time = None
        self.last_update = time.time()
        self.game_mode = '1v1'
        self.serve_direction = random.choice([-1, 1])
        self.slow_puck_since = None
        self.disconnect_started_at = None
        self.disconnected_user_id = None
        self.disconnected_player_num = None
        self.disconnected_username = None
        self.state_before_disconnect = None
        self.forfeit_player_num = None
        self.goal_y_start = (self.HEIGHT - self.GOAL_WIDTH) / 2
        self.goal_y_end = (self.HEIGHT + self.GOAL_WIDTH) / 2
        self.reset_positions()

    def start_countdown(self, kind='game'):
        self.state = 'countdown'
        self.countdown_kind = kind
        self.countdown = self.DISPLAY_COUNTDOWN_SECONDS
        self.countdown_duration = self.GAME_START_SOUND_SECONDS if kind == 'game' else self.ROUND_START_SOUND_SECONDS
        self.start_time = time.time()

    def reset_positions(self):
        self.puck = {
            'x': self.WIDTH / 2, 'y': self.HEIGHT / 2,
            'vx': 0, 'vy': 0, 'radius': self.PUCK_RADIUS
        }
        self.paddles = {
            1: {'x': 80, 'y': self.HEIGHT / 2, 'vx': 0, 'vy': 0,
                'radius': self.PADDLE_RADIUS, 'target_x': 80, 'target_y': self.HEIGHT / 2},
            2: {'x': self.WIDTH - 80, 'y': self.HEIGHT / 2, 'vx': 0, 'vy': 0,
                'radius': self.PADDLE_RADIUS, 'target_x': self.WIDTH - 80, 'target_y': self.HEIGHT / 2}
        }

    def add_player(self, sid, user_data):
        player_num = len(self.players) + 1
        if player_num > 2:
            return None
        self.players[sid] = {
            'number': player_num,
            'user_id': user_data['id'],
            'username': user_data['username'],
            'elo': user_data['elo'],
            'skin': user_data.get('active_skin', 'kompot'),
        }
        if len(self.players) == 2:
            self.start_countdown('game')
        return player_num

    def remove_player(self, sid):
        if sid in self.players:
            p = self.players[sid]
            del self.players[sid]
            if self.state in ('playing', 'countdown'):
                self.state = 'finished'
                return p['number']
        return None

    def mark_disconnected(self, user_id):
        if self.state not in ('playing', 'countdown'):
            return None
        if self.disconnected_user_id is not None:
            return None
        for sid, player in self.players.items():
            if player['user_id'] == user_id:
                self.state_before_disconnect = self.state
                self.state = 'disconnect_pause'
                self.disconnect_started_at = time.time()
                self.disconnected_user_id = user_id
                self.disconnected_player_num = player['number']
                self.disconnected_username = player['username']
                return {
                    'username': player['username'],
                    'player_number': player['number'],
                    'deadline_at': self.disconnect_started_at + self.DISCONNECT_GRACE_SECONDS
                }
        return None

    def restore_disconnected(self, user_id):
        if self.disconnected_user_id != user_id:
            return False
        paused_for = time.time() - (self.disconnect_started_at or time.time())
        if self.start_time:
            self.start_time += paused_for
        self.state = self.state_before_disconnect or 'countdown'
        self.disconnect_started_at = None
        self.disconnected_user_id = None
        self.disconnected_player_num = None
        self.disconnected_username = None
        self.state_before_disconnect = None
        self.forfeit_player_num = None
        return True

    def move_paddle(self, sid, x, y):
        if sid not in self.players or self.state != 'playing':
            return
        num = self.players[sid]['number']
        p = self.paddles[num]
        r = self.PADDLE_RADIUS
        if num == 1:
            x = max(r, min(x, self.WIDTH / 2 - r))
        else:
            x = max(self.WIDTH / 2 + r, min(x, self.WIDTH - r))
        y = max(r, min(y, self.HEIGHT - r))
        p['target_x'] = x
        p['target_y'] = y

    def update(self):
        now = time.time()
        dt = min(now - self.last_update, 0.05)
        self.last_update = now

        if self.state == 'disconnect_pause':
            if self.disconnect_started_at and now - self.disconnect_started_at >= self.DISCONNECT_GRACE_SECONDS:
                self.forfeit_player_num = self.disconnected_player_num
                self.state = 'finished'
            return self.get_state()

        if self.state == 'countdown':
            elapsed = now - self.start_time
            self.countdown = max(1, self.DISPLAY_COUNTDOWN_SECONDS - int(elapsed))
            if elapsed >= self.countdown_duration:
                self.state = 'playing'
                self.start_time = now
                angle = random.uniform(-0.5, 0.5)
                d = self.serve_direction or random.choice([-1, 1])
                self.puck['vx'] = self.FACEOFF_SPEED * d * math.cos(angle)
                self.puck['vy'] = self.FACEOFF_SPEED * math.sin(angle)
            return self.get_state()

        if self.state != 'playing':
            return self.get_state()

        for num in [1, 2]:
            p = self.paddles[num]
            dx = p['target_x'] - p['x']
            dy = p['target_y'] - p['y']
            lerp_speed = 0.48
            p['vx'] = dx * lerp_speed / max(dt, 0.001)
            p['vy'] = dy * lerp_speed / max(dt, 0.001)
            speed = math.sqrt(p['vx'] ** 2 + p['vy'] ** 2)
            max_spd = self.PADDLE_SPEED_LIMIT / max(dt, 0.001)
            if speed > max_spd:
                ratio = max_spd / speed
                p['vx'] *= ratio
                p['vy'] *= ratio
            p['x'] += p['vx'] * dt
            p['y'] += p['vy'] * dt
            r = p['radius']
            if num == 1:
                p['x'] = max(r, min(p['x'], self.WIDTH / 2 - r))
            else:
                p['x'] = max(self.WIDTH / 2 + r, min(p['x'], self.WIDTH - r))
            p['y'] = max(r, min(p['y'], self.HEIGHT - r))

        sub_steps = 3
        for _ in range(sub_steps):
            puck = self.puck
            puck['x'] += puck['vx']
            puck['y'] += puck['vy']
            puck['vx'] *= self.FRICTION
            puck['vy'] *= self.FRICTION

            speed = math.sqrt(puck['vx'] ** 2 + puck['vy'] ** 2)
            if speed > self.PUCK_SPEED_LIMIT:
                ratio = self.PUCK_SPEED_LIMIT / speed
                puck['vx'] *= ratio
                puck['vy'] *= ratio
            elif speed < self.MIN_PUCK_SPEED:
                if self.slow_puck_since is None:
                    self.slow_puck_since = now
                elif now - self.slow_puck_since > 0.65:
                    push = self.STALL_PUSH
                    if speed > 0:
                        puck['vx'] = (puck['vx'] / speed) * push
                        puck['vy'] = (puck['vy'] / speed) * push
                    else:
                        angle = random.uniform(-0.75, 0.75)
                        d = self.serve_direction or random.choice([-1, 1])
                        puck['vx'] = push * d * math.cos(angle)
                        puck['vy'] = push * math.sin(angle)
                    self.slow_puck_since = None
            else:
                self.slow_puck_since = None

            pr = puck['radius']
            if puck['y'] - pr <= 0:
                puck['y'] = pr
                puck['vy'] = abs(puck['vy']) * self.BOUNCE_ENERGY
            elif puck['y'] + pr >= self.HEIGHT:
                puck['y'] = self.HEIGHT - pr
                puck['vy'] = -abs(puck['vy']) * self.BOUNCE_ENERGY

            goal = self._check_goal()
            if goal:
                self.score[goal - 1] += 1
                self.serve_direction = -1 if goal == 1 else 1
                self.slow_puck_since = None
                if self.score[0] >= self.MAX_SCORE or self.score[1] >= self.MAX_SCORE:
                    self.state = 'finished'
                else:
                    self.reset_positions()
                    self.start_countdown('round')
                return self.get_state()

            if puck['x'] - pr <= 0:
                if not (self.goal_y_start < puck['y'] < self.goal_y_end):
                    puck['x'] = pr
                    puck['vx'] = abs(puck['vx']) * self.BOUNCE_ENERGY
            if puck['x'] + pr >= self.WIDTH:
                if not (self.goal_y_start < puck['y'] < self.goal_y_end):
                    puck['x'] = self.WIDTH - pr
                    puck['vx'] = -abs(puck['vx']) * self.BOUNCE_ENERGY

            for num in [1, 2]:
                self._check_paddle_collision(num)

        return self.get_state()

    def _check_goal(self):
        p = self.puck
        r = p['radius']
        if p['x'] - r <= 0 and self.goal_y_start < p['y'] < self.goal_y_end:
            return 2
        if p['x'] + r >= self.WIDTH and self.goal_y_start < p['y'] < self.goal_y_end:
            return 1
        return None

    def _check_paddle_collision(self, num):
        pad = self.paddles[num]
        puck = self.puck
        dx = puck['x'] - pad['x']
        dy = puck['y'] - pad['y']
        dist = math.sqrt(dx * dx + dy * dy)
        min_d = puck['radius'] + pad['radius']

        if dist < min_d and dist > 0:
            nx = dx / dist
            ny = dy / dist
            overlap = min_d - dist
            puck['x'] += nx * overlap
            puck['y'] += ny * overlap

            rel_vx = puck['vx'] - pad['vx'] * 0.016
            rel_vy = puck['vy'] - pad['vy'] * 0.016
            dot = rel_vx * nx + rel_vy * ny

            if dot < 0:
                rest = 1.2
                puck['vx'] -= (1 + rest) * dot * nx
                puck['vy'] -= (1 + rest) * dot * ny
                puck['vx'] += pad['vx'] * 0.016 * 0.75
                puck['vy'] += pad['vy'] * 0.016 * 0.75

    def get_state(self):
        elapsed = round(time.time() - self.start_time, 1) if self.start_time else 0
        if self.state == 'disconnect_pause' and self.disconnect_started_at:
            elapsed = round(max(0, elapsed - (time.time() - self.disconnect_started_at)), 1)
        players_info = {}
        for sid, p in self.players.items():
            players_info[str(p['number'])] = {
                'username': p['username'],
                'elo': p['elo'],
                'skin': p['skin'],
            }
        return {
            'room_id': self.room_id,
            'state': self.state,
            'score': self.score,
            'countdown': self.countdown,
            'countdown_kind': self.countdown_kind,
            'puck': {
                'x': round(self.puck['x'], 1),
                'y': round(self.puck['y'], 1),
                'vx': round(self.puck['vx'], 2),
                'vy': round(self.puck['vy'], 2),
                'r': self.PUCK_RADIUS
            },
            'paddles': {
                '1': {
                    'x': round(self.paddles[1]['x'], 1),
                    'y': round(self.paddles[1]['y'], 1),
                    'r': self.PADDLE_RADIUS
                },
                '2': {
                    'x': round(self.paddles[2]['x'], 1),
                    'y': round(self.paddles[2]['y'], 1),
                    'r': self.PADDLE_RADIUS
                }
            },
            'players': players_info,
            'goal_y_start': self.goal_y_start,
            'goal_y_end': self.goal_y_end,
            'max_score': self.MAX_SCORE,
            'disconnect': {
                'active': self.state == 'disconnect_pause' and self.disconnected_user_id is not None,
                'username': self.disconnected_username,
                'player_number': self.disconnected_player_num,
                'seconds_left': max(0, int(math.ceil(
                    self.DISCONNECT_GRACE_SECONDS - (time.time() - self.disconnect_started_at)
                ))) if self.disconnect_started_at else 0
            },
            'elapsed': elapsed
        }

    def get_winner(self):
        if self.score[0] > self.score[1]:
            return 1
        elif self.score[1] > self.score[0]:
            return 2
        return 0
