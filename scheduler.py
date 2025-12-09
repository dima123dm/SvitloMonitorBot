# scheduler.py
import asyncio
import json
from datetime import datetime, timedelta
import api_utils as api
import database as db
from config import UPDATE_INTERVAL

# Кеш в пам'яті, щоб порівнювати графіки
schedules_cache = {}
# Щоб не надсилати сповіщення про 5 хвилин двічі
alert_history = set()


async def broadcast(bot, region, queue, text):
    """Розсилає повідомлення всім користувачам черги."""
    users = await db.get_users_by_queue(region, queue)
    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
        except:
            pass  # Користувач заблокував бота


async def check_updates(bot):
    """Перевіряє оновлення графіків на сайті."""
    while True:
        try:
            data = await api.fetch_api_data()
            if data:
                today = datetime.now().strftime('%Y-%m-%d')
                tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                subs = await db.get_all_subs()

                for region, queue in subs:
                    r_data = next((r for r in data['regions'] if r['name_ua'] == region), None)
                    if not r_data: continue

                    # Отримуємо графіки (використовуємо .get(..., None))
                    today_sch = r_data['schedule'].get(queue, {}).get(today, None)
                    tom_sch = r_data['schedule'].get(queue, {}).get(tomorrow, None)

                    # --- ЛОГІКА ДЛЯ СЬОГОДНІ ---
                    if today_sch:
                        # Зберігаємо статистику
                        await db.save_stats(region, queue, today, api.calculate_off_hours(today_sch))

                        cached = schedules_cache.get((region, queue), {})
                        # Якщо графік змінився
                        if cached.get("today") and json.dumps(today_sch, sort_keys=True) != json.dumps(cached["today"],
                                                                                                       sort_keys=True):
                            text = api.format_message(today_sch, queue, today, False)
                            if text:
                                await broadcast(bot, region, queue,
                                                "🔄 **Увага! Графік оновлено**\n" + text.split('\n', 1)[1])

                    # --- ЛОГІКА ДЛЯ ЗАВТРА ---
                    cached_tom = schedules_cache.get((region, queue), {}).get("tomorrow")

                    # 1. Графік З'ЯВИВСЯ (був None -> став Дані)
                    if (tom_sch is not None) and (cached_tom is None):
                        # Сповіщаємо тільки якщо є відключення
                        if api.calculate_off_hours(tom_sch) > 0:
                            text = api.format_message(tom_sch, queue, tomorrow, True)
                            await broadcast(bot, region, queue, text)
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))

                    # 2. Графік ЗМІНИВСЯ
                    elif (tom_sch is not None) and (cached_tom is not None) and (
                            json.dumps(tom_sch, sort_keys=True) != json.dumps(cached_tom, sort_keys=True)):
                        if api.calculate_off_hours(tom_sch) > 0:
                            text = api.format_message(tom_sch, queue, tomorrow, True)
                            await broadcast(bot, region, queue,
                                            "🔄 **Увага! Графік на завтра змінено**\n" + text.split('\n', 1)[1])
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))

                    # Оновлюємо кеш
                    schedules_cache[(region, queue)] = {"today": today_sch, "tomorrow": tom_sch}
        except Exception as e:
            print(f"Помилка в чекері: {e}")

        await asyncio.sleep(UPDATE_INTERVAL)


async def check_alerts(bot):
    """Щохвилинна перевірка для сповіщень."""
    while True:
        try:
            now = datetime.now()
            curr_time = now.strftime("%H:%M")
            pre_time = (now + timedelta(minutes=5)).strftime("%H:%M")

            # Очищення історії опівночі
            if curr_time == "00:00":
                alert_history.clear()

            for (key, data) in schedules_cache.items():
                if not data.get("today"): continue

                intervals = api.parse_intervals(data["today"])
                for start, end in intervals:
                    # Сповіщення про початок
                    if pre_time == start:
                        alert_id = f"{key}_{start}_pre"
                        if alert_id not in alert_history:
                            await broadcast(bot, key[0], key[1],
                                            f"⏳ **Увага! Скоро відключення.**\nСвітло зникне о {start}.")
                            alert_history.add(alert_id)

                    # Сповіщення про кінець
                    if curr_time == end and end != "24:00":
                        alert_id = f"{key}_{end}_on"
                        if alert_id not in alert_history:
                            await broadcast(bot, key[0], key[1],
                                            f"⚡️ **Світло повертається!**\nВідключення завершено (за графіком {end}).")
                            alert_history.add(alert_id)
        except Exception as e:
            print(f"Помилка алертів: {e}")

        # Чекаємо до початку наступної хвилини
        await asyncio.sleep(60 - datetime.now().second)