async function buyItem(itemId) {
    if (!confirm('Купить этот предмет?')) return;

    try {
        const response = await fetch('/api/buy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId })
        });
        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
            document.getElementById('shop-coins').textContent = data.coins;
            document.getElementById('shop-gems').textContent = data.gems;
            setTimeout(() => location.reload(), 500);
        } else {
            showToast(data.error, 'error');
        }
    } catch (err) {
        showToast('Ошибка сети', 'error');
    }
}

async function equipItem(itemId) {
    try {
        const response = await fetch('/api/equip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId })
        });
        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
            setTimeout(() => location.reload(), 300);
        } else {
            showToast(data.error, 'error');
        }
    } catch (err) {
        showToast('Ошибка сети', 'error');
    }
}
