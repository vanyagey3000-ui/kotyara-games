import sqlite3
import os


def auto_migrate(app):
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('sqlite:///'):
        return

    db_file = uri.replace('sqlite:///', '')
    if not os.path.isabs(db_file):
        db_file = os.path.join(app.instance_path, db_file)

    if not os.path.exists(db_file):
        print("DB not found - will be created fresh")
        return

    print("Checking migrations...")

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}

    migrations = {
        'users': {
            'elo': 'INTEGER DEFAULT 1000',
            'peak_elo': 'INTEGER DEFAULT 1000',
            'wins': 'INTEGER DEFAULT 0',
            'losses': 'INTEGER DEFAULT 0',
            'draws': 'INTEGER DEFAULT 0',
            'goals_scored': 'INTEGER DEFAULT 0',
            'goals_conceded': 'INTEGER DEFAULT 0',
            'total_games': 'INTEGER DEFAULT 0',
            'win_streak': 'INTEGER DEFAULT 0',
            'best_streak': 'INTEGER DEFAULT 0',
            'coins': 'INTEGER DEFAULT 500',
            'gems': 'INTEGER DEFAULT 10',
            'active_skin': "VARCHAR(50) DEFAULT 'kompot'",
            'staff_role': "VARCHAR(20) DEFAULT 'player'",
            'match_ban_until': 'DATETIME',
            'created_at': 'DATETIME',
            'last_login': 'DATETIME',
            'last_game': 'DATETIME',
            'is_online': 'BOOLEAN DEFAULT 0',
            'is_in_game': 'BOOLEAN DEFAULT 0',
        },
        'match_history': {
            'game_mode': "VARCHAR(10) DEFAULT '1v1'",
            'score_p1': 'INTEGER DEFAULT 0',
            'score_p2': 'INTEGER DEFAULT 0',
            'elo_change_p1': 'INTEGER DEFAULT 0',
            'elo_change_p2': 'INTEGER DEFAULT 0',
            'p1_elo_before': 'INTEGER DEFAULT 0',
            'p2_elo_before': 'INTEGER DEFAULT 0',
            'coins_reward_p1': 'INTEGER DEFAULT 0',
            'coins_reward_p2': 'INTEGER DEFAULT 0',
            'duration_seconds': 'INTEGER DEFAULT 0',
        },
    }

    changes = 0
    for table, cols in migrations.items():
        if table not in tables:
            continue
        cur.execute("PRAGMA table_info(" + table + ")")
        existing = {r[1] for r in cur.fetchall()}
        for col, coltype in cols.items():
            if col not in existing:
                try:
                    cur.execute("ALTER TABLE " + table + " ADD COLUMN " + col + " " + coltype)
                    changes += 1
                    print("  Added: " + table + "." + col)
                except Exception as e:
                    print("  Error: " + str(e))

    conn.commit()
    conn.close()
    if changes:
        print("Migration done: " + str(changes) + " changes")
    else:
        print("DB is up to date")
