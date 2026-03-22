var canvas = document.getElementById('game-canvas');
var ctx = canvas.getContext('2d');

var myNum = 0, gs = null, connected = false, sock = null;
var FW = 800, FH = 500, PR = 30, PKR = 15;
var GW = 140, GY1 = (FH - GW) / 2, GY2 = (FH + GW) / 2;

var myX = 80, myY = FH / 2, mouseX = 400, mouseY = 250, lastSend = 0;

var puck = { x: FW / 2, y: FH / 2, vx: 0, vy: 0 };
var serverPuck = { x: FW / 2, y: FH / 2, vx: 0, vy: 0, receivedAt: 0, packetGap: 16 };
var drawPuck = { x: FW / 2, y: FH / 2 };

var enemyPad = { x: FW - 80, y: FH / 2 };
var drawEnemy = { x: FW - 80, y: FH / 2 };

var score = [0, 0];
var goalFlash = 0;
var trail = [];
var TRAIL_MAX = 16;

var p2pActive = false;
var isHost = false;
var prevState = 'waiting';
var lastFrameAt = performance.now();
var lastPuckPacketAt = 0;

var imgs = {};
var bgImg = null;

// ═══════════════════════════════════════════════
// БУФЕР СНАПШОТОВ — ключ к плавности
// Рисуем шайбу с задержкой 80мс, интерполируя
// между двумя серверными позициями.
// ═══════════════════════════════════════════════
var puckBuf = []; // [{t, x, y, vx, vy}]
var INTERP_DELAY = 80; // мс

function ldImg(k, u) {
    if (!u) return;
    var i = new Image();
    i.crossOrigin = 'anonymous';
    i.onload = function () {
        imgs[k] = i;
    };
    i.src = u;
}

function addSnap(x, y, vx, vy) {
    puckBuf.push({ t: performance.now(), x: x, y: y, vx: vx || 0, vy: vy || 0 });
    if (puckBuf.length > 150) puckBuf.shift();
}

function clearBuf(x, y) {
    puckBuf = [];
    puck.x = x || FW / 2;
    puck.y = y || FH / 2;
    drawPuck.x = puck.x;
    drawPuck.y = puck.y;
}

function interpPuck() {
    var len = puckBuf.length;
    if (len === 0) return;
    if (len === 1) {
        puck.x = puckBuf[0].x;
        puck.y = puckBuf[0].y;
        return;
    }

    var renderTime = performance.now() - INTERP_DELAY;

    var a = null, b = null;
    for (var i = len - 1; i >= 1; i--) {
        if (puckBuf[i - 1].t <= renderTime && puckBuf[i].t > renderTime) {
            a = puckBuf[i - 1];
            b = puckBuf[i];
            break;
        }
    }

    if (a && b) {
        var frac = (renderTime - a.t) / (b.t - a.t);
        if (frac < 0) frac = 0;
        if (frac > 1) frac = 1;
        puck.x = a.x + (b.x - a.x) * frac;
        puck.y = a.y + (b.y - a.y) * frac;
    } else if (puckBuf[len - 1].t <= renderTime) {
        var last = puckBuf[len - 1];
        var dt = (renderTime - last.t) / 16.67;
        if (dt > 8) dt = 8;
        puck.x = last.x + last.vx * 3 * dt;
        puck.y = last.y + last.vy * 3 * dt;
        puck.x = clamp(puck.x, PKR, FW - PKR);
        puck.y = clamp(puck.y, PKR, FH - PKR);
    } else {
        puck.x = puckBuf[0].x;
        puck.y = puckBuf[0].y;
    }
}

// ═══ HELPERS ═══

if (typeof SKIN_IMAGES !== 'undefined' && SKIN_IMAGES) {
    for (var id in SKIN_IMAGES) {
        if (Object.prototype.hasOwnProperty.call(SKIN_IMAGES, id)) {
            ldImg(id + '_paddle', SKIN_IMAGES[id]);
        }
    }
}

if (typeof BG_IMAGE_URL !== 'undefined' && BG_IMAGE_URL) {
    var bi = new Image();
    bi.crossOrigin = 'anonymous';
    bi.onload = function () {
        bgImg = bi;
    };
    bi.src = BG_IMAGE_URL;
}

var SK = {
    kompot: { p: '#ff8844', s: '#cc5500', f: '🐱' },
    karamelka: { p: '#ff69b4', s: '#cc3388', f: '🎀' },
    korzhik: { p: '#88bb44', s: '#558822', f: '⚽' },
    papa: { p: '#4466aa', s: '#223366', f: '👔' },
    mama: { p: '#dd66aa', s: '#993366', f: '🌐' },
    babushka: { p: '#996688', s: '#664455', f: '🍪' },
    dedushka: { p: '#888866', s: '#555544', f: '🎣' },
    nuke_kompot: { p: '#ff2200', s: '#ff8800', f: '🔥' },
    cyber_karamelka: { p: '#00ffcc', s: '#0088aa', f: '🤖' }
};

function initCanvas() {
    var c = document.getElementById('game-container');
    var h = document.querySelector('.game-hud');
    canvas.width = c.clientWidth;
    canvas.height = c.clientHeight - (h ? h.offsetHeight : 50);
}

window.addEventListener('resize', initCanvas);
window.addEventListener('orientationchange', function () {
    setTimeout(initCanvas, 200);
});
initCanvas();

function fmt(s) {
    var m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ':' + (sec < 10 ? '0' + sec : sec);
}

function clamp(v, a, b) {
    return v < a ? a : (v > b ? b : v);
}

function lerp(a, b, t) {
    return a + (b - a) * t;
}

function getGamePos(cx, cy) {
    var r = canvas.getBoundingClientRect();
    return {
        x: (cx - r.left) * (FW / canvas.width),
        y: (cy - r.top) * (FH / canvas.height)
    };
}

canvas.addEventListener('mousemove', function (e) {
    var p = getGamePos(e.clientX, e.clientY);
    mouseX = p.x;
    mouseY = p.y;
});

canvas.addEventListener('touchmove', function (e) {
    e.preventDefault();
    var t = e.touches[0];
    var p = getGamePos(t.clientX, t.clientY);
    mouseX = p.x;
    mouseY = p.y;
}, { passive: false });

canvas.addEventListener('touchstart', function (e) {
    e.preventDefault();
    var t = e.touches[0];
    var p = getGamePos(t.clientX, t.clientY);
    mouseX = p.x;
    mouseY = p.y;
}, { passive: false });

document.addEventListener('touchmove', function (e) {
    if (document.getElementById('game-canvas')) e.preventDefault();
}, { passive: false });

function toast(msg) {
    var old = document.getElementById('game-toast');
    if (old) old.remove();

    var t = document.createElement('div');
    t.id = 'game-toast';
    t.style.cssText =
        'position:fixed;top:80px;left:50%;transform:translateX(-50%);background:rgba(255,255,255,.95);color:#1a1a2e;padding:12px 24px;border-radius:12px;font-weight:700;z-index:9999;font-family:Nunito,sans-serif;border:3px solid #1a1a2e;box-shadow:0 4px 0 rgba(0,0,0,0.2)';
    t.textContent = msg;
    document.body.appendChild(t);

    setTimeout(function () {
        if (t.parentNode) t.remove();
    }, 3000);
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

    if (s.elapsed != null) {
        document.getElementById('game-timer').textContent = fmt(s.elapsed);
    }
}

function showResult(result) {
    if (typeof P2P !== 'undefined') P2P.close();

    document.getElementById('result-overlay').style.display = 'flex';

    var w = myNum === result.winner;
    var my = myNum === 1 ? result.player1 : result.player2;

    var tl = document.getElementById('result-title');
    tl.textContent = w ? 'Победа!' : 'Поражение';
    tl.style.color = w ? '#52b788' : '#e63946';

    document.getElementById('result-score-p1').textContent = result.score[0];
    document.getElementById('result-score-p2').textContent = result.score[1];

    var el = document.getElementById('result-elo');
    el.textContent = (my.elo_change >= 0 ? '+' : '') + my.elo_change + ' ELO';
    el.style.color = my.elo_change >= 0 ? '#52b788' : '#e63946';

    document.getElementById('result-coins').textContent = '+' + my.coins_earned + ' монет';
    document.getElementById('result-rank').textContent = my.new_rank + ' (' + my.elo + ')';
}

function snapAll(state) {
    if (!state) return;

    var my = String(myNum);
    var en = myNum === 1 ? '2' : '1';

    if (state.paddles && state.paddles[my]) {
        myX = state.paddles[my].x;
        myY = state.paddles[my].y;
        mouseX = myX;
        mouseY = myY;
    }

    if (state.paddles && state.paddles[en]) {
        enemyPad.x = drawEnemy.x = state.paddles[en].x;
        enemyPad.y = drawEnemy.y = state.paddles[en].y;
    }

    if (state.puck) {
        puck.x = state.puck.x;
        puck.y = state.puck.y;
        puck.vx = state.puck.vx || 0;
        puck.vy = state.puck.vy || 0;

        serverPuck.x = state.puck.x;
        serverPuck.y = state.puck.y;
        serverPuck.vx = state.puck.vx || 0;
        serverPuck.vy = state.puck.vy || 0;
        serverPuck.receivedAt = performance.now();
        serverPuck.packetGap = 16;

        drawPuck.x = state.puck.x;
        drawPuck.y = state.puck.y;
    }

    if (state.score) {
        score = [state.score[0], state.score[1]];
    }

    clearBuf(puck.x, puck.y);
    trail = [];
}

// ═══ SOCKET ═══

function initGame() {
    sock = io({
        transports: ['websocket', 'polling'],
        reconnection: true
    });

    sock.on('connect', function () {
        sock.emit('join_game', { room_id: ROOM_ID });
    });

    sock.on('disconnect', function () {
        connected = false;
    });

    sock.on('reconnect', function () {
        sock.emit('join_game', { room_id: ROOM_ID });
    });

    sock.on('game_joined', function (d) {
        myNum = d.player_number;
        gs = d.state;
        connected = true;
        isHost = (myNum === 1);

        document.getElementById('waiting-overlay').style.display = 'none';
        snapAll(d.state);

        if (typeof P2P !== 'undefined') {
            P2P.onConnect = function () {
                p2pActive = true;
                toast('P2P подключено!');
            };

            P2P.onDisconnect = function () {
                p2pActive = false;
            };

            P2P.onMessage = function (msg) {
                if (msg.t === 'pos') {
                    enemyPad.x = msg.x;
                    enemyPad.y = msg.y;
                }
            };

            P2P.init(sock, ROOM_ID, isHost);
        }
    });

    sock.on('game_state', function (state) {
        gs = state;

        if (state.puck) {
            var packetNow = performance.now();
            serverPuck.packetGap = lastPuckPacketAt ? (packetNow - lastPuckPacketAt) : 16;
            lastPuckPacketAt = packetNow;

            serverPuck.x = state.puck.x;
            serverPuck.y = state.puck.y;
            serverPuck.vx = state.puck.vx || 0;
            serverPuck.vy = state.puck.vy || 0;
            serverPuck.receivedAt = packetNow;
        }

        if (state.state !== prevState) {
            if (state.state === 'countdown' || state.state === 'waiting') {
                clearBuf(state.puck ? state.puck.x : FW / 2, state.puck ? state.puck.y : FH / 2);
            }
            prevState = state.state;
        }

        if (state.state === 'playing' && state.puck) {
            addSnap(state.puck.x, state.puck.y, state.puck.vx, state.puck.vy);
        } else if (state.puck) {
            puck.x = state.puck.x;
            puck.y = state.puck.y;
            puck.vx = state.puck.vx || 0;
            puck.vy = state.puck.vy || 0;
        }

        var en = myNum === 1 ? '2' : '1';
        if (!p2pActive && state.paddles && state.paddles[en]) {
            enemyPad.x = state.paddles[en].x;
            enemyPad.y = state.paddles[en].y;
        }

        if (state.score) {
            if (state.score[0] !== score[0] || state.score[1] !== score[1]) {
                goalFlash = 25;
                trail = [];
                clearBuf(state.puck ? state.puck.x : FW / 2, state.puck ? state.puck.y : FH / 2);
            }
            score = [state.score[0], state.score[1]];
        }

        if (state.state === 'countdown') {
            document.getElementById('countdown-overlay').style.display = 'flex';
            document.getElementById('countdown-num').textContent = state.countdown || '...';
            snapAll(state);
        } else {
            document.getElementById('countdown-overlay').style.display = 'none';
        }

        updateHUD(state);
    });

    sock.on('game_finished', showResult);

    sock.on('player_disconnected', function () {
        toast('Противник отключился!');
    });

    sock.on('game_error', function (d) {
        var w = document.querySelector('.waiting-card');
        if (w) {
            w.innerHTML =
                '<p style="color:#e63946;font-weight:700">' +
                d.error +
                '</p><a href="/lobby" class="btn btn-primary" style="margin-top:16px">Назад</a>';
        }
    });
}

// ═══ GAME LOOP ═══

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
        var mx = mouseX;
        var my = mouseY;

        if (myNum === 1) mx = clamp(mx, PR, FW / 2 - PR);
        else mx = clamp(mx, FW / 2 + PR, FW - PR);

        my = clamp(my, PR, FH - PR);

        myX = mx;
        myY = my;

        var now = performance.now();
        if (now - lastSend > 16) {
            lastSend = now;

            if (sock && connected) {
                sock.volatile.emit('paddle_move', {
                    x: Math.round(mx * 10) / 10,
                    y: Math.round(my * 10) / 10
                });
            }

            if (p2pActive && typeof P2P !== 'undefined') {
                P2P.send({
                    t: 'pos',
                    x: Math.round(mx * 10) / 10,
                    y: Math.round(my * 10) / 10
                });
            }
        }

        interpPuck();
    }

    var packetAge = Math.min((frameNow - serverPuck.receivedAt) / 1000, 0.18);
    var packetFrames = Math.min((serverPuck.packetGap || 16) / 16.6667, 4);
    var predictFactor = 1.4 + packetFrames * 0.35;

    var puckTargetX = serverPuck.x + serverPuck.vx * packetAge * 60 * predictFactor;
    var puckTargetY = serverPuck.y + serverPuck.vy * packetAge * 60 * predictFactor;

    var puckGap = Math.sqrt(
        Math.pow(puckTargetX - drawPuck.x, 2) +
        Math.pow(puckTargetY - drawPuck.y, 2)
    );

    if (!playing || gs.state === 'countdown') {
        drawPuck.x = lerp(drawPuck.x, puck.x, 0.45);
        drawPuck.y = lerp(drawPuck.y, puck.y, 0.45);
    } else {
        drawPuck.x = lerp(drawPuck.x, puckTargetX, puckGap > 90 ? 0.35 : 0.22);
        drawPuck.y = lerp(drawPuck.y, puckTargetY, puckGap > 90 ? 0.35 : 0.22);

        if (packetAge > 0.1) {
            drawPuck.x += serverPuck.vx * dt * 60 * 0.28;
            drawPuck.y += serverPuck.vy * dt * 60 * 0.28;
        }
    }

    drawEnemy.x = lerp(drawEnemy.x, enemyPad.x, p2pActive ? 0.7 : 0.4);
    drawEnemy.y = lerp(drawEnemy.y, enemyPad.y, p2pActive ? 0.7 : 0.4);

    trail.push({ x: drawPuck.x, y: drawPuck.y });
    if (trail.length > TRAIL_MAX) trail.shift();

    if (goalFlash > 0) goalFlash--;
}

// ═══ RENDER ═══

function render() {
    if (!gs) return;

    var W = canvas.width;
    var H = canvas.height;
    var sx = W / FW;
    var sy = H / FH;
    var s = Math.min(sx, sy);

    ctx.clearRect(0, 0, W, H);

    // Фон
    ctx.fillStyle = '#e8f4f8';
    ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = 'rgba(162,210,255,0.35)';
    var sw = W / 8;
    for (var i = 0; i < 8; i += 2) {
        ctx.fillRect(i * sw, 0, sw, H);
    }

    if (bgImg) {
        ctx.globalAlpha = 0.3;
        ctx.drawImage(bgImg, 0, 0, W, H);
        ctx.globalAlpha = 1;
    }

    // Рамка
    var bw = 8 * s;
    var cr = 30 * s;

    ctx.strokeStyle = '#3a7bd5';
    ctx.lineWidth = bw;
    rRect(ctx, bw / 2, bw / 2, W - bw, H - bw, cr);
    ctx.stroke();

    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2;
    rRect(ctx, bw / 2, bw / 2, W - bw, H - bw, cr);
    ctx.stroke();

    // Центр
    ctx.strokeStyle = '#4895ef';
    ctx.lineWidth = 3 * s;

    ctx.beginPath();
    ctx.moveTo(W / 2, bw);
    ctx.lineTo(W / 2, H - bw);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(W / 2, H / 2, 50 * s, 0, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(W / 2, H / 2, 5 * s, 0, Math.PI * 2);
    ctx.fillStyle = '#4895ef';
    ctx.fill();

    // Ворота
    var gy1 = GY1 * sy;
    var gy2 = GY2 * sy;
    var gw = 14 * sx;

    drawGoalNet(0, gy1, gw, gy2 - gy1, '#e63946', s);
    drawGoalNet(W - gw, gy1, gw, gy2 - gy1, '#4895ef', s);

    // Точки вбрасывания
    var fo = [
        [FW * 0.2, FH * 0.3],
        [FW * 0.2, FH * 0.7],
        [FW * 0.8, FH * 0.3],
        [FW * 0.8, FH * 0.7]
    ];

    for (var fi = 0; fi < fo.length; fi++) {
        var fx = fo[fi][0] * sx;
        var fy = fo[fi][1] * sy;
        var fr = 18 * s;

        ctx.beginPath();
        ctx.arc(fx, fy, fr, 0, Math.PI * 2);
        ctx.strokeStyle = '#e63946';
        ctx.lineWidth = 2 * s;
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(fx - fr * 0.5, fy - fr * 0.5);
        ctx.lineTo(fx + fr * 0.5, fy + fr * 0.5);
        ctx.moveTo(fx + fr * 0.5, fy - fr * 0.5);
        ctx.lineTo(fx - fr * 0.5, fy + fr * 0.5);
        ctx.stroke();
    }

    // Вспышка гола
    if (goalFlash > 0) {
        ctx.fillStyle = 'rgba(255,255,255,' + (goalFlash / 40) + ')';
        ctx.fillRect(0, 0, W, H);
    }

    // Шлейф
    for (var ti = 1; ti < trail.length; ti++) {
        var ta = (ti / trail.length) * 0.2;
        var tr2 = (ti / trail.length) * PKR * s * 0.5;

        ctx.beginPath();
        ctx.arc(trail[ti].x * sx, trail[ti].y * sy, tr2, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(26,26,46,' + ta + ')';
        ctx.fill();
    }

    // Шайба
    var px = drawPuck.x * sx;
    var py = drawPuck.y * sy;
    var pr = PKR * s;

    ctx.beginPath();
    ctx.arc(px + 2, py + 3, pr, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.2)';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(px, py, pr, 0, Math.PI * 2);
    ctx.fillStyle = '#1a1a2e';
    ctx.fill();
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(px - pr * 0.25, py - pr * 0.25, pr * 0.3, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.fill();

    // Клюшки
    var myN = String(myNum);
    var enN = myNum === 1 ? '2' : '1';

    drawPad(drawEnemy.x * sx, drawEnemy.y * sy, PR * s, enN, false, s);
    drawPad(myX * sx, myY * sy, PR * s, myN, true, s);

    // Индикатор
    ctx.fillStyle = p2pActive ? '#52b788' : '#e6b800';
    ctx.beginPath();
    ctx.arc(W - 20, 20, 6, 0, Math.PI * 2);
    ctx.fill();

    ctx.font = 'bold 10px Nunito,sans-serif';
    ctx.textAlign = 'right';
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
    ctx.fillStyle = color + '40';
    ctx.fillRect(x, y, w, h);

    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;

    var step = 8 * s;

    for (var gy = y; gy < y + h; gy += step) {
        ctx.beginPath();
        ctx.moveTo(x, gy);
        ctx.lineTo(x + w, gy);
        ctx.stroke();
    }

    for (var gx = x; gx < x + w; gx += step) {
        ctx.beginPath();
        ctx.moveTo(gx, y);
        ctx.lineTo(gx, y + h);
        ctx.stroke();
    }

    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, w, h);
}

function drawPad(x, y, r, numStr, isMe, s) {
    var sk = 'kompot';
    if (gs.players && gs.players[numStr]) {
        sk = gs.players[numStr].skin || 'kompot';
    }

    var skin = SK[sk] || SK.kompot;
    var tc = numStr === '1' ? '#e63946' : '#4895ef';

    ctx.beginPath();
    ctx.arc(x + 2, y + 4, r + 2, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.2)';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(x, y, r + 4 * s, 0, Math.PI * 2);
    ctx.fillStyle = tc;
    ctx.fill();
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 3;
    ctx.stroke();

    var img = imgs[sk + '_paddle'];
    if (img) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(img, x - r, y - r, r * 2, r * 2);
        ctx.restore();
    } else {
        var g = ctx.createRadialGradient(x - r * 0.2, y - r * 0.2, 0, x, y, r);
        g.addColorStop(0, skin.p);
        g.addColorStop(1, skin.s);

        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = g;
        ctx.fill();

        ctx.font = Math.round(r * 1.1) + 'px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(skin.f, x, y);
    }

    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    if (gs.players && gs.players[numStr]) {
        var name = gs.players[numStr].username;
        var fs = Math.max(10, Math.round(12 * s));

        ctx.font = 'bold ' + fs + 'px Nunito,sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        var tw = ctx.measureText(name).width;
        var lh = Math.round(16 * s);
        var ly = y + r + Math.round(6 * s);

        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.fillRect(x - tw / 2 - 4, ly, tw + 8, lh);

        ctx.strokeStyle = '#1a1a2e';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x - tw / 2 - 4, ly, tw + 8, lh);

        ctx.fillStyle = '#1a1a2e';
        ctx.fillText(name, x, ly + lh / 2);
    }

    if (isMe) {
        var mfs = Math.max(9, Math.round(10 * s));
        ctx.font = 'bold ' + mfs + 'px Nunito,sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        var meY = y - r - Math.round(12 * s);

        ctx.strokeStyle = '#1a1a2e';
        ctx.lineWidth = 2;
        ctx.strokeText('▼ YOU', x, meY);

        ctx.fillStyle = '#52b788';
        ctx.fillText('▼ YOU', x, meY);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    initGame();
    requestAnimationFrame(gameLoop);
});
