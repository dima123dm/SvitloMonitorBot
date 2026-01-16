# scheduler.py
import asyncio
import json
from datetime import datetime, timedelta
import api_utils as api
import database as db
from config import UPDATE_INTERVAL

# Кеш в пам'яті (цей словник ми будемо імпортувати в handlers.py)
schedules_cache = {} 
# Історія сповіщень
alert_history = set()

async def broadcast(bot, region, queue, text):
    """Розсилає повідомлення всім користувачам черги."""
    users = await db.get_users_by_queue(region, queue)
    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
        except:
            pass

def find_next_outage(current_time_str, today_intervals, tomorrow_intervals):
    """Шукає час наступного відключення."""
    for start, end in today_intervals:
        if start > current_time_str:
            return f"сьогодні о {start}"
    
    if tomorrow_intervals:
        start, end = tomorrow_intervals[0]
        return f"завтра о {start}"
    
    return None

async def check_updates(bot):
    """Перевіряє оновлення графіків на сайті."""
    while True:
        try:
            # Очищаємо старі дані статистики
            await db.cleanup_old_stats()

            data = await api.fetch_api_data()
            if data:
                today = datetime.now().strftime('%Y-%m-%d')
                tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                
                # Форматуємо дати для красивого повідомлення (16.01)
                today_nice = datetime.now().strftime('%d.%m')
                tomorrow_nice = (datetime.now() + timedelta(days=1)).strftime('%d.%m')

                subs = await db.get_all_subs()

                for region, queue in subs:
                    r_data = next((r for r in data['regions'] if r['name_ua'] == region), None)
                    if not r_data: continue
                    
                    today_sch = r_data['schedule'].get(queue, {}).get(today, None)
                    tom_sch = r_data['schedule'].get(queue, {}).get(tomorrow, None)

                    cached = schedules_cache.get((region, queue), {})
                    cached_today = cached.get("today")
                    cached_tom = cached.get("tomorrow")

                    # --- 1. ПЕРЕВІРКА СЬОГОДНІ ---
                    if today_sch:
                        await db.save_stats(region, queue, today, api.calculate_off_hours(today_sch))
                        
                        if cached_today and json.dumps(today_sch, sort_keys=True) != json.dumps(cached_today, sort_keys=True):
                             text = api.format_message(today_sch, queue, today, False)
                             if text:
                                # Додано дату в заголовок
                                await broadcast(bot, region, queue, f"🔄 📅 **Оновлено графік на СЬОГОДНІ ({today_nice})!**\n" + text.split('\n', 1)[1])

                    # --- 2. ПЕРЕВІРКА ЗАВТРА ---
                    if (tom_sch is not None) and (cached_tom is None):
                        if api.calculate_off_hours(tom_sch) > 0:
                            text = api.format_message(tom_sch, queue, tomorrow, True)
                            await broadcast(bot, region, queue, text)
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))
                    
                    elif (tom_sch is not None) and (cached_tom is not None) and (json.dumps(tom_sch, sort_keys=True) != json.dumps(cached_tom, sort_keys=True)):
                        if api.calculate_off_hours(tom_sch) > 0:
                            text = api.format_message(tom_sch, queue, tomorrow, True)
                            if text:
                                # Додано дату в заголовок
                                await broadcast(bot, region, queue, f"🔄 🔮 **Оновлено графік на ЗАВТРА ({tomorrow_nice})!**\n" + text.split('\n', 1)[1])
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))

                    schedules_cache[(region, queue)] = {"today": today_sch, "tomorrow": tom_sch}

                # Додатково зберігаємо статистику за останні 7 днів, якщо дані є в API
                current_date = datetime.now()
                for i in range(7):
                    d = (current_date - timedelta(days=i)).strftime('%Y-%m-%d')
                    sch = r_data['schedule'].get(queue, {}).get(d)
                    if sch:
                        await db.save_stats(region, queue, d, api.calculate_off_hours(sch))

        except Exception as e:
            print(f"Update Error: {e}")
        
        await asyncio.sleep(UPDATE_INTERVAL)

async def check_alerts(bot):
    """Щохвилинна перевірка для сповіщень."""
    while True:
        try:
            now = datetime.now()
            curr_time = now.strftime("%H:%M")
            # pre_time - це час через 5 хвилин
            pre_time = (now + timedelta(minutes=5)).strftime("%H:%M")
            
            if curr_time == "00:00": 
                alert_history.clear()

            for (key, data) in schedules_cache.items():
                today_sch = data.get("today")
                tom_sch = data.get("tomorrow")
                
                if not today_sch: continue
                
                today_intervals = api.parse_intervals(today_sch)
                tom_intervals = api.parse_intervals(tom_sch) if tom_sch else []

                # --- 1. СПОВІЩЕННЯ ПРО ВІДКЛЮЧЕННЯ (PRE-ALERT) ---
                for start, end in today_intervals:
                    # ВАЖЛИВО: Ігноруємо "00:00" з поточного дня, бо це вже минуле (початок дня).
                    # Сповіщення про 00:00 обробляється окремим блоком нижче.
                    if start == "00:00":
                        continue

                    if pre_time == start:
                        alert_id = f"{key}_{start}_pre"
                        if alert_id not in alert_history:
                            # Перевіряємо, чи продовжується відключення завтра
                            actual_end = end
                            if end == "24:00" and tom_intervals and tom_intervals[0][0] == "00:00":
                                actual_end = tom_intervals[0][1]
                                if actual_end == "24:00":
                                    actual_end = "завтра до кінця дня"
                                else:
                                    actual_end = f"завтра до {actual_end}"
                            elif end == "24:00":
                                actual_end = "кінця дня"
                            
                            # Формуємо повідомлення
                            msg = f"⏳ **Скоро відключення (в {start}).**\nСвітла не буде до **{actual_end}**."
                            await broadcast(bot, key[0], key[1], msg)
                            alert_history.add(alert_id)
                
                # --- Стик днів (23:55 -> 00:00) ---
                # Якщо зараз 23:55, то pre_time стає "00:00".
                # Саме в цей момент перевіряємо початок завтрашнього графіку.
                if pre_time == "00:00" and tom_intervals:
                    start_tom, end_tom = tom_intervals[0]
                    if start_tom == "00:00":
                        alert_id = f"{key}_00:00_tom_pre"
                        if alert_id not in alert_history:
                             if end_tom == "24:00":
                                 end_display = "кінця дня"
                             else:
                                 end_display = end_tom

                             msg = f"⏳ **Скоро відключення (в 00:00).**\nСвітла не буде до **{end_display}**."
                             await broadcast(bot, key[0], key[1], msg)
                             alert_history.add(alert_id)

                # --- 2. СПОВІЩЕННЯ ПРО ВКЛЮЧЕННЯ (ON-ALERT) ---
                for start, end in today_intervals:
                    if curr_time == end and end != "24:00":
                        alert_id = f"{key}_{end}_on"
                        if alert_id not in alert_history:
                            next_outage = find_next_outage(end, today_intervals, tom_intervals)
                            next_info = f"Наступне відключення: **{next_outage}**." if next_outage else "✅ Далі без відключень."
                            
                            msg = (f"⚡️ **Світло повертається!**\n"
                                   f"Включення за графіком ({end}).\n"
                                   f"{next_info}")
                            
                            await broadcast(bot, key[0], key[1], msg)
                            alert_history.add(alert_id)

        except Exception as e:
             print(f"Alert Error: {e}")
        
        await asyncio.sleep(60 - datetime.now().second)