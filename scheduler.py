# scheduler.py
import asyncio
import json
from datetime import datetime, timedelta
import api_utils as api
import database as db
from config import UPDATE_INTERVAL

# Кеш в пам'яті
schedules_cache = {} 
# Історія сповіщень
alert_history = set()

# --- НОВЕ: Флаг, чи був відправлений графік сьогодні ---
schedule_sent_today = False

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
    global schedule_sent_today  # Використовуємо глобальну змінну
    first_run = True

    while True:
        try:
            # Очищаємо старі дані статистики
            await db.cleanup_old_stats()

            data = await api.fetch_api_data()
            if data:
                today = datetime.now().strftime('%Y-%m-%d')
                tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                
                today_nice = datetime.now().strftime('%d.%m')
                tomorrow_nice = (datetime.now() + timedelta(days=1)).strftime('%d.%m')

                subs = await db.get_all_subs()

                for region, queue in subs:
                    r_data = next((r for r in data['regions'] if r['name_ua'] == region), None)
                    if not r_data: continue
                    
                    today_sch = r_data['schedule'].get(queue, {}).get(today, None)
                    tom_sch = r_data['schedule'].get(queue, {}).get(tomorrow, None)

                    cached = schedules_cache.get((region, queue), {})
                    cached_date = cached.get("date")
                    
                    if cached_date != today:
                        cached_today = None
                        cached_tom = None
                    else:
                        cached_today = cached.get("today")
                        cached_tom = cached.get("tomorrow")

                    # --- 1. ПЕРЕВІРКА СЬОГОДНІ ---
                    if today_sch:
                        await db.save_stats(region, queue, today, api.calculate_off_hours(today_sch))
                        
                        current_norm = api.parse_intervals(today_sch)
                        cached_norm = api.parse_intervals(cached_today) if cached_today else None

                        if cached_norm is not None and json.dumps(current_norm, sort_keys=True) != json.dumps(cached_norm, sort_keys=True):
                             txt_b = api.format_message(today_sch, queue, today, False, "blackout")
                             txt_l = api.format_message(today_sch, queue, today, False, "light")
                             
                             if not first_run:
                                header = f"🔄 📅 **Оновлено графік на СЬОГОДНІ! ({today_nice})**\n"
                                await smart_broadcast(
                                    bot, region, queue, 
                                    header + txt_b.split('\n', 1)[1], 
                                    header + txt_l.split('\n', 1)[1],
                                    lambda s: s['notify_changes'] == 1
                                )
                                # Якщо ми відправили оновлення, то ранкове повідомлення вже не потрібне
                                schedule_sent_today = True

                    # --- 2. ПЕРЕВІРКА ЗАВТРА ---
                    if (tom_sch is not None) and (cached_tom is None):
                        await db.save_stats(region, queue, tomorrow, api.calculate_off_hours(tom_sch))
                        
                        if not first_run and api.calculate_off_hours(tom_sch) > 0:
                            txt_b = api.format_message(tom_sch, queue, tomorrow, True, "blackout")
                            txt_l = api.format_message(tom_sch, queue, tomorrow, True, "light")
                            
                            await smart_broadcast(
                                bot, region, queue, txt_b, txt_l,
                                lambda s: s['notify_changes'] == 1
                            )
                    
                    elif (tom_sch is not None) and (cached_tom is not None):
                        tom_norm = api.parse_intervals(tom_sch)
                        cached_tom_norm = api.parse_intervals(cached_tom)

                        if json.dumps(tom_norm, sort_keys=True) != json.dumps(cached_tom_norm, sort_keys=True):
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

                    schedules_cache[(region, queue)] = {"date": today, "today": today_sch, "tomorrow": tom_sch}

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
    global schedule_sent_today  # Використовуємо глобальну змінну

    while True:
        try:
            now = datetime.now()
            curr_time = now.strftime("%H:%M")
            today_str = now.strftime('%Y-%m-%d')
            
            # --- СБРОС У 00:00 ---
            if curr_time == "00:00": 
                alert_history.clear()
                schedule_sent_today = False # Скидаємо прапорець на новий день

            # --- НОВЕ: РАНКОВЕ ОПОВІЩЕННЯ (06:00) ---
            if curr_time == "06:00" and not schedule_sent_today:
                print("☀️ Відправляю ранкове зведення...")
                # Проходимо по всіх відомих чергах в кеші
                for (region, queue), data in schedules_cache.items():
                    # Переконуємось, що дані свіжі
                    if data.get("date") != today_str: continue

                    today_sch = data.get("today")
                    if not today_sch: continue

                    # Формуємо повідомлення
                    txt_b = api.format_message(today_sch, queue, today_str, False, "blackout")
                    txt_l = api.format_message(today_sch, queue, today_str, False, "light")

                    # Заголовок
                    header = f"☀️ **Добрий ранок! Графік на сьогодні:**\n"

                    # Відправляємо тим, у кого увімкнені ранкові сповіщення (використовуємо notify_changes як прапорець підписки на важливе)
                    # Або можна вважати це базовим функціоналом для всіх.
                    # Тут налаштовано для всіх, хто не вимкнув notify_changes (або можна створити нову колонку в БД)
                    await smart_broadcast(
                        bot, region, queue,
                        header + txt_b.split('\n', 1)[1], # Прибираємо старий заголовок, ставимо новий
                        header + txt_l.split('\n', 1)[1],
                        lambda s: s['notify_changes'] == 1 # Або True для всіх
                    )
                
                schedule_sent_today = True # Запам'ятовуємо, що вже відправили

            # Часові точки для перевірки
            check_moments = {
                5: (now + timedelta(minutes=5)).strftime("%H:%M"),
                15: (now + timedelta(minutes=15)).strftime("%H:%M"),
                30: (now + timedelta(minutes=30)).strftime("%H:%M"),
                60: (now + timedelta(minutes=60)).strftime("%H:%M"),
            }

            for (key, data) in list(schedules_cache.items()):
                today_sch = data.get("today")
                tom_sch = data.get("tomorrow")
                
                if not today_sch: continue
                
                today_intervals = api.parse_intervals(today_sch)
                tom_intervals = api.parse_intervals(tom_sch) if tom_sch else []

                for start, end in today_intervals:
                    # 1. СПОВІЩЕННЯ ПРО ВІДКЛЮЧЕННЯ
                    if start != "00:00":
                        for mins, check_time in check_moments.items():
                            if check_time == start:
                                alert_id = f"{key}_{start}_out_pre_{mins}"
                                if alert_id not in alert_history:
                                    actual_end = end
                                    if end == "24:00" and tom_intervals and tom_intervals[0][0] == "00:00":
                                        actual_end = tom_intervals[0][1]
                                        actual_end = "завтра до кінця дня" if actual_end == "24:00" else f"завтра до {actual_end}"
                                    elif end == "24:00":
                                        actual_end = "кінця дня"
                                    
                                    msg = f"⏳ **Скоро відключення (через {mins} хв).**\nСвітла не буде до **{actual_end}**."
                                    
                                    await smart_broadcast(
                                        bot, key[0], key[1], msg, msg,
                                        lambda s, m=mins: s['notify_outage'] == 1 and s['notify_before'] == m
                                    )
                                    alert_history.add(alert_id)

                    # 2. СПОВІЩЕННЯ ПРО ВКЛЮЧЕННЯ
                    if end != "24:00":
                        for mins, check_time in check_moments.items():
                            if check_time == end:
                                alert_id = f"{key}_{end}_ret_pre_{mins}"
                                if alert_id not in alert_history:
                                    msg = f"💡 **Світло з'явиться орієнтовно через {mins} хв (о {end}).**"
                                    
                                    await smart_broadcast(
                                        bot, key[0], key[1], msg, msg,
                                        lambda s, m=mins: s['notify_return'] == 1 and s['notify_return_before'] == m
                                    )
                                    alert_history.add(alert_id)

                # Стик днів
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

                # 3. СПОВІЩЕННЯ В МОМЕНТ ВКЛЮЧЕННЯ
                for start, end in today_intervals:
                    if curr_time == end and end != "24:00":
                        alert_id = f"{key}_{end}_on"
                        if alert_id not in alert_history:
                            next_outage = find_next_outage(end, today_intervals, tom_intervals)
                            next_info = f"Наступне відключення: **{next_outage}**." if next_outage else "✅ Далі без відключень."
                            
                            msg = (f"⚡️ **Світло повертається!**\n"
                                   f"Включення за графіком ({end}).\n"
                                   f"{next_info}")
                            
                            await smart_broadcast(
                                bot, key[0], key[1], msg, msg,
                                lambda s: s['notify_return'] == 1
                            )
                            alert_history.add(alert_id)

        except Exception as e:
             print(f"Alert Error: {e}")
        
        await asyncio.sleep(60 - datetime.now().second)