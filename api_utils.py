# api_utils.py
import aiohttp
from datetime import datetime
from config import API_URL

async def fetch_api_data():
    """Робить запит до сайту і повертає JSON."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
    except Exception as e:
        print(f"Помилка API: {e}")
    return None

def calculate_off_hours(schedule_json):
    """Рахує суму годин без світла (множимо на 0.5, бо дані по 30 хв)."""
    if not schedule_json: 
        return 0
    # Рахуємо клітинки, де статус = 2 (відключення)
    count = sum(1 for k, v in schedule_json.items() if k != "24:00" and v == 2)
    return count * 0.5

def calculate_on_hours(schedule_json):
    """Рахує суму годин ЗІ світлом (для нового режиму)."""
    if not schedule_json: return 0
    # Рахуємо все, що НЕ є відключенням (не 2)
    count = sum(1 for k, v in schedule_json.items() if k != "24:00" and v != 2)
    return count * 0.5

def parse_intervals(schedule_json, target_status=2, inverse=False):
    """
    Універсальний парсер інтервалів.
    target_status=2, inverse=False -> Шукає відключення (стандарт)
    target_status=2, inverse=True  -> Шукає наявність світла (новий режим)
    """
    if not schedule_json: 
        return []
    
    times = sorted([k for k in schedule_json.keys() if k != "24:00"])
    intervals = []
    current_start = None
    in_interval = False 

    for t in times:
        val = schedule_json.get(t)
        
        # Логіка визначення "активного" стану
        if inverse:
            # Шукаємо "СВІТЛО Є" (все, що не 2)
            is_active = (val != target_status)
        else:
            # Шукаємо "СВІТЛА НЕМАЄ" (тільки 2)
            is_active = (val == target_status)

        # Початок інтервалу
        if is_active and not in_interval:
            current_start = t
            in_interval = True
        # Кінець інтервалу
        elif not is_active and in_interval:
            if current_start:
                intervals.append((current_start, t))
            in_interval = False
            current_start = None
            
    # Якщо інтервал триває до кінця доби
    if in_interval and current_start:
        intervals.append((current_start, "24:00"))
        
    return intervals

def format_message(schedule_json, queue_name, date_str, is_tomorrow=False, display_mode="blackout"):
    """Створює текст повідомлення з урахуванням налаштувань користувача."""
    
    # Визначаємо день тижня
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    days = {"Monday": "Понеділок", "Tuesday": "Вівторок", "Wednesday": "Середа",
            "Thursday": "Четвер", "Friday": "П'ятниця", "Saturday": "Субота", "Sunday": "Неділя"}
    day_name = days.get(dt.strftime("%A"), dt.strftime("%A"))
    date_nice = dt.strftime('%d.%m')

    # Якщо даних немає взагалі
    if schedule_json is None:
        if is_tomorrow:
            return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**"
        else:
            return "⏳ **Дані оновлюються...**"

    # --- НАЛАШТУВАННЯ ВІДОБРАЖЕННЯ ---
    if display_mode == "light":
        # РЕЖИМ: СВІТЛО Є
        intervals = parse_intervals(schedule_json, target_status=2, inverse=True)
        emoji_main = "🟢"
        emoji_header = "💡"
        # total_hours = calculate_on_hours(schedule_json)
        # total_label = "✨ Всього зі світлом"
        empty_text = "😔 **Світла не передбачено.** (Повний блекаут)"
        header_text = f"Графік наявності світла"
        
        # Для підрахунку годин світла
        total_hours = calculate_on_hours(schedule_json)
        total_label = "✨ Всього зі світлом"

    else:
        # РЕЖИМ: ВІДКЛЮЧЕННЯ (BLACKOUT) - Стандартний
        intervals = parse_intervals(schedule_json, target_status=2, inverse=False)
        emoji_main = "🕒" 
        emoji_header = "💡"
        
        empty_text = "✅ **Відключень не передбачено.** (Світло є)"
        header_text = f"Графік відключень світла"
        
        # Для підрахунку годин темряви
        total_hours = calculate_off_hours(schedule_json)
        total_label = "⚡️ Всього без світла"

    # ЗАГОЛОВОК
    when = "на завтра" if is_tomorrow else "на сьогодні"
    header = f"{emoji_header} **{header_text} {when}, {date_nice} ({day_name})**"

    # Якщо це завтра і список порожній у режимі blackout -> графіку ще немає (або немає відключень)
    if is_tomorrow and not intervals and display_mode == "blackout":
         return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**\n(Або відключень не планується)"

    # ТІЛО ПОВІДОМЛЕННЯ
    if not intervals:
        body = empty_text
    else:
        lines = []
        for start, end in intervals:
            # Тривалість
            t1 = datetime.strptime(start, "%H:%M")
            if end == "24:00":
                diff = 24 - t1.hour - (t1.minute / 60)
            else:
                t2 = datetime.strptime(end, "%H:%M")
                diff = (t2 - t1).seconds / 3600
            
            diff_str = f"{int(diff)}" if diff.is_integer() else f"{diff:.1f}"
            lines.append(f"{emoji_main} **{start} — {end}** _({diff_str} год)_")
        body = "\n".join(lines)

    total_str = f"{int(total_hours)}" if total_hours.is_integer() else f"{total_hours:.1f}"

    text = (
        f"{header}\n"
        f"👤 Черга: **{queue_name}**\n"
        f"──────────────────\n"
        f"{body}\n"
        f"──────────────────\n"
    )
    
    # Додаємо підсумок годин, якщо він більше 0 або якщо це режим світла (щоб показати 0 годин світла при блекауті)
    if total_hours > 0 or display_mode == "light":
         text += f"{total_label}: **{total_str} год.**"
    else:
         # Це для режиму blackout, коли світло є весь день
         text += f"⚡️ Світло має бути весь день."

    return text