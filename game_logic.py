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
    PADDLE_SPEED_LIMIT = 12
    BOUNCE_ENERGY = 0.85

    def __init__(self, room_id=None):
        self.room_id = room_id or str(uuid.uuid4())[:8]
        self.players = {}
        self.state = 'waiting'
        self.score = [0, 0]
        self.countdown = 3
        self.start_time = None
        self.last_update = time.time()
        self.game_mode = '1v1'
        self.goal_y_start = (self.HEIGHT - self.GOAL_WIDTH) / 2
        self.goal_y_end = (self.HEIGHT + self.GOAL_WIDTH) / 2
        self.reset_positions()

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
            self.state = 'countdown'
            self.countdown = 3
            self.start_time = time.time()
        return player_num

    def remove_player(self, sid):
        if sid in self.players:
            p = self.players[sid]
            del self.players[sid]
            if self.state in ('playing', 'countdown'):
                self.state = 'finished'
                return p['number']
        return None

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

        if self.state == 'countdown':
            elapsed = now - self.start_time
            self.countdown = max(0, 3 - int(elapsed))
            if elapsed >= 3:
                self.state = 'playing'
                self.start_time = now
                angle = random.uniform(-0.5, 0.5)
                d = random.choice([-1, 1])
                self.puck['vx'] = 5 * d * math.cos(angle)
                self.puck['vy'] = 5 * math.sin(angle)
            return self.get_state()

        if self.state != 'playing':
            return self.get_state()

        for num in [1, 2]:
            p = self.paddles[num]
            dx = p['target_x'] - p['x']
            dy = p['target_y'] - p['y']
            lerp_speed = 0.35
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
                if self.score[0] >= self.MAX_SCORE or self.score[1] >= self.MAX_SCORE:
                    self.state = 'finished'
                else:
                    self.reset_positions()
                    self.state = 'countdown'
                    self.countdown = 3
                    self.start_time = time.time()
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
                puck['vx'] += pad['vx'] * 0.016 * 0.5
                puck['vy'] += pad['vy'] * 0.016 * 0.5

    def get_state(self):
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
            'elapsed': round(time.time() - self.start_time, 1) if self.start_time else 0
        }

    def get_winner(self):
        if self.score[0] > self.score[1]:
            return 1
        elif self.score[1] > self.score[0]:
            return 2
        return 0