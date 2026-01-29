# api_utils.py
import aiohttp
import asyncio
import re
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from config import API_URL
import database as db

# URL офіційного сайту
HOE_SITE_URL = "https://hoe.com.ua/page/pogodinni-vidkljuchennja"

# Словник для конвертації місяців
UA_MONTHS = {
    'січня': '01', 'лютого': '02', 'березня': '03', 'квітня': '04',
    'травня': '05', 'червня': '06', 'липня': '07', 'серпня': '08',
    'вересня': '09', 'жовтня': '10', 'листопада': '11', 'грудня': '12'
}

async def fetch_api_data():
    """ГОЛОВНА ФУНКЦІЯ ОТРИМАННЯ ДАНИХ."""
    api_data = await fetch_original_api_source()
    is_site_enabled = await db.get_system_config('hoe_site_enabled', '1')

    if is_site_enabled == '1':
        try:
            site_data = await fetch_hoe_site()
            if site_data and api_data:
                found = False
                for region in api_data.get('regions', []):
                    if region['name_ua'] == 'Хмельницька':
                        region['schedule'] = site_data['regions'][0]['schedule']
                        found = True
                        break
                if not found:
                    api_data.setdefault('regions', []).append(site_data['regions'][0])
            elif site_data and not api_data:
                return site_data
        except Exception as e:
            print(f"⚠️ Помилка інтеграції сайту HOE: {e}")

    return api_data

async def fetch_original_api_source():
    """Робить запит до резервного API."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
    except Exception as e:
        print(f"Помилка API (Backup): {e}")
    return None

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
        
        for element in post_div.children:
            text = element.get_text(strip=True) if element.name else ""
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
            
            if element.name == 'ul' and current_date_str:
                for li in element.find_all('li'):
                    parse_queue_line(li.get_text(strip=True), current_date_str, schedule_map)

        if not schedule_map: return None

        return {"regions": [{"name_ua": "Хмельницька", "schedule": schedule_map}]}
    except Exception as e:
        print(f"Site Parser Error: {e}")
        return None

def parse_queue_line(text, date_str, schedule_map):
    queue_match = re.search(r"(\d\.\d)", text)
    if not queue_match: return
    queue_id = queue_match.group(1)
    times = re.findall(r"(\d{2}:\d{2})\s*(?:до|-|–|—)\s*(\d{2}:\d{2})", text)
    formatted_intervals = []
    for start, end in times:
        formatted_intervals.append(f"{start}-{end}")
    if queue_id not in schedule_map:
        schedule_map[queue_id] = {}
    if date_str in schedule_map[queue_id]:
        return
    schedule_map[queue_id][date_str] = formatted_intervals

def calculate_off_hours(schedule_data):
    """Рахує суму годин БЕЗ світла (гарантовані, статус 2)."""
    if not schedule_data: return 0.0
    
    if isinstance(schedule_data, list):
        total_minutes = 0
        for item in schedule_data:
            try:
                start, end = item.split("-")
                end_t = "23:59" if end == "24:00" else end
                bonus = 1 if end == "24:00" else 0
                t1 = datetime.strptime(start, "%H:%M")
                t2 = datetime.strptime(end_t, "%H:%M")
                diff = (t2 - t1).total_seconds() / 60 + bonus
                if diff < 0: diff += 24 * 60
                total_minutes += diff
            except: pass
        return round(total_minutes / 60, 1)

    elif isinstance(schedule_data, dict):
        count = sum(1 for k, v in schedule_data.items() if k != "24:00" and v == 2)
        return count * 0.5
    return 0

def calculate_possible_hours(schedule_data):
    if isinstance(schedule_data, dict):
        count = sum(1 for k, v in schedule_data.items() if k != "24:00" and v == 3)
        return count * 0.5
    return 0

def calculate_on_hours(schedule_data):
    """Рахує суму годин ЗІ світлом."""
    if not schedule_data: return 24.0 # Якщо даних немає (пусто), вважаємо що світло є
    
    if isinstance(schedule_data, list):
        off = calculate_off_hours(schedule_data)
        return max(0, 24.0 - off)
    elif isinstance(schedule_data, dict):
        count = sum(1 for k, v in schedule_data.items() if k != "24:00" and v == 1)
        # Якщо немає явних 1, але і немає 2/3, треба рахувати все інше як світло
        off = calculate_off_hours(schedule_data)
        poss = calculate_possible_hours(schedule_data)
        return max(0, 24.0 - off - poss)
    return 24.0

def parse_intervals(schedule_data, target_status=None, inverse=False):
    if not schedule_data: return []
    
    if isinstance(schedule_data, list):
        if not inverse and (target_status == 2 or target_status is None):
            result = []
            for i in schedule_data:
                try:
                    s, e = i.split("-")
                    result.append((s, e))
                except: pass
            return sorted(result)
        else:
            return [] 

    elif isinstance(schedule_data, dict):
        times = sorted([k for k in schedule_data.keys() if k != "24:00"])
        intervals = []
        current_start = None
        in_interval = False 

        for t in times:
            val = schedule_data.get(t)
            is_active = False
            if inverse:
                is_active = (val == 1)
            else:
                if target_status is not None:
                    is_active = (val == target_status)
                else:
                    is_active = False

            if is_active and not in_interval:
                current_start = t
                in_interval = True
            elif not is_active and in_interval:
                if current_start:
                    intervals.append((current_start, t))
                in_interval = False
                current_start = None
                
        if in_interval and current_start:
            intervals.append((current_start, "24:00"))
        return intervals
    return []

def invert_schedule_for_site(blackout_intervals):
    light_intervals = []
    parsed_blackouts = []
    for interval in blackout_intervals:
        try:
            start_s, end_s = interval.split('-')
            sh, sm = map(int, start_s.split(':'))
            start_min = sh * 60 + sm
            if end_s == "24:00": end_min = 1440
            else:
                eh, em = map(int, end_s.split(':'))
                end_min = eh * 60 + em
                if end_min == 0 and start_min > 0: end_min = 1440
            parsed_blackouts.append((start_min, end_min))
        except: continue
            
    parsed_blackouts.sort()
    last_end = 0
    for start, end in parsed_blackouts:
        if start > last_end:
            light_intervals.append((last_end, start))
        last_end = max(last_end, end)
        
    if last_end < 1440:
        light_intervals.append((last_end, 1440))
        
    result = []
    for start, end in light_intervals:
        s_h, s_m = divmod(start, 60)
        e_h, e_m = divmod(end, 60)
        s_str = f"{s_h:02}:{s_m:02}"
        e_str = "24:00" if end == 1440 else f"{e_h:02}:{e_m:02}"
        result.append((s_str, e_str))
    return result

def format_message(schedule_json, queue_name, date_str, is_tomorrow=False, display_mode="blackout"):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    days = {"Monday": "Понеділок", "Tuesday": "Вівторок", "Wednesday": "Середа",
            "Thursday": "Четвер", "Friday": "П'ятниця", "Saturday": "Субота", "Sunday": "Неділя"}
    day_name = days.get(dt.strftime("%A"), dt.strftime("%A"))
    date_nice = dt.strftime('%d.%m')

    # Якщо даних немає взагалі - повертаємо статус "Оновлюється / Не оприлюднено"
    if schedule_json is None:
        if is_tomorrow:
            return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**"
        else:
            return "⏳ **Дані оновлюються...**"

    timeline = []
    confirmed = parse_intervals(schedule_json, target_status=2)
    for s, e in confirmed: timeline.append((s, e, 2))

    if isinstance(schedule_json, dict):
        possible = parse_intervals(schedule_json, target_status=3)
        for s, e in possible: timeline.append((s, e, 3))

    if display_mode == "light":
        if isinstance(schedule_json, dict):
            light_ints = parse_intervals(schedule_json, target_status=1, inverse=False)
        else:
            raw_blackouts = parse_intervals(schedule_json, target_status=2, inverse=False)
            str_blackouts = [f"{s}-{e}" for s, e in raw_blackouts]
            light_tuples = invert_schedule_for_site(str_blackouts)
            light_ints = light_tuples
        for s, e in light_ints: timeline.append((s, e, 1))

    timeline.sort(key=lambda x: x[0])

    when = "на завтра" if is_tomorrow else "на сьогодні"
    emoji_header = "💡"
    
    # Визначаємо тексти для порожніх випадків
    if display_mode == "light":
        header_text = f"Графік наявності світла"
        emoji_main = "🟢"
        # "Поганий" текст (якщо timeline пустий і total_off > 0 - це повний блекаут)
        empty_text_bad = "😔 **Світла не передбачено.** (Повний блекаут)" 
        # "Хороший" текст (якщо timeline пустий і total_off == 0 - це зелений день)
        empty_text_good = "✨ **Світло є весь день!** (Відключень не передбачено)"
    else:
        header_text = f"Графік відключень"
        emoji_main = "🕒"
        empty_text_good = "✅ **Відключень не передбачено.**"
        empty_text_bad = "✅ **Відключень не передбачено.**" # Для режиму блекауту порожній список завжди добре

    header = f"{emoji_header} **{header_text} {when}, {date_nice} ({day_name})**"

    # Рахуємо години для перевірки
    total_off = calculate_off_hours(schedule_json)
    total_possible = calculate_possible_hours(schedule_json)
    total_on = calculate_on_hours(schedule_json)

    # Якщо список інтервалів порожній
    if not timeline:
        # ПЕРЕВІРКА: Якщо годин відключень 0 -> Значить все добре
        if total_off == 0:
            body = empty_text_good
        else:
            # Якщо години відключення є, але timeline пустий (дивний збіг) або режим "light" без світла
            body = empty_text_bad
    else:
        lines = []
        for start, end, type_code in timeline:
            if type_code == 1: emoji = "🟢"; suffix = ""
            elif type_code == 2: emoji = "🕒"; suffix = ""
            elif type_code == 3: emoji = "⚠️"; suffix = " _(Можливе)_"
            else: emoji = "❓"; suffix = ""

            try:
                t1 = datetime.strptime(start, "%H:%M")
                if end == "24:00":
                    diff = 24 - t1.hour - (t1.minute / 60)
                else:
                    t2 = datetime.strptime(end, "%H:%M")
                    diff = (t2 - t1).seconds / 3600
                diff_str = f"{int(diff)}" if diff.is_integer() else f"{diff:.1f}"
                lines.append(f"{emoji} **{start} — {end}**{suffix} _({diff_str} год)_")
            except:
                lines.append(f"{emoji} **{start} — {end}**{suffix}")
        body = "\n".join(lines)

    stats_text = ""
    if display_mode == "light":
         stats_text += f"✨ Всього зі світлом: **{total_on:g} год.**"
    else:
         stats_text += f"⚡️ Гарантовано без світла: **{total_off:g} год.**"

    if total_possible > 0:
        stats_text += f"\n⚠️ Можливо без світла: **{total_possible:g} год.**"

    text = (
        f"{header}\n"
        f"👤 Черга: **{queue_name}**\n"
        f"──────────────────\n"
        f"{body}\n"
        f"──────────────────\n"
        f"{stats_text}"
    )
    return text