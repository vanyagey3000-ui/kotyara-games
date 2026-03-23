## Railway MySQL

The app now supports Railway MySQL directly.

### What to add in the game service

Recommended:

`MYSQL_URL=${{ MySQL.MYSQL_URL }}`

You can also keep using a generic name:

`DATABASE_URL=${{ MySQL.MYSQL_URL }}`

The app checks `DATABASE_URL`, then `MYSQL_URL`, then `MYSQL_PUBLIC_URL`.

### Notes

- Private network is preferred to avoid extra egress costs.
- Set `SECRET_KEY` as a separate variable in the game service.
- If no database URL is set, the project still falls back to `sqlite:///hockey.db`.
- Railway MySQL URLs like `mysql://...` are normalized automatically to `mysql+pymysql://...`.
- The app also appends `charset=utf8mb4` automatically for MySQL connections.

### Important about old data

Switching to MySQL does not automatically move data from the old local SQLite database.
If you want existing users, inventory, match history, chat, and shop state to appear in Railway MySQL,
that requires a separate data migration step from `hockey.db`.
