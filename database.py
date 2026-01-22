# database.py
import aiosqlite
from datetime import datetime, timedelta
from config import DB_NAME

async def init_db():
    """Створює таблиці та безпечно оновлює структуру."""
    async with aiosqlite.connect(DB_NAME) as db:
        # === 1. ОСНОВНІ ДАНІ (НЕ ЧІПАЄМО) ===
        # Таблиця для користувачів
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                region TEXT,
                queue TEXT,
                mode TEXT DEFAULT 'normal'
            )
        """)

        # === МІГРАЦІЯ: ДОДАВАННЯ НАЛАШТУВАНЬ (Personalization 2.0) ===
        # Додаємо нові колонки до існуючої таблиці users.
        
        try:
            # Час попередження (за замовчуванням 5 хв)
            await db.execute("ALTER TABLE users ADD COLUMN notify_before INTEGER DEFAULT 5")
        except: pass
        
        try:
            # Сповіщення про відключення (1 = вкл, 0 = викл)
            await db.execute("ALTER TABLE users ADD COLUMN notify_outage INTEGER DEFAULT 1")
        except: pass

        try:
            # Сповіщення про включення
            await db.execute("ALTER TABLE users ADD COLUMN notify_return INTEGER DEFAULT 1")
        except: pass

        try:
            # Сповіщення про зміни графіку
            await db.execute("ALTER TABLE users ADD COLUMN notify_changes INTEGER DEFAULT 1")
        except: pass

        try:
            # Режим відображення ('blackout' - відключення, 'light' - світло)
            await db.execute("ALTER TABLE users ADD COLUMN display_mode TEXT DEFAULT 'blackout'")
        except: pass


        # Таблиця для статистики
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT,
                region TEXT,
                queue TEXT,
                off_hours REAL,
                PRIMARY KEY (date, region, queue)
            )
        """)
        
        # === 2. ПЕРЕВІРКА І ВИПРАВЛЕННЯ ТІЛЬКИ ТАБЛИЦЬ ПІДТРИМКИ ===
        # Перевіряємо, чи існує таблиця support_messages і чи є в ній колонка ticket_id
        try:
            async with db.execute("PRAGMA table_info(support_messages)") as cur:
                columns = [row[1] for row in await cur.fetchall()]
                # Якщо таблиця є, але в ній немає потрібної колонки 'ticket_id'
                if columns and 'ticket_id' not in columns:
                    print("⚠️ Оновлення структури таблиць підтримки... (Основні дані збережено)")
                    # Видаляємо тільки старі таблиці підтримки, бо вони не сумісні з новим кодом
                    await db.execute("DROP TABLE IF EXISTS support_messages")
                    await db.execute("DROP TABLE IF EXISTS support_tickets")
                    await db.commit()
        except Exception as e:
            print(f"Non-critical DB check error: {e}")

        # === 3. СТВОРЕННЯ ТАБЛИЦЬ ПІДТРИМКИ (ЯКЩО ЇХ НЕМАЄ) ===
        
        # Таблиця тікетів
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                status TEXT DEFAULT 'unread',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_message_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Таблиця повідомлень
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                from_user TEXT NOT NULL,
                message_text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES support_tickets(ticket_id)
            )
        """)
        
        await db.commit()

async def save_user(user_id, region, queue):
    """Зберігає або оновлює вибір користувача."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (user_id, region, queue) 
            VALUES (?, ?, ?) 
            ON CONFLICT(user_id) DO UPDATE SET region=excluded.region, queue=excluded.queue
        """, (user_id, region, queue))
        await db.commit()

async def get_user(user_id):
    """Повертає регіон і чергу користувача."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT region, queue FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

# --- НОВІ ФУНКЦІЇ ДЛЯ НАЛАШТУВАНЬ ---

async def get_user_settings(user_id):
    """Отримує всі налаштування користувача."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Якщо користувача немає або поля пусті, повертаємо дефолтні налаштування
        defaults = {
            "notify_before": 5, # СТАНДАРТ: 5 ХВИЛИН
            "notify_outage": 1, 
            "notify_return": 1, 
            "notify_changes": 1, 
            "display_mode": "blackout"
        }
        
        # Пробуємо отримати нові поля. Якщо база стара і міграція не пройшла (малоймовірно),
        # запит може впасти, тому можна обгорнути в try, але init_db має гарантувати наявність полів.
        try:
            async with db.execute("""
                SELECT notify_before, notify_outage, notify_return, notify_changes, display_mode 
                FROM users WHERE user_id = ?
            """, (user_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    # Якщо значення NULL (наприклад, старий юзер), беремо дефолт
                    return {
                        "notify_before": row[0] if row[0] is not None else 5, # СТАНДАРТ: 5 ХВИЛИН
                        "notify_outage": row[1] if row[1] is not None else 1,
                        "notify_return": row[2] if row[2] is not None else 1,
                        "notify_changes": row[3] if row[3] is not None else 1,
                        "display_mode": row[4] if row[4] is not None else "blackout"
                    }
        except Exception as e:
            print(f"Error getting settings: {e}")
            
        return defaults

async def update_user_setting(user_id, key, value):
    """Оновлює конкретне налаштування."""
    allowed_keys = ["notify_before", "notify_outage", "notify_return", "notify_changes", "display_mode"]
    if key not in allowed_keys:
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()

# --- КІНЕЦЬ НОВИХ ФУНКЦІЙ ---

async def save_stats(region, queue, date_str, off_hours):
    """Записує статистику за день."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO daily_stats (date, region, queue, off_hours) 
            VALUES (?, ?, ?, ?) 
            ON CONFLICT(date, region, queue) DO UPDATE SET off_hours=excluded.off_hours
        """, (date_str, region, queue, off_hours))
        await db.commit()

async def get_stats_data(region, queue):
    """Отримує статистику за останні 7 днів (від сьогодні і назад)."""
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT date, off_hours 
            FROM daily_stats 
            WHERE region = ? AND queue = ? 
            ORDER BY date DESC 
            LIMIT 7
        """
        async with db.execute(sql, (region, queue)) as cur:
            rows = await cur.fetchall()
            return list(reversed(rows))

async def get_all_subs():
    """Отримує список всіх унікальних підписок (регіон + черга)."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT DISTINCT region, queue FROM users") as cur:
            return await cur.fetchall()

async def get_users_by_queue(region, queue):
    """Отримує ID всіх користувачів конкретної черги."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users WHERE region = ? AND queue = ?", (region, queue)) as cur:
            return await cur.fetchall()

async def delete_user(user_id):
    """Видаляє користувача з бази даних (відписка)."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()

async def set_user_mode(user_id, mode):
    """Встановлює режим користувача (normal, support, admin)."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET mode = ? WHERE user_id = ?",
            (mode, user_id)
        )
        await db.commit()

async def get_user_mode(user_id):
    """Отримує режим користувача."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT mode FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "normal"

async def get_users_count():
    """Отримує кількість всіх користувачів."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def get_all_users_for_broadcast():
    """Отримує всіх користувачів для розсилки."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT DISTINCT user_id FROM users") as cur:
            return await cur.fetchall()

async def cleanup_old_stats():
    """Видаляє статистику старше 7 днів."""
    async with aiosqlite.connect(DB_NAME) as db:
        cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        await db.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff_date,))
        await db.commit()

async def get_off_hours_for_date(region, queue, date_str):
    """Отримує години відключення для конкретної дати."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT off_hours FROM daily_stats WHERE region = ? AND queue = ? AND date = ?", (region, queue, date_str)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# ========== НОВА СИСТЕМА ПІДТРИМКИ ==========

async def create_or_get_ticket(user_id, username):
    """Створює новий тікет або повертає існуючий відкритий тікет."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Перевіряємо чи є відкритий тікет
        async with db.execute(
            "SELECT ticket_id FROM support_tickets WHERE user_id = ? AND status != 'closed' ORDER BY last_message_at DESC LIMIT 1",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0]
        
        # Створюємо новий тікет
        async with db.execute(
            "INSERT INTO support_tickets (user_id, username, status) VALUES (?, ?, 'unread')",
            (user_id, username)
        ) as cur:
            await db.commit()
            return cur.lastrowid

async def save_support_message(ticket_id, from_user, message_text):
    """Зберігає повідомлення в тікет."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO support_messages (ticket_id, from_user, message_text) VALUES (?, ?, ?)",
            (ticket_id, from_user, message_text)
        )
        # Оновлюємо час останнього повідомлення
        await db.execute(
            "UPDATE support_tickets SET last_message_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
            (ticket_id,)
        )
        await db.commit()

async def get_unread_tickets():
    """Отримує всі непрочитані тікети."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT t.ticket_id, t.user_id, t.username, t.created_at, t.last_message_at,
                   (SELECT COUNT(*) FROM support_messages WHERE ticket_id = t.ticket_id) as msg_count
            FROM support_tickets t
            WHERE t.status = 'unread'
            ORDER BY t.last_message_at DESC
        """) as cur:
            return await cur.fetchall()

async def get_all_tickets():
    """Отримує всі тікети."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT t.ticket_id, t.user_id, t.username, t.status, t.created_at, t.last_message_at,
                   (SELECT COUNT(*) FROM support_messages WHERE ticket_id = t.ticket_id) as msg_count
            FROM support_tickets t
            ORDER BY t.last_message_at DESC
            LIMIT 20
        """) as cur:
            return await cur.fetchall()

async def get_ticket_messages(ticket_id):
    """Отримує всі повідомлення тікету."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT from_user, message_text, created_at
            FROM support_messages
            WHERE ticket_id = ?
            ORDER BY created_at ASC
        """, (ticket_id,)) as cur:
            return await cur.fetchall()

async def mark_ticket_read(ticket_id):
    """Позначає тікет як прочитаний."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE support_tickets SET status = 'read' WHERE ticket_id = ?",
            (ticket_id,)
        )
        await db.commit()

async def close_ticket(ticket_id):
    """Закриває тікет."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE support_tickets SET status = 'closed' WHERE ticket_id = ?",
            (ticket_id,)
        )
        await db.commit()

async def reopen_ticket(ticket_id):
    """Знову відкриває тікет."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE support_tickets SET status = 'unread', last_message_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
            (ticket_id,)
        )
        await db.commit()

async def get_ticket_info(ticket_id):
    """Отримує інформацію про тікет."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, username, status FROM support_tickets WHERE ticket_id = ?",
            (ticket_id,)
        ) as cur:
            return await cur.fetchone()

async def get_unread_count():
    """Отримує кількість непрочитаних тікетів."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE status = 'unread'"
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0