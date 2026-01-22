# scheduler.py
import asyncio
import json
from datetime import datetime, timedelta
import api_utils as api
import database as db
from config import UPDATE_INTERVAL

# Кеш в пам'яті (цей словник ми будемо імпортувати в handlers.py)
schedules_cache = {} 
# Історія сповіщень (тепер з урахуванням часу попередження)
alert_history = set()

async def smart_broadcast(bot, region, queue, text_blackout, text_light, filter_func):
    """
    Розумна розсилка:
    1. Перевіряє налаштування кожного юзера (filter_func).
    2. Відправляє текст залежно від режиму (blackout/light).
    """
    users = await db.get_users_by_queue(region, queue)
    
    for (uid,) in users:
        try:
            # Отримуємо налаштування юзера
            settings = await db.get_user_settings(uid)
            
            # Перевіряємо, чи підходить цей юзер під умови розсилки
            if not filter_func(settings):
                continue
            
            # Вибираємо правильний текст
            mode = settings.get('display_mode', 'blackout')
            text_to_send = text_light if mode == 'light' else text_blackout
            
            await bot.send_message(uid, text_to_send, parse_mode="Markdown")
        except Exception:
            pass
        
        # Невелика затримка, щоб уникнути блокування за флуд
        await asyncio.sleep(0.05) 

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
    # --- ФІКС: Прапорець першого запуску ---
    first_run = True

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
                             # Генеруємо два варіанти тексту (для різних режимів)
                             txt_b = api.format_message(today_sch, queue, today, False, "blackout")
                             txt_l = api.format_message(today_sch, queue, today, False, "light")
                             
                             # Додано перевірку not first_run
                             if not first_run:
                                header = f"🔄 📅 **Оновлено графік на СЬОГОДНІ! ({today_nice})**\n"
                                # Фільтр: тільки ті, хто хоче знати про зміни (notify_changes == 1)
                                await smart_broadcast(
                                    bot, region, queue, 
                                    header + txt_b.split('\n', 1)[1], 
                                    header + txt_l.split('\n', 1)[1],
                                    lambda s: s['notify_changes'] == 1
                                )

                    # --- 2. ПЕРЕВІРКА ЗАВТРА ---
                    # Новий графік з'явився
                    if (tom_sch is not None) and (cached_tom is None):
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))
                        
                        if not first_run and api.calculate_off_hours(tom_sch) > 0:
                            txt_b = api.format_message(tom_sch, queue, tomorrow, True, "blackout")
                            txt_l = api.format_message(tom_sch, queue, tomorrow, True, "light")
                            
                            await smart_broadcast(
                                bot, region, queue, txt_b, txt_l,
                                lambda s: s['notify_changes'] == 1
                            )
                    
                    # Графік змінився
                    elif (tom_sch is not None) and (cached_tom is not None) and (json.dumps(tom_sch, sort_keys=True) != json.dumps(cached_tom, sort_keys=True)):
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))
                        
                        if not first_run:
                            txt_b = api.format_message(tom_sch, queue, tomorrow, True, "blackout")
                            txt_l = api.format_message(tom_sch, queue, tomorrow, True, "light")
                            
                            if txt_b:
                                header = f"🔄 🔮 **Оновлено графік на ЗАВТРА! ({tomorrow_nice})**\n"
                                await smart_broadcast(
                                    bot, region, queue, 
                                    header + txt_b.split('\n', 1)[1], 
                                    header + txt_l.split('\n', 1)[1],
                                    lambda s: s['notify_changes'] == 1
                                )

                    schedules_cache[(region, queue)] = {"today": today_sch, "tomorrow": tom_sch}

                # Додатково зберігаємо статистику
                current_date = datetime.now()
                for i in range(7):
                    d = (current_date - timedelta(days=i)).strftime('%Y-%m-%d')
                    if 'r_data' in locals() and r_data: 
                        sch = r_data['schedule'].get(queue, {}).get(d)
                        if sch: await db.save_stats(region, queue, d, api.calculate_off_hours(sch))
                
                if first_run: first_run = False

        except Exception as e:
            print(f"Update Error: {e}")
        
        await asyncio.sleep(UPDATE_INTERVAL)

async def check_alerts(bot):
    """Щохвилинна перевірка для сповіщень."""
    while True:
        try:
            now = datetime.now()
            curr_time = now.strftime("%H:%M")
            
            if curr_time == "00:00": 
                alert_history.clear()

            # Часові точки для перевірки (5, 15, 30, 60 хвилин наперед)
            check_moments = {
                5: (now + timedelta(minutes=5)).strftime("%H:%M"),
                15: (now + timedelta(minutes=15)).strftime("%H:%M"),
                30: (now + timedelta(minutes=30)).strftime("%H:%M"),
                60: (now + timedelta(minutes=60)).strftime("%H:%M"),
            }

            # ВАЖЛИВО: Використовуємо list(), щоб створити копію і не поламати цикл при оновленні кешу
            for (key, data) in list(schedules_cache.items()):
                today_sch = data.get("today")
                tom_sch = data.get("tomorrow")
                
                if not today_sch: continue
                
                today_intervals = api.parse_intervals(today_sch)
                tom_intervals = api.parse_intervals(tom_sch) if tom_sch else []

                # --- 1. СПОВІЩЕННЯ ПРО ВІДКЛЮЧЕННЯ (PRE-ALERT) ---
                for start, end in today_intervals:
                    if start == "00:00": continue

                    # Перевіряємо всі таймінги (5, 15, 30, 60)
                    for mins, check_time in check_moments.items():
                        if check_time == start:
                            alert_id = f"{key}_{start}_pre_{mins}" # Унікальний ID для кожного таймінгу
                            
                            if alert_id not in alert_history:
                                # Визначаємо кінець відключення
                                actual_end = end
                                if end == "24:00" and tom_intervals and tom_intervals[0][0] == "00:00":
                                    actual_end = tom_intervals[0][1]
                                    if actual_end == "24:00":
                                        actual_end = "завтра до кінця дня"
                                    else:
                                        actual_end = f"завтра до {actual_end}"
                                elif end == "24:00":
                                    actual_end = "кінця дня"
                                
                                msg = f"⏳ **Скоро відключення (через {mins} хв).**\nСвітла не буде до **{actual_end}**."
                                
                                # Фільтр: вкл сповіщення про відключення + збігається час таймера
                                await smart_broadcast(
                                    bot, key[0], key[1], msg, msg,
                                    lambda s, m=mins: s['notify_outage'] == 1 and s['notify_before'] == m
                                )
                                alert_history.add(alert_id)
                
                # --- Стик днів (23:XX -> 00:00) ---
                if tom_intervals and tom_intervals[0][0] == "00:00":
                    start_tom, end_tom = tom_intervals[0]
                    
                    for mins, check_time in check_moments.items():
                        if check_time == "00:00":
                             alert_id = f"{key}_00:00_tom_pre_{mins}"
                             if alert_id not in alert_history:
                                 end_display = "кінця дня" if end_tom == "24:00" else end_tom
                                 msg = f"⏳ **Скоро відключення (через {mins} хв, о 00:00).**\nСвітла не буде до **{end_display}**."
                                 
                                 await smart_broadcast(
                                     bot, key[0], key[1], msg, msg,
                                     lambda s, m=mins: s['notify_outage'] == 1 and s['notify_before'] == m
                                 )
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
                            
                            # Фільтр: тільки ті, хто хоче знати про включення
                            await smart_broadcast(
                                bot, key[0], key[1], msg, msg,
                                lambda s: s['notify_return'] == 1
                            )
                            alert_history.add(alert_id)

        except Exception as e:
             print(f"Alert Error: {e}")
        
        await asyncio.sleep(60 - datetime.now().second)