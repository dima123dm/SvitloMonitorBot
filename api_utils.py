# api_utils.py
import aiohttp
import asyncio
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup
from config import API_URL

# URL офіційного сайту
HOE_SITE_URL = "https://hoe.com.ua/page/pogodinni-vidkljuchennja"

# Словник для конвертації місяців (для парсингу сайту)
UA_MONTHS = {
    'січня': '01', 'лютого': '02', 'березня': '03', 'квітня': '04',
    'травня': '05', 'червня': '06', 'липня': '07', 'серпня': '08',
    'вересня': '09', 'жовтня': '10', 'листопада': '11', 'грудня': '12'
}

async def fetch_api_data():
    """
    ГОЛОВНА ФУНКЦІЯ ОТРИМАННЯ ДАНИХ.
    1. Завантажує дані з API (Світло) для всіх областей (Резерв + Інші області).
    2. Завантажує дані з сайту (Хмельницькобленерго).
    3. Якщо сайт працює -> замінює дані Хмельницької області на точніші.
    """
    # 1. Спочатку беремо загальну базу (щоб інші області теж працювали)
    api_data = await fetch_original_api_source()
    
    # 2. Пробуємо отримати точні дані з сайту (Пріоритет)
    try:
        site_data = await fetch_hoe_site()
        
        if site_data and api_data:
            # Шукаємо Хмельницьку область і підміняємо графік
            found = False
            for region in api_data.get('regions', []):
                if region['name_ua'] == 'Хмельницька':
                    # ПІДМІНА ДАНИХ:
                    # API може давати затримку, сайт - першоджерело.
                    region['schedule'] = site_data['regions'][0]['schedule']
                    found = True
                    break
            
            # Якщо раптом в API немає Хмельницької, додаємо її з сайту
            if not found:
                api_data.setdefault('regions', []).append(site_data['regions'][0])
                
        # Якщо API лежить, а сайт працює — повертаємо хоча б структуру з сайту
        elif site_data and not api_data:
            return site_data

    except Exception as e:
        print(f"⚠️ Помилка інтеграції сайту HOE: {e}")
        # Якщо сайт впав, ми просто повернемо api_data, який отримали на кроці 1

    return api_data

async def fetch_original_api_source():
    """Робить запит до резервного API (твоя стара функція)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
    except Exception as e:
        print(f"Помилка API (Backup): {e}")
    return None

# ==========================================
# === ПАРСЕР САЙТУ ХМЕЛЬНИЦЬКОБЛЕНЕРГО ===
# ==========================================

async def fetch_hoe_site():
    """Завантажує HTML сайту і парсить черги."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(HOE_SITE_URL, timeout=10) as response:
                if response.status != 200: return None
                html = await response.text()
                
        soup = BeautifulSoup(html, 'html.parser')
        post_div = soup.find('div', class_='post')
        if not post_div: return None

        schedule_map = {}
        current_date_str = None
        
        # Проходимо по контенту зверху вниз
        for element in post_div.children:
            text = element.get_text(strip=True) if element.name else ""
            
            # Шукаємо дату (наприклад: "23 січня")
            date_match = re.search(r'(\d{1,2})\s+([а-яієї]+)', text.lower())
            if date_match:
                day, month_name = date_match.groups()
                if month_name in UA_MONTHS:
                    year = datetime.now().year
                    if datetime.now().month == 12 and month_name == 'січня':
                        year += 1
                    
                    month = UA_MONTHS[month_name]
                    day = day.zfill(2)
                    current_date_str = f"{year}-{month}-{day}"
            
            # Шукаємо списки черг
            if element.name == 'ul' and current_date_str:
                for li in element.find_all('li'):
                    parse_queue_line(li.get_text(strip=True), current_date_str, schedule_map)

        if not schedule_map: return None

        # Повертаємо в тій же структурі, що і API
        return {
            "regions": [
                {
                    "name_ua": "Хмельницька",
                    "schedule": schedule_map
                }
            ]
        }
    except Exception as e:
        print(f"Site Parser Error: {e}")
        return None

def parse_queue_line(text, date_str, schedule_map):
    """Розбирає рядок 'підчерга 1.1 – з 00:00 до 04:00...'."""
    # Шукаємо номер черги
    queue_match = re.search(r"(\d\.\d)", text)
    if not queue_match: return

    queue_id = queue_match.group(1)
    
    # Шукаємо час (розуміє різні тире та слова)
    times = re.findall(r"(\d{2}:\d{2})\s*(?:до|-|–|—)\s*(\d{2}:\d{2})", text)
    
    formatted_intervals = []
    for start, end in times:
        formatted_intervals.append(f"{start}-{end}")

    if queue_id not in schedule_map:
        schedule_map[queue_id] = {}
    
    # === ВАЖЛИВА ЗМІНА: ЗАХИСТ ВІД ПЕРЕЗАПИСУ ===
    # Оскільки ми читаємо зверху вниз, перше знайдене - найактуальніше.
    # Якщо дані для цієї дати вже є - ігноруємо (це старий пост знизу).
    if date_str in schedule_map[queue_id]:
        return

    # Записуємо інтервали
    schedule_map[queue_id][date_str] = formatted_intervals

# ==========================================
# === ОНОВЛЕНІ ДОПОМІЖНІ ФУНКЦІЇ ===
# ==========================================

def calculate_off_hours(schedule_data):
    """
    Рахує суму годин без світла.
    Підтримує і старий словник {time: status}, і новий список ['start-end'].
    """
    if not schedule_data: 
        return 0.0
    
    # 1. Якщо це список інтервалів (з сайту)
    if isinstance(schedule_data, list):
        total_minutes = 0
        for item in schedule_data:
            try:
                start, end = item.split("-")
                # Фікс 24:00
                end_t = "23:59" if end == "24:00" else end
                bonus = 1 if end == "24:00" else 0
                
                t1 = datetime.strptime(start, "%H:%M")
                t2 = datetime.strptime(end_t, "%H:%M")
                
                diff = (t2 - t1).total_seconds() / 60 + bonus
                if diff < 0: diff += 24 * 60
                total_minutes += diff
            except: pass
        return round(total_minutes / 60, 1)

    # 2. Якщо це словник (старе API)
    elif isinstance(schedule_data, dict):
        # Рахуємо клітинки, де статус = 2 (відключення)
        count = sum(1 for k, v in schedule_data.items() if k != "24:00" and v == 2)
        return count * 0.5
    
    return 0

def calculate_on_hours(schedule_data):
    """Рахує суму годин ЗІ світлом."""
    if not schedule_data: return 0
    
    # Якщо список (сайт) -> 24 мінус години відключення
    if isinstance(schedule_data, list):
        off = calculate_off_hours(schedule_data)
        return max(0, 24.0 - off)
        
    # Якщо словник (API) -> рахуємо клітинки != 2
    elif isinstance(schedule_data, dict):
        count = sum(1 for k, v in schedule_data.items() if k != "24:00" and v != 2)
        return count * 0.5
    
    return 0

def parse_intervals(schedule_data, target_status=2, inverse=False):
    """
    Універсальний парсер інтервалів.
    Адаптований під обидва формати даних.
    """
    if not schedule_data: 
        return []
    
    # === ВАРІАНТ 1: ДАНІ З САЙТУ (Список рядків "00:00-04:00") ===
    if isinstance(schedule_data, list):
        # Сайт повертає ТІЛЬКИ відключення.
        # Якщо нам треба відключення (inverse=False) - повертаємо як є.
        if not inverse:
            result = []
            for i in schedule_data:
                try:
                    s, e = i.split("-")
                    result.append((s, e))
                except: pass
            return sorted(result)
        else:
            # Якщо треба "Світло Є" (inverse=True) з інтервалів відключень - це складно (інверсія).
            # Поки що для простоти повернемо пустий список або реалізуємо інверсію пізніше.
            # Щоб не ламати логіку, повертаємо пустий список для "світлого" режиму сайту поки що.
            return [] 

    # === ВАРІАНТ 2: ДАНІ З API (Словник "00:00": 2) ===
    elif isinstance(schedule_data, dict):
        times = sorted([k for k in schedule_data.keys() if k != "24:00"])
        intervals = []
        current_start = None
        in_interval = False 

        for t in times:
            val = schedule_data.get(t)
            
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
    
    return []

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
        # Якщо це дані з сайту (list), інверсія поки складна, тому покажемо заглушку або відключення
        if isinstance(schedule_json, list):
             # Тимчасово для сайту показуємо "Світло є" як "Всі години мінус відключення"
             # Але візуально покажемо відключення з іншим заголовком
             intervals = parse_intervals(schedule_json, target_status=2, inverse=False)
             emoji_main = "⬛" # Показуємо чорним, бо це відключення
             header_text = "Графік (дані з сайту - тільки відключення)"
        else:
             emoji_main = "🟢"
             header_text = f"Графік наявності світла"

        emoji_header = "💡"
        empty_text = "😔 **Світла не передбачено.** (Повний блекаут)"
        total_hours = calculate_on_hours(schedule_json)
        total_label = "✨ Всього зі світлом"

    else:
        # РЕЖИМ: ВІДКЛЮЧЕННЯ (BLACKOUT) - Стандартний
        intervals = parse_intervals(schedule_json, target_status=2, inverse=False)
        emoji_main = "🕒" 
        emoji_header = "💡"
        
        empty_text = "✅ **Відключень не передбачено.** (Світло є)"
        header_text = f"Графік відключень світла"
        
        total_hours = calculate_off_hours(schedule_json)
        total_label = "⚡️ Всього без світла"

    # ЗАГОЛОВОК
    when = "на завтра" if is_tomorrow else "на сьогодні"
    header = f"{emoji_header} **{header_text} {when}, {date_nice} ({day_name})**"

    # Якщо це завтра і список порожній у режимі blackout -> графіку ще немає (або немає відключень)
    if is_tomorrow and not intervals and display_mode == "blackout" and isinstance(schedule_json, dict):
         return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**\n(Або відключень не планується)"

    # ТІЛО ПОВІДОМЛЕННЯ
    if not intervals and total_hours == 0 and display_mode == "blackout":
        body = empty_text
    elif not intervals and display_mode == "light" and isinstance(schedule_json, dict):
        body = empty_text
    else:
        lines = []
        for start, end in intervals:
            # Тривалість
            try:
                t1 = datetime.strptime(start, "%H:%M")
                if end == "24:00":
                    diff = 24 - t1.hour - (t1.minute / 60)
                else:
                    t2 = datetime.strptime(end, "%H:%M")
                    diff = (t2 - t1).seconds / 3600
                
                diff_str = f"{int(diff)}" if diff.is_integer() else f"{diff:.1f}"
                lines.append(f"{emoji_main} **{start} — {end}** _({diff_str} год)_")
            except:
                lines.append(f"{emoji_main} **{start} — {end}**")
                
        body = "\n".join(lines)

    total_str = f"{int(total_hours)}" if total_hours.is_integer() else f"{total_hours:.1f}"

    text = (
        f"{header}\n"
        f"👤 Черга: **{queue_name}**\n"
        f"──────────────────\n"
        f"{body}\n"
        f"──────────────────\n"
    )
    
    # Додаємо підсумок годин
    if total_hours > 0 or display_mode == "light":
         text += f"{total_label}: **{total_str} год.**"
    else:
         text += f"⚡️ Світло має бути весь день."

    return text