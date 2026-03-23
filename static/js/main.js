const SOUND_ENABLED_KEY = 'kotayra_sound_enabled';
const SOUND_PROMPT_SEEN_KEY = 'kotayra_sound_prompt_seen';
const SITE_ANNOUNCEMENT_HIDE_MS = 10000;

let siteAnnouncementHideTimer = null;

function storageGet(key) {
    try {
        return window.localStorage.getItem(key);
    } catch (e) {
        return null;
    }
}

function storageSet(key, value) {
    try {
        window.localStorage.setItem(key, value);
    } catch (e) {
        return;
    }
}

function isSoundEnabled() {
    return storageGet(SOUND_ENABLED_KEY) === '1';
}

async function primeSoundPermission() {
    try {
        const audio = new Audio('/static/sounds/zvuk_shaiba.mp3');
        audio.preload = 'auto';
        audio.volume = 0;
        audio.muted = true;
        await audio.play();
        audio.pause();
        audio.currentTime = 0;
        return true;
    } catch (e) {
        return false;
    }
}

async function setSoundEnabled(enabled, shouldPrime) {
    storageSet(SOUND_ENABLED_KEY, enabled ? '1' : '0');
    if (enabled) {
        storageSet(SOUND_PROMPT_SEEN_KEY, '1');
        if (shouldPrime) {
            await primeSoundPermission();
        }
    }
    window.dispatchEvent(new CustomEvent('kotayra-sound-change', {
        detail: { enabled: enabled }
    }));
}

function removeSoundPrompt() {
    const overlay = document.getElementById('sound-permission-overlay');
    if (overlay) overlay.remove();
}

function showSoundPromptOnce() {
    if (storageGet(SOUND_PROMPT_SEEN_KEY) === '1') return;
    if (document.getElementById('sound-permission-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'sound-permission-overlay';
    overlay.className = 'sound-permission-overlay';
    overlay.innerHTML = `
        <div class="sound-permission-card">
            <span class="section-kicker">ЗВУК В ИГРЕ</span>
            <h2 class="section-title">Уважаемый пользователь, Пожалуйста прочтите!</h2>
            <p class="profile-member">
                Пожалуйста включите звук нашей игре, Если вы это не сделайте
                из-за этого игра может работать так как не очень сильно хотелось.
            </p>
            <p class="profile-member">
                При всем уважении звук вы можете включить у нас в настройках там будет гайд!
            </p>
            <button type="button" class="btn btn-primary btn-full" id="sound-permission-accept" disabled>
                Принять (5с на прочтение)
            </button>
        </div>
    `;
    document.body.appendChild(overlay);

    const button = document.getElementById('sound-permission-accept');
    let secondsLeft = 5;
    const timer = setInterval(() => {
        secondsLeft -= 1;
        if (secondsLeft > 0) {
            button.textContent = `Принять (${secondsLeft}с на прочтение)`;
            return;
        }
        clearInterval(timer);
        button.disabled = false;
        button.textContent = 'Принять';
    }, 1000);

    button.addEventListener('click', async () => {
        await setSoundEnabled(true, true);
        removeSoundPrompt();
    });
}

function ensureSiteAnnouncementElement() {
    let banner = document.querySelector('.site-announcement');
    if (banner) return banner;

    const navbar = document.querySelector('.navbar');
    if (!navbar) return null;

    banner = document.createElement('div');
    banner.className = 'site-announcement';
    banner.innerHTML = '<div class="nav-content site-announcement-content"><strong></strong><span></span></div>';
    navbar.insertAdjacentElement('afterend', banner);
    return banner;
}

function hideSiteAnnouncement() {
    const banner = document.querySelector('.site-announcement');
    if (banner) {
        banner.style.display = 'none';
    }
}

function showSiteAnnouncement(data) {
    const banner = ensureSiteAnnouncementElement();
    if (!banner) return;

    const durationMs = Math.max(1000, (data.duration_seconds || 10) * 1000);
    const titleEl = banner.querySelector('strong');
    const messageEl = banner.querySelector('span');

    banner.className = `site-announcement site-announcement-${escapeHtml(data.level || 'info')}`;
    banner.style.display = 'block';
    if (titleEl) titleEl.textContent = data.title || 'Важное объявление';
    if (messageEl) messageEl.textContent = data.message || '';

    clearTimeout(siteAnnouncementHideTimer);
    siteAnnouncementHideTimer = setTimeout(hideSiteAnnouncement, durationMs);
}

window.KotayraSound = {
    isEnabled: isSoundEnabled,
    setEnabled: setSoundEnabled,
    prime: primeSoundPermission,
    hasPromptBeenShown: () => storageGet(SOUND_PROMPT_SEEN_KEY) === '1'
};

document.addEventListener('DOMContentLoaded', () => {
    const isGamePage = document.getElementById('game-canvas');

    showSoundPromptOnce();

    const banner = document.querySelector('.site-announcement');
    if (banner) {
        clearTimeout(siteAnnouncementHideTimer);
        siteAnnouncementHideTimer = setTimeout(hideSiteAnnouncement, SITE_ANNOUNCEMENT_HIDE_MS);
    }

    if (!isGamePage && typeof io !== 'undefined') {
        try {
            window.socket = io({
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionAttempts: 10
            });

            window.socket.on('online_count', (data) => {
                const el = document.getElementById('online-count');
                if (el) el.textContent = data.count;
                const heroEl = document.getElementById('hero-online');
                if (heroEl) heroEl.textContent = data.count;
            });

            window.socket.on('admin_announcement', (data) => {
                const levelToType = {
                    info: 'success',
                    warning: 'error',
                    alert: 'error'
                };
                showSiteAnnouncement(data);
                showToast(`${escapeHtml(data.title)}: ${escapeHtml(data.message)}`, levelToType[data.level] || 'success', (data.duration_seconds || 10) * 1000);
            });
        } catch (e) {
            console.log('Socket unavailable');
        }
    }

    setTimeout(() => {
        document.querySelectorAll('.flash').forEach(el => {
            el.style.transition = 'opacity 0.5s ease';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 500);
        });
    }, 5000);
});

function showToast(message, type = 'success', durationMs = 4000) {
    let container = document.querySelector('.flash-messages');
    if (!container) {
        container = document.createElement('div');
        container.className = 'flash-messages';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `flash flash-${type}`;
    toast.innerHTML = `${message} <button class="flash-close" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.transition = 'opacity 0.5s ease';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 500);
    }, durationMs);
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function escapeHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
}
