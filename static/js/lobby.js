// ═══════════════════════════════════════════════════════════
// ТРИ КОТА — АЭРОХОККЕЙ | Лобби и поиск матча
// ═══════════════════════════════════════════════════════════

let isSearching = false;
let searchStartTime = null;
let searchTimerInterval = null;

function findMatch() {
    if (isSearching) return;
    isSearching = true;

    document.getElementById('btn-find-match').style.display = 'none';
    document.getElementById('search-status').style.display = 'flex';

    searchStartTime = Date.now();
    searchTimerInterval = setInterval(updateSearchTimer, 1000);

    window.socket.emit('find_match');
}

function cancelSearch() {
    isSearching = false;

    document.getElementById('btn-find-match').style.display = '';
    document.getElementById('search-status').style.display = 'none';

    clearInterval(searchTimerInterval);

    window.socket.emit('cancel_search');
}

function updateSearchTimer() {
    if (!searchStartTime) return;
    const elapsed = Math.floor((Date.now() - searchStartTime) / 1000);
    document.getElementById('search-timer').textContent = formatTime(elapsed);
}

// ─── Socket Events ───────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!window.socket) return;

    window.socket.on('queue_status', (data) => {
        if (data.status === 'cancelled') {
            isSearching = false;
        }
    });

    window.socket.on('match_found', (data) => {
        clearInterval(searchTimerInterval);
        isSearching = false;

        // Показываем оверлей "Матч найден"
        const overlay = document.getElementById('match-found');
        overlay.style.display = 'flex';

        document.getElementById('opponent-name').textContent = data.opponent;
        document.getElementById('opponent-elo').textContent = data.opponent_elo + ' ELO';

        // Переходим в игру через 2 сек
        setTimeout(() => {
            window.location.href = '/game/' + data.room_id;
        }, 2000);
    });
});