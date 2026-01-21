# database.py
import aiosqlite
from datetime import datetime, timedelta
from config import DB_NAME

async def init_db():
    """Створює таблиці, якщо їх ще немає."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблиця для користувачів (хто на що підписаний)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                region TEXT,
                queue TEXT,
                mode TEXT DEFAULT 'normal'
            )
        """)
        # Таблиця для статистики (дата, черга, кількість годин без світла)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT,
                region TEXT,
                queue TEXT,
                off_hours REAL,
                PRIMARY KEY (date, region, queue)
            )
        """)
        
        # === НОВА СИСТЕМА ПІДТРИМКИ ===
        
        # Таблиця тікетів підтримки
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
        
        # Таблиця повідомлень в тікетах
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
        # Беремо останні 7 днів від сьогодні, сортуємо від старого до нового
        sql = """
            SELECT date, off_hours 
            FROM daily_stats 
            WHERE region = ? AND queue = ? 
            ORDER BY date DESC 
            LIMIT 7
        """
        async with db.execute(sql, (region, queue)) as cur:
            rows = await cur.fetchall()
            # Розвертаємо, щоб мати від старого до нового
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
        # Видаляємо записи, де дата старше ніж 7 днів від сьогодні
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
