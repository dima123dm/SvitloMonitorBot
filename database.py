# database.py
import aiosqlite
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
        # Таблиця для повідомлень підтримки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
        # Беремо останні 7 днів від сьогодні
        sql = """
            SELECT date, off_hours 
            FROM daily_stats 
            WHERE region = ? AND queue = ? 
            ORDER BY date ASC 
            LIMIT 7
        """
        async with db.execute(sql, (region, queue)) as cur:
            rows = await cur.fetchall()
            return rows

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

async def save_support_message(user_id, username, text):
    """Зберігає повідомлення від користувача в підтримці."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO support_messages (user_id, username, text) VALUES (?, ?, ?)",
            (user_id, username, text)
        )
        await db.commit()

async def get_all_support_messages():
    """Отримує всі повідомлення підтримки."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, username, text, timestamp FROM support_messages ORDER BY timestamp DESC"
        ) as cur:
            return await cur.fetchall()

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