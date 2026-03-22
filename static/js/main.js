// ═══════════════════════════════════════════════════════════
// ТРИ КОТА — АЭРОХОККЕЙ | Базовый JS
// ═══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    // НЕ создаём сокет на странице игры — там свой
    const isGamePage = document.getElementById('game-canvas');
    if (isGamePage) {
        console.log('🎮 Страница игры — main.js пропускает сокет');
        return;
    }

    if (typeof io !== 'undefined') {
        try {
            window.socket = io({
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionAttempts: 10
            });

            window.socket.on('connect', () => {
                console.log('🔌 Подключено к серверу');
            });

            window.socket.on('disconnect', () => {
                console.log('❌ Отключено от сервера');
            });

            window.socket.on('online_count', (data) => {
                const el = document.getElementById('online-count');
                if (el) el.textContent = data.count;
                const heroEl = document.getElementById('hero-online');
                if (heroEl) heroEl.textContent = data.count;
            });
        } catch (e) {
            console.log('Socket.IO не доступен');
        }
    }

    // Автоскрытие flash
    setTimeout(() => {
        document.querySelectorAll('.flash').forEach(el => {
            el.style.transition = 'opacity 0.5s ease';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 500);
        });
    }, 5000);
});

function showToast(message, type = 'success') {
    let container = document.querySelector('.flash-messages');
    if (!container) {
        container = document.createElement('div');
        container.className = 'flash-messages';
        container.style.cssText = 'position:fixed;top:70px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `flash flash-${type}`;
    toast.innerHTML = `${message} <button class="flash-close" onclick="this.parentElement.remove()">×</button>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.transition = 'opacity 0.5s ease';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}