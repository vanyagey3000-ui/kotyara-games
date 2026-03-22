var canvas = document.getElementById('game-canvas');
var ctx = canvas.getContext('2d');

var myNum = 0;
var gs = null;
var connected = false;
var sock = null;

var FW = 800, FH = 500, PR = 30, PKR = 15;
var GW = 140, GY1 = (FH - GW) / 2, GY2 = (FH + GW) / 2;

var myX = 80, myY = FH / 2;
var mouseX = 400, mouseY = 250;
var lastSend = 0;

var puck = {x: FW/2, y: FH/2, vx: 0, vy: 0};
var enemyPad = {x: FW-80, y: FH/2};

// Серверные данные — только сохраняем, НЕ применяем сразу
var srv = {x: FW/2, y: FH/2, vx: 0, vy: 0, fresh: false};

var score = [0, 0];
var goalFlash = 0;
var trail = [];
var TRAIL_MAX = 16;
var p2pActive = false;
var isHost = false;

// Физика — ТОЧНО как на сервере
var FRICTION = 0.995;
var BOUNCE = 0.85;
var SPEED_LIMIT = 18;
var SUB_STEPS = 3; // сервер тоже делает 3!

var imgs = {};
var bgImg = null;

function ldImg(k, u) {
    if (!u) return;
    var i = new Image(); i.crossOrigin = 'anonymous';
    i.onload = function() { imgs[k] = i; }; i.src = u;
}
if (typeof SKIN_IMAGES !== 'undefined' && SKIN_IMAGES)
    for (var id in SKIN_IMAGES) ldImg(id + '_paddle', SKIN_IMAGES[id]);
if (typeof BG_IMAGE_URL !== 'undefined' && BG_IMAGE_URL) {
    var bi = new Image(); bi.crossOrigin = 'anonymous';
    bi.onload = function() { bgImg = bi; }; bi.src = BG_IMAGE_URL;
}

var SK = {
    kompot:{p:'#ff8844',s:'#cc5500',f:'\uD83D\uDC31'},
    karamelka:{p:'#ff69b4',s:'#cc3388',f:'\uD83C\uDF80'},
    korzhik:{p:'#88bb44',s:'#558822',f:'\u26BD'},
    papa:{p:'#4466aa',s:'#223366',f:'\uD83D\uDC54'},
    mama:{p:'#dd66aa',s:'#993366',f:'\uD83D\uDC90'},
    babushka:{p:'#996688',s:'#664455',f:'\uD83C\uDF6A'},
    dedushka:{p:'#888866',s:'#555544',f:'\uD83C\uDFA3'},
    nuke_kompot:{p:'#ff2200',s:'#ff8800',f:'\uD83D\uDD25'},
    cyber_karamelka:{p:'#00ffcc',s:'#0088aa',f:'\uD83E\uDD16'}
};

function initCanvas() {
    var c = document.getElementById('game-container');
    var h = document.querySelector('.game-hud');
    canvas.width = c.clientWidth;
    canvas.height = c.clientHeight - (h ? h.offsetHeight : 50);
}
window.addEventListener('resize', initCanvas);
window.addEventListener('orientationchange', function() { setTimeout(initCanvas, 200); });
initCanvas();

function fmt(s) { var m=Math.floor(s/60),sec=Math.floor(s%60); return m+':'+(sec<10?'0':'')+sec; }
function clamp(v,a,b) { return v<a?a:v>b?b:v; }
function lerp(a,b,t) { return a+(b-a)*t; }

// ═══════════════════════════════════════════
// ФИЗИКА ШАЙБЫ — ИДЕНТИЧНА СЕРВЕРУ
// 3 sub-steps, те же константы, те же формулы
// ═══════════════════════════════════════════

function simPuck() {
    for (var step = 0; step < SUB_STEPS; step++) {
        puck.x += puck.vx;
        puck.y += puck.vy;
        puck.vx *= FRICTION;
        puck.vy *= FRICTION;

        var spd = Math.sqrt(puck.vx*puck.vx + puck.vy*puck.vy);
        if (spd > SPEED_LIMIT) {
            puck.vx *= SPEED_LIMIT/spd;
            puck.vy *= SPEED_LIMIT/spd;
        }

        // Стены верх/низ
        if (puck.y - PKR <= 0) { puck.y = PKR; puck.vy = Math.abs(puck.vy)*BOUNCE; }
        if (puck.y + PKR >= FH) { puck.y = FH-PKR; puck.vy = -Math.abs(puck.vy)*BOUNCE; }

        // Стены лево/право (вне ворот)
        if (puck.x - PKR <= 0) {
            if (!(puck.y > GY1 && puck.y < GY2)) {
                puck.x = PKR;
                puck.vx = Math.abs(puck.vx)*BOUNCE;
            }
        }
        if (puck.x + PKR >= FW) {
            if (!(puck.y > GY1 && puck.y < GY2)) {
                puck.x = FW-PKR;
                puck.vx = -Math.abs(puck.vx)*BOUNCE;
            }
        }

        // Столкновения с клюшками
        hitPad(myX, myY);
        hitPad(enemyPad.x, enemyPad.y);
    }
}

function hitPad(px, py) {
    var dx = puck.x-px, dy = puck.y-py;
    var d = Math.sqrt(dx*dx + dy*dy);
    var minD = PKR + PR;
    if (d < minD && d > 0.1) {
        var nx = dx/d, ny = dy/d;
        puck.x += nx*(minD-d);
        puck.y += ny*(minD-d);
        var dot = puck.vx*nx + puck.vy*ny;
        if (dot < 0) {
            puck.vx -= 2.2*dot*nx;
            puck.vy -= 2.2*dot*ny;
        }
    }
}

// ═══ SOCKET ═══

function initGame() {
    sock = io({transports:['websocket','polling'], reconnection:true});

    sock.on('connect', function() { sock.emit('join_game', {room_id:ROOM_ID}); });
    sock.on('disconnect', function() { connected = false; });
    sock.on('reconnect', function() { sock.emit('join_game', {room_id:ROOM_ID}); });

    sock.on('game_joined', function(d) {
        myNum = d.player_number;
        gs = d.state;
        connected = true;
        isHost = (myNum === 1);
        document.getElementById('waiting-overlay').style.display = 'none';
        snapToState(d.state);

        if (typeof P2P !== 'undefined') {
            P2P.onConnect = function() { p2pActive = true; toast('P2P подключено!'); };
            P2P.onDisconnect = function() { p2pActive = false; };
            P2P.onMessage = function(msg) {
                if (msg.t === 'pos') { enemyPad.x = msg.x; enemyPad.y = msg.y; }
            };
            P2P.init(sock, ROOM_ID, isHost);
        }
    });

    // ═══ СЕРВЕР ПРИСЛАЛ ДАННЫЕ ═══
    // НЕ двигаем шайбу здесь! Только сохраняем.
    sock.on('game_state', function(state) {
        gs = state;

        // Сохраняем серверную шайбу (коррекция будет в update)
        if (state.puck) {
            srv.x = state.puck.x;
            srv.y = state.puck.y;
            srv.vx = state.puck.vx || 0;
            srv.vy = state.puck.vy || 0;
        }

        // Клюшка противника (если нет P2P)
        var en = myNum === 1 ? '2' : '1';
        if (!p2pActive && state.paddles && state.paddles[en]) {
            enemyPad.x = state.paddles[en].x;
            enemyPad.y = state.paddles[en].y;
        }

        // Счёт
        if (state.score) {
            if (state.score[0] !== score[0] || state.score[1] !== score[1]) {
                goalFlash = 25;
                trail = [];
                // Гол — мгновенный сброс к серверу
                puck.x = srv.x; puck.y = srv.y;
                puck.vx = srv.vx; puck.vy = srv.vy;
            }
            score = [state.score[0], state.score[1]];
        }

        // Countdown
        if (state.state === 'countdown') {
            document.getElementById('countdown-overlay').style.display = 'flex';
            document.getElementById('countdown-num').textContent = state.countdown || '...';
            snapToState(state);
        } else {
            document.getElementById('countdown-overlay').style.display = 'none';
        }

        updateHUD(state);
    });

    sock.on('game_finished', showResult);
    sock.on('player_disconnected', function() { toast('Противник отключился!'); });
    sock.on('game_error', function(d) {
        var w = document.querySelector('.waiting-card');
        if (w) w.innerHTML = '<p style="color:#e63946;font-weight:700">' + d.error +
            '</p><a href="/lobby" class="btn btn-primary" style="margin-top:16px">Назад</a>';
    });
}

// Полная синхронизация (countdown, первый вход)
function snapToState(state) {
    if (!state) return;
    var my = String(myNum), en = myNum === 1 ? '2' : '1';
    if (state.paddles && state.paddles[my]) {
        myX = state.paddles[my].x; myY = state.paddles[my].y;
        mouseX = myX; mouseY = myY;
    }
    if (state.paddles && state.paddles[en]) {
        enemyPad.x = state.paddles[en].x;
        enemyPad.y = state.paddles[en].y;
    }
    if (state.puck) {
        puck.x = srv.x = state.puck.x;
        puck.y = srv.y = state.puck.y;
        puck.vx = srv.vx = state.puck.vx || 0;
        puck.vy = srv.vy = state.puck.vy || 0;
    }
    if (state.score) score = [state.score[0], state.score[1]];
    trail = [];
}

// ═══ INPUT ═══

function getGamePos(cx, cy) {
    var r = canvas.getBoundingClientRect();
    return {x:(cx-r.left)*(FW/canvas.width), y:(cy-r.top)*(FH/canvas.height)};
}
canvas.addEventListener('mousemove', function(e) { var p=getGamePos(e.clientX,e.clientY); mouseX=p.x; mouseY=p.y; });
canvas.addEventListener('touchmove', function(e) { e.preventDefault(); var t=e.touches[0]; var p=getGamePos(t.clientX,t.clientY); mouseX=p.x; mouseY=p.y; }, {passive:false});
canvas.addEventListener('touchstart', function(e) { e.preventDefault(); var t=e.touches[0]; var p=getGamePos(t.clientX,t.clientY); mouseX=p.x; mouseY=p.y; }, {passive:false});
document.addEventListener('touchmove', function(e) { if(document.getElementById('game-canvas')) e.preventDefault(); }, {passive:false});

function toast(msg) {
    var old = document.getElementById('game-toast'); if (old) old.remove();
    var t = document.createElement('div'); t.id = 'game-toast';
    t.style.cssText = 'position:fixed;top:80px;left:50%;transform:translateX(-50%);background:rgba(255,255,255,.95);color:#1a1a2e;padding:12px 24px;border-radius:12px;font-weight:700;z-index:9999;font-family:Nunito,sans-serif;border:3px solid #1a1a2e;box-shadow:0 4px 0 rgba(0,0,0,0.2)';
    t.textContent = msg; document.body.appendChild(t);
    setTimeout(function() { if(t.parentNode) t.remove(); }, 3000);
}

function updateHUD(s) {
    if (!s) return;
    document.getElementById('score-p1').textContent = score[0];
    document.getElementById('score-p2').textContent = score[1];
    if (s.players) {
        if (s.players['1']) { document.getElementById('hud-p1-name').textContent = s.players['1'].username; document.getElementById('hud-p1-elo').textContent = s.players['1'].elo + ' ELO'; }
        if (s.players['2']) { document.getElementById('hud-p2-name').textContent = s.players['2'].username; document.getElementById('hud-p2-elo').textContent = s.players['2'].elo + ' ELO'; }
    }
    if (s.elapsed) document.getElementById('game-timer').textContent = fmt(s.elapsed);
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

// ═══════════════════════════════════════════
// GAME LOOP — 60fps, плавная шайба
// ═══════════════════════════════════════════

function gameLoop() {
    update();
    render();
    requestAnimationFrame(gameLoop);
}

function update() {
    if (!gs) return;
    var playing = (gs.state === 'playing');

    if (playing) {
        // ═══ МОЯ КЛЮШКА ═══
        var mx = mouseX, my = mouseY;
        if (myNum === 1) mx = clamp(mx, PR, FW/2-PR);
        else mx = clamp(mx, FW/2+PR, FW-PR);
        my = clamp(my, PR, FH-PR);
        myX = mx; myY = my;

        var now = performance.now();
        if (now - lastSend > 16) {
            lastSend = now;
            if (sock && connected) sock.volatile.emit('paddle_move', {x:Math.round(mx*10)/10, y:Math.round(my*10)/10});
            if (p2pActive && typeof P2P !== 'undefined') P2P.send({t:'pos', x:Math.round(mx*10)/10, y:Math.round(my*10)/10});
        }

        // ═══ ФИЗИКА ШАЙБЫ: 3 sub-steps (как сервер) ═══
        simPuck();

        // ═══ ПЛАВНАЯ КОРРЕКЦИЯ К СЕРВЕРУ ═══
        // 2% позиции + 3% скорости за кадр
        // За 1 секунду (60 кадров): ~70% ошибки исчезает
        // Визуально незаметно!
        var dx = srv.x - puck.x;
        var dy = srv.y - puck.y;
        var err = Math.sqrt(dx*dx + dy*dy);

        if (err > 100) {
            // Телепорт — что-то пошло совсем не так
            puck.x = srv.x;
            puck.y = srv.y;
            puck.vx = srv.vx;
            puck.vy = srv.vy;
        } else {
            // Микро-коррекция каждый кадр
            puck.x += dx * 0.02;
            puck.y += dy * 0.02;
            puck.vx += (srv.vx - puck.vx) * 0.03;
            puck.vy += (srv.vy - puck.vy) * 0.03;
        }
    }

    trail.push({x: puck.x, y: puck.y});
    if (trail.length > TRAIL_MAX) trail.shift();
    if (goalFlash > 0) goalFlash--;
}

// ═══ RENDER ═══

function render() {
    if (!gs) return;
    var W=canvas.width, H=canvas.height, sx=W/FW, sy=H/FH, s=Math.min(sx,sy);
    ctx.clearRect(0,0,W,H);

    ctx.fillStyle='#e8f4f8'; ctx.fillRect(0,0,W,H);
    ctx.fillStyle='rgba(162,210,255,0.35)';
    var sw=W/8; for(var i=0;i<8;i+=2) ctx.fillRect(i*sw,0,sw,H);
    if(bgImg){ctx.globalAlpha=0.3;ctx.drawImage(bgImg,0,0,W,H);ctx.globalAlpha=1;}

    var bw=8*s,cr=30*s;
    ctx.strokeStyle='#3a7bd5';ctx.lineWidth=bw;
    rRect(ctx,bw/2,bw/2,W-bw,H-bw,cr);ctx.stroke();
    ctx.strokeStyle='#1a1a2e';ctx.lineWidth=2;
    rRect(ctx,bw/2,bw/2,W-bw,H-bw,cr);ctx.stroke();

    ctx.strokeStyle='#4895ef';ctx.lineWidth=3*s;
    ctx.beginPath();ctx.moveTo(W/2,bw);ctx.lineTo(W/2,H-bw);ctx.stroke();
    ctx.beginPath();ctx.arc(W/2,H/2,50*s,0,Math.PI*2);ctx.stroke();
    ctx.beginPath();ctx.arc(W/2,H/2,5*s,0,Math.PI*2);ctx.fillStyle='#4895ef';ctx.fill();

    var gy1=GY1*sy,gy2=GY2*sy,gw=14*sx;
    drawGoalNet(0,gy1,gw,gy2-gy1,'#e63946',s);
    drawGoalNet(W-gw,gy1,gw,gy2-gy1,'#4895ef',s);

    var fo=[[FW*.2,FH*.3],[FW*.2,FH*.7],[FW*.8,FH*.3],[FW*.8,FH*.7]];
    for(var fi=0;fi<fo.length;fi++){
        var fx=fo[fi][0]*sx,fy=fo[fi][1]*sy,fr=18*s;
        ctx.beginPath();ctx.arc(fx,fy,fr,0,Math.PI*2);
        ctx.strokeStyle='#e63946';ctx.lineWidth=2*s;ctx.stroke();
        ctx.beginPath();ctx.moveTo(fx-fr*.5,fy-fr*.5);ctx.lineTo(fx+fr*.5,fy+fr*.5);
        ctx.moveTo(fx+fr*.5,fy-fr*.5);ctx.lineTo(fx-fr*.5,fy+fr*.5);ctx.stroke();
    }

    if(goalFlash>0){ctx.fillStyle='rgba(255,255,255,'+(goalFlash/40)+')';ctx.fillRect(0,0,W,H);}

    for(var ti=1;ti<trail.length;ti++){
        var ta=(ti/trail.length)*0.2,tr2=(ti/trail.length)*PKR*s*0.5;
        ctx.beginPath();ctx.arc(trail[ti].x*sx,trail[ti].y*sy,tr2,0,Math.PI*2);
        ctx.fillStyle='rgba(26,26,46,'+ta+')';ctx.fill();
    }

    // Шайба — прямо из puck, никакой доп. интерполяции
    var px=puck.x*sx, py=puck.y*sy, pr=PKR*s;
    ctx.beginPath();ctx.arc(px+2,py+3,pr,0,Math.PI*2);ctx.fillStyle='rgba(0,0,0,0.2)';ctx.fill();
    ctx.beginPath();ctx.arc(px,py,pr,0,Math.PI*2);ctx.fillStyle='#1a1a2e';ctx.fill();
    ctx.strokeStyle='#333';ctx.lineWidth=2;ctx.stroke();
    ctx.beginPath();ctx.arc(px-pr*.25,py-pr*.25,pr*.3,0,Math.PI*2);
    ctx.fillStyle='rgba(255,255,255,0.3)';ctx.fill();

    var myN=String(myNum), enN=myNum===1?'2':'1';
    drawPad(enemyPad.x*sx, enemyPad.y*sy, PR*s, enN, false, s);
    drawPad(myX*sx, myY*sy, PR*s, myN, true, s);

    ctx.fillStyle=p2pActive?'#52b788':'#e6b800';
    ctx.beginPath();ctx.arc(W-20,20,6,0,Math.PI*2);ctx.fill();
    ctx.font='bold 10px Nunito,sans-serif';ctx.textAlign='right';
    ctx.fillText(p2pActive?'P2P':'WS',W-30,24);
}

function rRect(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.lineTo(x+w-r,y);c.arcTo(x+w,y,x+w,y+r,r);c.lineTo(x+w,y+h-r);c.arcTo(x+w,y+h,x+w-r,y+h,r);c.lineTo(x+r,y+h);c.arcTo(x,y+h,x,y+h-r,r);c.lineTo(x,y+r);c.arcTo(x,y,x+r,y,r);c.closePath();}

function drawGoalNet(x,y,w,h,color,s){
    ctx.fillStyle=color+'40';ctx.fillRect(x,y,w,h);
    ctx.strokeStyle=color;ctx.lineWidth=1.5;
    var step=8*s;
    for(var gy=y;gy<y+h;gy+=step){ctx.beginPath();ctx.moveTo(x,gy);ctx.lineTo(x+w,gy);ctx.stroke();}
    for(var gx=x;gx<x+w;gx+=step){ctx.beginPath();ctx.moveTo(gx,y);ctx.lineTo(gx,y+h);ctx.stroke();}
    ctx.strokeStyle='#1a1a2e';ctx.lineWidth=3;ctx.strokeRect(x,y,w,h);
}

function drawPad(x,y,r,numStr,isMe,s){
    var sk='kompot';
    if(gs.players&&gs.players[numStr])sk=gs.players[numStr].skin||'kompot';
    var skin=SK[sk]||SK.kompot;
    var tc=numStr==='1'?'#e63946':'#4895ef';
    ctx.beginPath();ctx.arc(x+2,y+4,r+2,0,Math.PI*2);ctx.fillStyle='rgba(0,0,0,0.2)';ctx.fill();
    ctx.beginPath();ctx.arc(x,y,r+4*s,0,Math.PI*2);ctx.fillStyle=tc;ctx.fill();
    ctx.strokeStyle='#1a1a2e';ctx.lineWidth=3;ctx.stroke();
    var img=imgs[sk+'_paddle'];
    if(img){ctx.save();ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.clip();ctx.drawImage(img,x-r,y-r,r*2,r*2);ctx.restore();}
    else{var g=ctx.createRadialGradient(x-r*.2,y-r*.2,0,x,y,r);g.addColorStop(0,skin.p);g.addColorStop(1,skin.s);ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=g;ctx.fill();ctx.font=Math.round(r*1.1)+'px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(skin.f,x,y);}
    ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.strokeStyle='#1a1a2e';ctx.lineWidth=2.5;ctx.stroke();
    if(gs.players&&gs.players[numStr]){var name=gs.players[numStr].username;var fs=Math.max(10,Math.round(12*s));ctx.font='bold '+fs+'px Nunito,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';var tw=ctx.measureText(name).width;var lh=Math.round(16*s),ly=y+r+Math.round(6*s);ctx.fillStyle='rgba(255,255,255,0.85)';ctx.fillRect(x-tw/2-4,ly,tw+8,lh);ctx.strokeStyle='#1a1a2e';ctx.lineWidth=1.5;ctx.strokeRect(x-tw/2-4,ly,tw+8,lh);ctx.fillStyle='#1a1a2e';ctx.fillText(name,x,ly+lh/2);}
    if(isMe){var mfs=Math.max(9,Math.round(10*s));ctx.font='bold '+mfs+'px Nunito,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';var meY=y-r-Math.round(12*s);ctx.strokeStyle='#1a1a2e';ctx.lineWidth=2;ctx.strokeText('\u25BC YOU',x,meY);ctx.fillStyle='#52b788';ctx.fillText('\u25BC YOU',x,meY);}
}

document.addEventListener('DOMContentLoaded', function() {
    initGame();
    requestAnimationFrame(gameLoop);
});
