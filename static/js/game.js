var canvas = document.getElementById('game-canvas');
var ctx = canvas.getContext('2d');

var myNum = 0;
var gs = null;
var connected = false;
var sock = null;

var FW = 800, FH = 500, PR = 30, PKR = 15;
var GW = 140, GY1 = (FH - GW) / 2, GY2 = (FH + GW) / 2;

var myX = 80, myY = FH / 2, mouseX = 400, mouseY = 250, lastSend = 0;
var lastMyX = myX, lastMyY = myY;

var puck = {x: FW / 2, y: FH / 2, vx: 0, vy: 0};
var serverPuck = {x: FW / 2, y: FH / 2, vx: 0, vy: 0, receivedAt: 0, packetGap: 16};
var enemyPad = {x: FW - 80, y: FH / 2};
var drawPuck = {x: FW / 2, y: FH / 2};
var drawEnemy = {x: FW - 80, y: FH / 2};

var score = [0, 0];
var goalFlash = 0;
var trail = [];
var TRAIL_MAX = 16;

var p2pActive = false;
var isHost = false;
var lastFrameAt = performance.now();
var lastPuckPacketAt = 0;
var renderPuckVX = 0;
var renderPuckVY = 0;
var lastLocalPuckTouchAt = 0;
var touchActive = false;
var lastTouchAt = 0;
var announcementHideTimer = null;
var goalCelebrationTimers = [];
var soundBank = {};
var lastBounceSoundAt = 0;
var currentRoomState = 'waiting';

var imgs = {};
var bgImg = null;
var GOAL_SIDE_IMAGES = {
    '1': '/static/testimage/images/hockey_goal1.png',
    '2': '/static/testimage/images/hockey_goal2.png'
};
var WINNER_SIDE_IMAGES = {
    '1': '/static/testimage/ui/hockey_winner1.png',
    '2': '/static/testimage/ui/hockey_winner2.png'
};

function ldImg(k, u) {
    if (!u) return;
    var i = new Image();
    i.crossOrigin = 'anonymous';
    i.onload = function() { imgs[k] = i; };
    i.src = u;
}

if (typeof SKIN_IMAGES !== 'undefined' && SKIN_IMAGES) {
    for (var id in SKIN_IMAGES) ldImg(id + '_paddle', SKIN_IMAGES[id]);
}
for (var goalSide in GOAL_SIDE_IMAGES) ldImg('goal_side_' + goalSide, GOAL_SIDE_IMAGES[goalSide]);
for (var winnerSide in WINNER_SIDE_IMAGES) ldImg('winner_side_' + winnerSide, WINNER_SIDE_IMAGES[winnerSide]);

if (typeof BG_IMAGE_URL !== 'undefined' && BG_IMAGE_URL) {
    var bi = new Image();
    bi.crossOrigin = 'anonymous';
    bi.onload = function() { bgImg = bi; };
    bi.src = BG_IMAGE_URL;
}

var SK = {
    kompot: {p: '#ff8844', s: '#cc5500', f: '\uD83D\uDC31'},
    karamelka: {p: '#ff69b4', s: '#cc3388', f: '\uD83C\uDF80'},
    korzhik: {p: '#88bb44', s: '#558822', f: '\u26BD'},
    papa: {p: '#4466aa', s: '#223366', f: '\uD83D\uDC54'},
    mama: {p: '#dd66aa', s: '#993366', f: '\uD83D\uDC90'},
    babushka: {p: '#996688', s: '#664455', f: '\uD83C\uDF6A'},
    dedushka: {p: '#888866', s: '#555544', f: '\uD83C\uDFA3'},
    nuke_kompot: {p: '#ff2200', s: '#ff8800', f: '\uD83D\uDD25'},
    cyber_karamelka: {p: '#00ffcc', s: '#0088aa', f: '\uD83E\uDD16'},
    lyapochka: {p: '#ff8ca8', s: '#c24b73', f: '\uD83C\uDF80'},
    bantik: {p: '#ff6bb9', s: '#7f4dff', f: '\u2728'}
};

var SOUND_FILES = {
    goal: '/static/sounds/goal.mp3',
    lose: '/static/sounds/if_prosral_katky.mp3',
    gameIntro: '/static/sounds/pered_startom_igri.mp3',
    roundIntro: '/static/sounds/pered_startom_raunda.mp3',
    puck: '/static/sounds/zvuk_shaiba.mp3'
};

function soundEnabled() {
    try {
        return localStorage.getItem('kotayra_sound_enabled') === '1';
    } catch (e) {
        return false;
    }
}

function ensureGameSound(name) {
    if (!soundBank[name] && SOUND_FILES[name]) {
        if (name === 'puck') {
            soundBank[name] = [];
            for (var i = 0; i < 6; i++) {
                var puckAudio = new Audio(SOUND_FILES[name]);
                puckAudio.preload = 'auto';
                puckAudio.volume = 0.42;
                soundBank[name].push(puckAudio);
            }
        } else {
            soundBank[name] = new Audio(SOUND_FILES[name]);
            soundBank[name].preload = 'auto';
            soundBank[name].volume = 0.92;
        }
    }
    return soundBank[name] || null;
}

function playGameSound(name) {
    if (!soundEnabled()) return Promise.resolve(false);
    var soundEntry = ensureGameSound(name);
    if (!soundEntry) return Promise.resolve(false);

    if (Array.isArray(soundEntry)) {
        var available = null;
        for (var i = 0; i < soundEntry.length; i++) {
            if (soundEntry[i].paused || soundEntry[i].ended) {
                available = soundEntry[i];
                break;
            }
        }
        if (!available && soundEntry.length < 12) {
            available = new Audio(SOUND_FILES[name]);
            available.preload = 'auto';
            available.volume = 0.42;
            soundEntry.push(available);
        }
        if (!available) return Promise.resolve(false);
        try {
            available.currentTime = 0;
            var pooledPlay = available.play();
            if (pooledPlay && typeof pooledPlay.then === 'function') {
                return pooledPlay.then(function() { return true; }).catch(function() { return false; });
            }
            return Promise.resolve(true);
        } catch (e) {
            return Promise.resolve(false);
        }
    }

    try {
        var audio = soundEntry;
        audio.pause();
        audio.currentTime = 0;
        var playResult = audio.play();
        if (playResult && typeof playResult.then === 'function') {
            return playResult.then(function() { return true; }).catch(function() { return false; });
        }
        return Promise.resolve(true);
    } catch (e) {
        return Promise.resolve(false);
    }
}

function playBounceSound() {
    if (!soundEnabled()) return;
    var now = performance.now();
    if (now - lastBounceSoundAt < 18) return;
    lastBounceSoundAt = now;
    playGameSound('puck');
}

function shouldPlayRemoteBounce(prevVX, prevVY, nextVX, nextVY) {
    var prevSpeed = Math.sqrt(prevVX * prevVX + prevVY * prevVY);
    var nextSpeed = Math.sqrt(nextVX * nextVX + nextVY * nextVY);
    if (prevSpeed < 1.2 || nextSpeed < 1.2) return false;

    var dot = (prevVX * nextVX + prevVY * nextVY) / Math.max(0.0001, prevSpeed * nextSpeed);
    var deltaVX = Math.abs(nextVX - prevVX);
    var deltaVY = Math.abs(nextVY - prevVY);
    var speedDelta = Math.abs(nextSpeed - prevSpeed);

    return dot < 0.9 || deltaVX > 2 || deltaVY > 2 || speedDelta > 2.1;
}

function playCountdownIntro(kind) {
    playGameSound(kind === 'round' ? 'roundIntro' : 'gameIntro');
}

for (var soundName in SOUND_FILES) {
    ensureGameSound(soundName);
}

function initCanvas() {
    var c = document.getElementById('game-container');
    var h = document.querySelector('.game-hud');
    canvas.width = c.clientWidth;
    canvas.height = c.clientHeight - (h ? h.offsetHeight : 50);
}
window.addEventListener('resize', initCanvas);
window.addEventListener('orientationchange', function() { setTimeout(initCanvas, 200); });
initCanvas();

function fmt(s) { var m = Math.floor(s / 60), sec = Math.floor(s % 60); return m + ':' + (sec < 10 ? '0' : '') + sec; }
function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
function lerp(a, b, t) { return a + (b - a) * t; }

function getGamePos(cx, cy) {
    var r = canvas.getBoundingClientRect();
    return { x: (cx - r.left) * (FW / canvas.width), y: (cy - r.top) * (FH / canvas.height) };
}

canvas.addEventListener('mousemove', function(e) {
    var p = getGamePos(e.clientX, e.clientY);
    mouseX = p.x;
    mouseY = p.y;
});
canvas.addEventListener('touchmove', function(e) {
    e.preventDefault();
    touchActive = true;
    lastTouchAt = performance.now();
    var t = e.touches[0];
    var p = getGamePos(t.clientX, t.clientY);
    mouseX = p.x;
    mouseY = p.y;
}, {passive: false});
canvas.addEventListener('touchstart', function(e) {
    e.preventDefault();
    touchActive = true;
    lastTouchAt = performance.now();
    var t = e.touches[0];
    var p = getGamePos(t.clientX, t.clientY);
    mouseX = p.x;
    mouseY = p.y;
}, {passive: false});
document.addEventListener('touchmove', function(e) {
    if (document.getElementById('game-canvas')) e.preventDefault();
}, {passive: false});

function toast(msg) {
    var old = document.getElementById('game-toast');
    if (old) old.remove();
    var t = document.createElement('div');
    t.id = 'game-toast';
    t.style.cssText = 'position:fixed;top:80px;left:50%;transform:translateX(-50%);background:rgba(255,255,255,.95);color:#1a1a2e;padding:12px 24px;border-radius:12px;font-weight:700;z-index:9999;font-family:Manrope,sans-serif;border:2px solid #1a1a2e;box-shadow:0 4px 18px rgba(0,0,0,0.2)';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function() { if (t.parentNode) t.remove(); }, 3000);
}

function updateDisconnectOverlay(state) {
    var overlay = document.getElementById('disconnect-overlay');
    if (!overlay) return;
    var info = state && state.disconnect;
    if (info && info.active) {
        document.getElementById('disconnect-title').textContent = (info.username || 'Игрок') + ' Отключился!';
        document.getElementById('disconnect-seconds').textContent = Math.max(0, info.seconds_left || 0);
        overlay.style.display = 'flex';
    } else {
        overlay.style.display = 'none';
    }
}

function showGameAnnouncement(data) {
    var overlay = document.getElementById('game-announcement-overlay');
    if (!overlay) return;
    overlay.dataset.level = data.level || 'info';
    document.getElementById('game-announcement-title').textContent = data.title || 'Важное объявление';
    document.getElementById('game-announcement-message').textContent = data.message || '';
    overlay.style.display = 'flex';
    clearTimeout(announcementHideTimer);
    announcementHideTimer = setTimeout(function() {
        overlay.style.display = 'none';
    }, Math.max(1000, (data.duration_seconds || 10) * 1000));
}

function getCelebrationPalette(skin) {
    var map = {
        karamelka: { fill: 'linear-gradient(90deg, #ff9ac7, #ff5ca8)', overlay: 'rgba(255, 108, 160, 0.75)' },
        korzhik: { fill: 'linear-gradient(90deg, #53a7ff, #1c66d8)', overlay: 'rgba(55, 136, 255, 0.75)' },
        kompot: { fill: 'linear-gradient(90deg, #ff5a4d, #c81f27)', overlay: 'rgba(224, 55, 55, 0.75)' },
        dedushka: { fill: 'linear-gradient(90deg, #8de7ff, #46b8ff)', overlay: 'rgba(84, 200, 255, 0.75)' },
        babushka: { fill: 'linear-gradient(90deg, #aa7cff, #6a4bd7)', overlay: 'rgba(128, 87, 241, 0.75)' },
        mama: { fill: 'linear-gradient(90deg, #ff465b, #1a1a1a)', overlay: 'rgba(149, 18, 35, 0.75)' },
        papa: { fill: 'linear-gradient(90deg, #111111, #ffca28)', overlay: 'rgba(118, 97, 12, 0.75)' },
        lyapochka: { fill: 'linear-gradient(90deg, #ffb0c4, #ff6c98)', overlay: 'rgba(255, 116, 170, 0.75)' },
        bantik: { fill: 'linear-gradient(90deg, #ff7ccc, #7f4dff)', overlay: 'rgba(160, 92, 240, 0.75)' },
        nuke_kompot: { fill: 'linear-gradient(90deg, #ff2b2b, #111111)', overlay: 'rgba(146, 18, 18, 0.76)' },
        cyber_karamelka: { fill: 'linear-gradient(90deg, #00f2ff, #0079ff)', overlay: 'rgba(20, 131, 255, 0.75)' }
    };
    return map[skin] || { fill: 'linear-gradient(90deg, #ff7a33, #ff934d)', overlay: 'rgba(255, 122, 51, 0.75)' };
}

function clearGoalCelebrationTimers() {
    while (goalCelebrationTimers.length) {
        clearTimeout(goalCelebrationTimers.pop());
    }
}

function applyCelebrationSkin(panelId, imageId, fallbackId, skin, username) {
    var panel = document.getElementById(panelId);
    var image = document.getElementById(imageId);
    var fallback = document.getElementById(fallbackId);
    if (!panel || !image || !fallback) return;
    var palette = getCelebrationPalette(skin);
    panel.style.setProperty('--celebration-fill', palette.fill);
    var skinUrl = skin ? '/static/images/processed/' + skin + '_ui.png' : '';
    if (skinUrl) {
        image.src = skinUrl;
        image.style.display = 'block';
        fallback.style.display = 'none';
        image.onerror = function() {
            image.style.display = 'none';
            fallback.style.display = 'grid';
        };
    } else {
        image.style.display = 'none';
        fallback.style.display = 'grid';
    }
    fallback.textContent = (username || 'Игрок').charAt(0) || 'K';
}

function showGoalCelebration(player, sideKey) {
    var overlay = document.getElementById('goal-celebration-overlay');
    var panel = document.getElementById('goal-celebration-panel');
    var badge = document.getElementById('goal-badge-image');
    if (!overlay || !panel || !player) return;

    clearGoalCelebrationTimers();
    overlay.style.background = getCelebrationPalette(player.skin).overlay;
    applyCelebrationSkin('goal-celebration-panel', 'goal-skin-image', 'goal-skin-fallback', player.skin, player.username);
    if (badge) badge.src = GOAL_SIDE_IMAGES[String(sideKey || '1')] || GOAL_SIDE_IMAGES['1'];
    document.getElementById('goal-player-name').textContent = player.username || 'Игрок';
    document.getElementById('goal-title').classList.remove('visible');
    overlay.style.display = 'flex';
    panel.classList.remove('active');
    void panel.offsetWidth;
    panel.classList.add('active');

    goalCelebrationTimers.push(setTimeout(function() {
        document.getElementById('goal-title').classList.add('visible');
        playGameSound('goal');
    }, 2000));
    goalCelebrationTimers.push(setTimeout(function() {
        overlay.style.display = 'none';
        document.getElementById('goal-title').classList.remove('visible');
    }, 3500));
}

function updateHUD(s) {
    if (!s) return;
    document.getElementById('score-p1').textContent = score[0];
    document.getElementById('score-p2').textContent = score[1];
    if (s.players) {
        if (s.players['1']) {
            document.getElementById('hud-p1-name').textContent = s.players['1'].username;
            document.getElementById('hud-p1-elo').textContent = s.players['1'].elo + ' ELO';
        }
        if (s.players['2']) {
            document.getElementById('hud-p2-name').textContent = s.players['2'].username;
            document.getElementById('hud-p2-elo').textContent = s.players['2'].elo + ' ELO';
        }
    }
    if (s.elapsed) document.getElementById('game-timer').textContent = fmt(s.elapsed);
}

function showResult(result) {
    if (typeof P2P !== 'undefined') P2P.close();
    updateDisconnectOverlay(null);
    clearGoalCelebrationTimers();
    document.getElementById('goal-celebration-overlay').style.display = 'none';
    document.getElementById('result-overlay').style.display = 'flex';
    var w = myNum === result.winner;
    var my = myNum === 1 ? result.player1 : result.player2;
    var winnerSide = String(result.winner || '1');
    var winnerImg = document.getElementById('result-winner-image');
    var tl = document.getElementById('result-title');
    document.getElementById('result-overlay').style.background = getCelebrationPalette(my.active_skin).overlay;
    applyCelebrationSkin('result-card', 'result-skin-image', 'result-skin-fallback', my.active_skin, my.username);
    if (winnerImg) winnerImg.src = WINNER_SIDE_IMAGES[winnerSide] || WINNER_SIDE_IMAGES['1'];
    document.getElementById('result-player-name').textContent = my.username;
    tl.textContent = w ? 'WIN!' : 'ПРОСРАЛ';
    document.getElementById('result-score-p1').textContent = result.score[0];
    document.getElementById('result-score-p2').textContent = result.score[1];
    var el = document.getElementById('result-elo');
    el.textContent = (my.elo_change >= 0 ? '+' : '') + my.elo_change + ' ELO';
    el.style.color = my.elo_change >= 0 ? '#52b788' : '#e63946';
    document.getElementById('result-coins').textContent = '+' + my.coins_earned + ' монет';
    document.getElementById('result-rank').textContent = my.new_rank + ' (' + my.elo + ')';
    if (!w) playGameSound('lose');
}

function syncFull(state) {
    if (!state) return;
    var my = String(myNum), en = myNum === 1 ? '2' : '1';
    if (state.paddles && state.paddles[my]) {
        myX = state.paddles[my].x;
        myY = state.paddles[my].y;
        mouseX = myX;
        mouseY = myY;
        lastMyX = myX;
        lastMyY = myY;
    }
    if (state.paddles && state.paddles[en]) {
        enemyPad.x = drawEnemy.x = state.paddles[en].x;
        enemyPad.y = drawEnemy.y = state.paddles[en].y;
    }
    if (state.puck) {
        serverPuck.x = state.puck.x;
        serverPuck.y = state.puck.y;
        serverPuck.vx = state.puck.vx || 0;
        serverPuck.vy = state.puck.vy || 0;
        serverPuck.receivedAt = performance.now();
        serverPuck.packetGap = 16;
        puck.x = drawPuck.x = state.puck.x;
        puck.y = drawPuck.y = state.puck.y;
        puck.vx = state.puck.vx || 0;
        puck.vy = state.puck.vy || 0;
        renderPuckVX = puck.vx;
        renderPuckVY = puck.vy;
    }
    if (state.score) score = [state.score[0], state.score[1]];
    trail = [];
    updateDisconnectOverlay(state);
}

function onP2PMsg(msg) {
    if (msg.t === 'pos') {
        enemyPad.x = msg.x;
        enemyPad.y = msg.y;
    }
}

function initGame() {
    sock = io({transports: ['websocket', 'polling'], reconnection: true});

    sock.on('connect', function() {
        sock.emit('join_game', {room_id: ROOM_ID});
    });
    sock.on('disconnect', function() {
        connected = false;
    });
    sock.on('reconnect', function() { sock.emit('join_game', {room_id: ROOM_ID}); });

    sock.on('game_joined', function(d) {
        myNum = d.player_number;
        gs = d.state;
        connected = true;
        isHost = (myNum === 1);
        document.getElementById('waiting-overlay').style.display = 'none';
        syncFull(d.state);
        currentRoomState = d.state && d.state.state ? d.state.state : 'waiting';
        if (currentRoomState === 'countdown') {
            playCountdownIntro(d.state.countdown_kind);
        }

        if (typeof P2P !== 'undefined') {
            P2P.onConnect = function() {
                p2pActive = true;
                toast('P2P подключено');
            };
            P2P.onDisconnect = function() {
                p2pActive = false;
            };
            P2P.onMessage = onP2PMsg;
            P2P.init(sock, ROOM_ID, isHost);
        }
    });

    sock.on('game_state', function(state) {
        var previousRoomState = currentRoomState;
        currentRoomState = state.state;
        gs = state;

        if (state.puck) {
            var prevServerVX = serverPuck.vx;
            var prevServerVY = serverPuck.vy;
            var packetNow = performance.now();
            serverPuck.packetGap = lastPuckPacketAt ? (packetNow - lastPuckPacketAt) : 16;
            lastPuckPacketAt = packetNow;
            serverPuck.x = state.puck.x;
            serverPuck.y = state.puck.y;
            serverPuck.vx = state.puck.vx || 0;
            serverPuck.vy = state.puck.vy || 0;
            serverPuck.receivedAt = packetNow;
            puck.x = state.puck.x;
            puck.y = state.puck.y;
            puck.vx = state.puck.vx || 0;
            puck.vy = state.puck.vy || 0;
            if (state.state === 'playing' && shouldPlayRemoteBounce(prevServerVX, prevServerVY, serverPuck.vx, serverPuck.vy)) {
                playBounceSound();
            }
        }

        var en = myNum === 1 ? '2' : '1';
        if (!p2pActive && state.paddles && state.paddles[en]) {
            enemyPad.x = state.paddles[en].x;
            enemyPad.y = state.paddles[en].y;
        }

        if (state.score) {
            var scorerKey = null;
            if (state.score[0] !== score[0] || state.score[1] !== score[1]) {
                goalFlash = 25;
                trail = [];
                if (state.score[0] > score[0]) scorerKey = '1';
                if (state.score[1] > score[1]) scorerKey = '2';
            }
            score = [state.score[0], state.score[1]];
            if (scorerKey && state.players && state.players[scorerKey]) {
                showGoalCelebration(state.players[scorerKey], scorerKey);
            }
        }

        if (state.state === 'countdown' && previousRoomState !== 'countdown') {
            playCountdownIntro(state.countdown_kind);
        }

        if (state.state === 'countdown') {
            document.getElementById('countdown-overlay').style.display = 'flex';
            document.getElementById('countdown-num').textContent = state.countdown || '...';
            syncFull(state);
        } else {
            document.getElementById('countdown-overlay').style.display = 'none';
        }

        updateDisconnectOverlay(state);
        updateHUD(state);
    });

    sock.on('game_finished', showResult);
    sock.on('player_disconnected', function(data) {
        toast((data && data.username ? data.username : 'Противник') + ' отключился');
    });
    sock.on('player_reconnected', function(data) {
        updateDisconnectOverlay(null);
        toast((data && data.username ? data.username : 'Игрок') + ' переподключился');
    });
    sock.on('admin_announcement', function(data) {
        showGameAnnouncement(data);
    });
    sock.on('game_error', function(d) {
        var w = document.querySelector('.waiting-card');
        if (w) {
            w.innerHTML = '<p style="color:#e63946;font-weight:700">' + d.error +
                '</p><a href="/lobby" class="btn btn-primary" style="margin-top:16px">Назад</a>';
        }
    });
}

function applyLocalPuckResponse(frameNow, dt) {
    if (!gs || gs.state !== 'playing') return;

    var padVX = myX - lastMyX;
    var padVY = myY - lastMyY;
    var startX = lastMyX;
    var startY = lastMyY;
    lastMyX = myX;
    lastMyY = myY;

    var segDX = myX - startX;
    var segDY = myY - startY;
    var segLenSq = segDX * segDX + segDY * segDY;
    var t = 1;
    if (segLenSq > 0.0001) {
        t = ((drawPuck.x - startX) * segDX + (drawPuck.y - startY) * segDY) / segLenSq;
        t = clamp(t, 0, 1);
    }

    var closestX = startX + segDX * t;
    var closestY = startY + segDY * t;
    var dx = drawPuck.x - closestX;
    var dy = drawPuck.y - closestY;
    var dist = Math.sqrt(dx * dx + dy * dy) || 0.001;
    var minDist = PR + PKR + 7;
    var padSpeed = Math.sqrt(padVX * padVX + padVY * padVY);
    var puckSpeed = Math.sqrt(renderPuckVX * renderPuckVX + renderPuckVY * renderPuckVY);
    var approachDot = dx * renderPuckVX + dy * renderPuckVY;

    if (dist < minDist && (approachDot < 0 || padSpeed > 0.9 || segLenSq > 16)) {
        var nx = dx / dist;
        var ny = dy / dist;
        drawPuck.x = closestX + nx * minDist;
        drawPuck.y = closestY + ny * minDist;

        var boost = Math.max(3.1, padSpeed * 1.05 + puckSpeed * 0.34);
        renderPuckVX = nx * boost + padVX * 0.62;
        renderPuckVY = ny * boost + padVY * 0.62;
        serverPuck.vx = renderPuckVX;
        serverPuck.vy = renderPuckVY;
        serverPuck.receivedAt = frameNow - dt * 420;
        lastLocalPuckTouchAt = frameNow;

        drawPuck.x += renderPuckVX * 0.24;
        drawPuck.y += renderPuckVY * 0.24;
        playBounceSound();
    }
}

function applyLocalWallResponse(frameNow) {
    if (drawPuck.y - PKR < 0) {
        drawPuck.y = PKR;
        renderPuckVY = Math.abs(renderPuckVY) * 0.86;
        lastLocalPuckTouchAt = frameNow;
        playBounceSound();
    } else if (drawPuck.y + PKR > FH) {
        drawPuck.y = FH - PKR;
        renderPuckVY = -Math.abs(renderPuckVY) * 0.86;
        lastLocalPuckTouchAt = frameNow;
        playBounceSound();
    }

    if (drawPuck.x - PKR < 0 && !(GY1 < drawPuck.y && drawPuck.y < GY2)) {
        drawPuck.x = PKR;
        renderPuckVX = Math.abs(renderPuckVX) * 0.86;
        lastLocalPuckTouchAt = frameNow;
        playBounceSound();
    } else if (drawPuck.x + PKR > FW && !(GY1 < drawPuck.y && drawPuck.y < GY2)) {
        drawPuck.x = FW - PKR;
        renderPuckVX = -Math.abs(renderPuckVX) * 0.86;
        lastLocalPuckTouchAt = frameNow;
        playBounceSound();
    }
}

function gameLoop() {
    update();
    render();
    requestAnimationFrame(gameLoop);
}

function update() {
    var frameNow = performance.now();
    var dt = Math.min((frameNow - lastFrameAt) / 1000, 0.05);
    lastFrameAt = frameNow;

    if (!gs) return;
    var playing = (gs.state === 'playing');

    if (playing) {
        var mx = mouseX, my = mouseY;
        if (myNum === 1) mx = clamp(mx, PR, FW / 2 - PR);
        else mx = clamp(mx, FW / 2 + PR, FW - PR);
        my = clamp(my, PR, FH - PR);

        var touchSmoothing = touchActive && (frameNow - lastTouchAt < 1400);
        myX = touchSmoothing ? lerp(myX, mx, 0.42) : mx;
        myY = touchSmoothing ? lerp(myY, my, 0.42) : my;

        var now = performance.now();
        if (now - lastSend > 8) {
            lastSend = now;
            if (sock && connected) {
                sock.volatile.emit('paddle_move', {
                    x: Math.round(mx * 10) / 10,
                    y: Math.round(my * 10) / 10
                });
            }
            if (p2pActive && typeof P2P !== 'undefined') {
                P2P.send({t: 'pos', x: Math.round(mx * 10) / 10, y: Math.round(my * 10) / 10});
            }
        }
    }

    // When server packets arrive very late, trust local puck motion more than stale snapshots.
    var realPacketAge = (frameNow - serverPuck.receivedAt) / 1000;
    var packetAge = Math.min(realPacketAge, 0.18);
    var packetFrames = Math.min((serverPuck.packetGap || 16) / 16.6667, 4);
    var predictFactor = 1.35 + packetFrames * 0.33;
    var latencyShield = clamp((realPacketAge - 0.05) / 0.3, 0, 1);
    var recentLocalTouch = (frameNow - lastLocalPuckTouchAt) < 950;
    var localControl = recentLocalTouch ? Math.max(latencyShield, 0.82) : Math.max(0.18, latencyShield * 0.92);
    var serverVelocityBlend = recentLocalTouch ? 0.015 : (packetAge > 0.08 ? 0.06 : 0.14);
    renderPuckVX = lerp(renderPuckVX, serverPuck.vx, serverVelocityBlend * (1 - latencyShield * 0.85));
    renderPuckVY = lerp(renderPuckVY, serverPuck.vy, serverVelocityBlend * (1 - latencyShield * 0.85));
    var puckTargetX = serverPuck.x + serverPuck.vx * packetAge * 60 * predictFactor;
    var puckTargetY = serverPuck.y + serverPuck.vy * packetAge * 60 * predictFactor;
    var puckGap = Math.sqrt(Math.pow(puckTargetX - drawPuck.x, 2) + Math.pow(puckTargetY - drawPuck.y, 2));

    if (!playing || gs.state === 'countdown') {
        drawPuck.x = lerp(drawPuck.x, puck.x, 0.45);
        drawPuck.y = lerp(drawPuck.y, puck.y, 0.45);
    } else {
        var serverPull = (puckGap > 90 ? 0.24 : 0.14) * (1 - localControl);
        if (serverPull > 0.001) {
            drawPuck.x = lerp(drawPuck.x, puckTargetX, serverPull);
            drawPuck.y = lerp(drawPuck.y, puckTargetY, serverPull);
        }

        var localDrift = 0.22 + localControl * 1.06;
        drawPuck.x += renderPuckVX * dt * 60 * localDrift;
        drawPuck.y += renderPuckVY * dt * 60 * localDrift;

        if (packetAge > 0.1 || latencyShield > 0.2) {
            renderPuckVX *= 0.9984 - Math.min(0.0012, latencyShield * 0.0008);
            renderPuckVY *= 0.9984 - Math.min(0.0012, latencyShield * 0.0008);
        }

        applyLocalPuckResponse(frameNow, dt);
        applyLocalWallResponse(frameNow);
    }

    drawEnemy.x = lerp(drawEnemy.x, enemyPad.x, p2pActive ? 0.7 : 0.45);
    drawEnemy.y = lerp(drawEnemy.y, enemyPad.y, p2pActive ? 0.7 : 0.45);

    trail.push({x: drawPuck.x, y: drawPuck.y});
    if (trail.length > TRAIL_MAX) trail.shift();
    if (goalFlash > 0) goalFlash--;
}

function render() {
    if (!gs) return;
    var W = canvas.width, H = canvas.height, sx = W / FW, sy = H / FH, s = Math.min(sx, sy);
    ctx.clearRect(0, 0, W, H);

    ctx.fillStyle = '#e8f4f8';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(162,210,255,0.35)';
    var sw = W / 8;
    for (var i = 0; i < 8; i += 2) ctx.fillRect(i * sw, 0, sw, H);
    if (bgImg) { ctx.globalAlpha = 0.3; ctx.drawImage(bgImg, 0, 0, W, H); ctx.globalAlpha = 1; }

    var pad = 6 * s;
    var bw = 4 * s;
    var cr = 24 * s;
    var field = { x: pad, y: pad, w: W - pad * 2, h: H - pad * 2 };
    var fsx = field.w / FW;
    var fsy = field.h / FH;
    var fs = Math.min(fsx, fsy);
    function tx(x) { return field.x + x * fsx; }
    function ty(y) { return field.y + y * fsy; }

    ctx.strokeStyle = '#3a7bd5'; ctx.lineWidth = bw;
    rRect(ctx, field.x, field.y, field.w, field.h, cr); ctx.stroke();
    ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 2;
    rRect(ctx, field.x, field.y, field.w, field.h, cr); ctx.stroke();

    ctx.strokeStyle = '#4895ef'; ctx.lineWidth = 3 * s;
    ctx.beginPath(); ctx.moveTo(tx(FW / 2), field.y + bw); ctx.lineTo(tx(FW / 2), field.y + field.h - bw); ctx.stroke();
    ctx.beginPath(); ctx.arc(tx(FW / 2), ty(FH / 2), 50 * fs, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(tx(FW / 2), ty(FH / 2), 5 * fs, 0, Math.PI * 2); ctx.fillStyle = '#4895ef'; ctx.fill();

    var gy1 = ty(GY1), gy2 = ty(GY2), gw = 10 * fs;
    drawGoalNet(field.x, gy1, gw, gy2 - gy1, '#e63946', fs);
    drawGoalNet(field.x + field.w - gw, gy1, gw, gy2 - gy1, '#4895ef', fs);

    var fo = [[FW * .2, FH * .3], [FW * .2, FH * .7], [FW * .8, FH * .3], [FW * .8, FH * .7]];
    for (var fi = 0; fi < fo.length; fi++) {
        var fx = tx(fo[fi][0]), fy = ty(fo[fi][1]), fr = 18 * fs;
        ctx.beginPath(); ctx.arc(fx, fy, fr, 0, Math.PI * 2);
        ctx.strokeStyle = '#e63946'; ctx.lineWidth = 2 * fs; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(fx - fr * .5, fy - fr * .5); ctx.lineTo(fx + fr * .5, fy + fr * .5);
        ctx.moveTo(fx + fr * .5, fy - fr * .5); ctx.lineTo(fx - fr * .5, fy + fr * .5); ctx.stroke();
    }

    if (goalFlash > 0) { ctx.fillStyle = 'rgba(255,255,255,' + (goalFlash / 40) + ')'; ctx.fillRect(0, 0, W, H); }

    for (var ti = 1; ti < trail.length; ti++) {
        var ta = (ti / trail.length) * 0.2, tr2 = (ti / trail.length) * PKR * fs * 0.5;
        ctx.beginPath(); ctx.arc(tx(trail[ti].x), ty(trail[ti].y), tr2, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(26,26,46,' + ta + ')'; ctx.fill();
    }

    var px = tx(drawPuck.x), py = ty(drawPuck.y), pr = PKR * fs;
    ctx.beginPath();
    ctx.arc(px, py + pr * 0.34, pr * 0.96, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(px, py, pr, 0, Math.PI * 2);
    ctx.fillStyle = '#101010';
    ctx.fill();
    ctx.strokeStyle = '#060606';
    ctx.lineWidth = Math.max(1.5, fs * 1.6);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(px, py, pr * 0.78, 0, Math.PI * 2);
    ctx.fillStyle = '#272729';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(px, py, pr * 0.58, 0, Math.PI * 2);
    ctx.fillStyle = '#343438';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(px - pr * 0.2, py - pr * 0.2, pr * 0.2, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fill();

    var myN = String(myNum), enN = myNum === 1 ? '2' : '1';
    drawPad(tx(drawEnemy.x), ty(drawEnemy.y), PR * fs, enN, false, fs);
    drawPad(tx(myX), ty(myY), PR * fs, myN, true, fs);

    ctx.fillStyle = p2pActive ? '#52b788' : '#e6b800';
    ctx.beginPath(); ctx.arc(W - 20, 20, 6, 0, Math.PI * 2); ctx.fill();
    ctx.font = 'bold 10px Manrope,sans-serif'; ctx.textAlign = 'right';
    ctx.fillText(p2pActive ? 'P2P' : 'WS', W - 30, 24);
}

function rRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.lineTo(x + w - r, y);
    c.arcTo(x + w, y, x + w, y + r, r);
    c.lineTo(x + w, y + h - r);
    c.arcTo(x + w, y + h, x + w - r, y + h, r);
    c.lineTo(x + r, y + h);
    c.arcTo(x, y + h, x, y + h - r, r);
    c.lineTo(x, y + r);
    c.arcTo(x, y, x + r, y, r);
    c.closePath();
}

function drawGoalNet(x, y, w, h, color, s) {
    ctx.fillStyle = color + '40'; ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    var step = 8 * s;
    for (var gy = y; gy < y + h; gy += step) { ctx.beginPath(); ctx.moveTo(x, gy); ctx.lineTo(x + w, gy); ctx.stroke(); }
    for (var gx = x; gx < x + w; gx += step) { ctx.beginPath(); ctx.moveTo(gx, y); ctx.lineTo(gx, y + h); ctx.stroke(); }
    ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
}

function drawPad(x, y, r, numStr, isMe, s) {
    var sk = 'kompot';
    if (gs.players && gs.players[numStr]) sk = gs.players[numStr].skin || 'kompot';
    var skin = SK[sk] || SK.kompot;
    var tc = numStr === '1' ? '#e63946' : '#4895ef';
    ctx.beginPath(); ctx.arc(x + 2, y + 4, r + 2, 0, Math.PI * 2); ctx.fillStyle = 'rgba(0,0,0,0.2)'; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, r + 4 * s, 0, Math.PI * 2); ctx.fillStyle = tc; ctx.fill();
    ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 3; ctx.stroke();
    var img = imgs[sk + '_paddle'];
    if (img) {
        ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.clip();
        ctx.drawImage(img, x - r, y - r, r * 2, r * 2); ctx.restore();
    } else {
        var g = ctx.createRadialGradient(x - r * .2, y - r * .2, 0, x, y, r);
        g.addColorStop(0, skin.p); g.addColorStop(1, skin.s);
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();
        ctx.font = Math.round(r * 1.1) + 'px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(skin.f, x, y);
    }
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 2.5; ctx.stroke();
    if (gs.players && gs.players[numStr]) {
        var name = gs.players[numStr].username;
        var fs = Math.max(10, Math.round(12 * s));
        ctx.font = 'bold ' + fs + 'px Manrope,sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        var tw = ctx.measureText(name).width;
        var lh = Math.round(16 * s), ly = y + r + Math.round(6 * s);
        ctx.fillStyle = 'rgba(255,255,255,0.85)'; ctx.fillRect(x - tw / 2 - 4, ly, tw + 8, lh);
        ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 1.5; ctx.strokeRect(x - tw / 2 - 4, ly, tw + 8, lh);
        ctx.fillStyle = '#1a1a2e'; ctx.fillText(name, x, ly + lh / 2);
    }
    if (isMe) {
        var mfs = Math.max(9, Math.round(10 * s));
        ctx.font = 'bold ' + mfs + 'px Manrope,sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        var meY = y - r - Math.round(12 * s); ctx.strokeStyle = '#1a1a2e'; ctx.lineWidth = 2;
        ctx.strokeText('\u25BC YOU', x, meY); ctx.fillStyle = '#52b788'; ctx.fillText('\u25BC YOU', x, meY);
    }
}

initGame();
gameLoop();
