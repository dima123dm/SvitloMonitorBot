# api_utils.py
import aiohttp
import asyncio
import re
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from config import API_URL, PRIMARY_API_URL, BACKUP_API_URL, FAILOVER_TIMEOUT, RECOVERY_CHECK_INTERVAL, HOE_SITE_URL
import database as db

# Словник для конвертації місяців
UA_MONTHS = {
    'січня': '01', 'лютого': '02', 'березня': '03', 'квітня': '04',
    'травня': '05', 'червня': '06', 'липня': '07', 'серпня': '08',
    'вересня': '09', 'жовтня': '10', 'листопада': '11', 'грудня': '12'
}

# === МАППІНГ НАЗВ РЕГІОНІВ ДЛЯ СУМІСНОСТІ ===
# Нове API використовує "Хмельницька область", старе — "Хмельницька"
# Нормалізуємо до формату, який вже є в базі юзерів
REGION_NAME_NORMALIZE = {
    "Хмельницька область": "Хмельницька",
    # Додати інші якщо знайдуться
}

# === СТАН FAILOVER СИСТЕМИ ===
api_state = {
    "active_source": "primary",      # "primary" або "backup"
    "primary_down_since": None,       # datetime коли впало основне
    "backup_down_since": None,        # datetime коли впало резервне
    "last_primary_check": None,       # коли останній раз перевіряли основне (з резерву)
    "last_switch": None,              # коли останній раз перемикались
    "consecutive_primary_fails": 0,   # підряд невдачі основного
    "consecutive_backup_fails": 0,    # підряд невдачі резервного
    "total_switches": 0,              # загальна кількість перемикань
    "last_emergency_regions": set(),  # регіони з екстреним режимом
}

# === КЕШ ДАНИХ (Захист від Thundering Herd) ===
api_cache = {
    "data": None,
    "timestamp": None
}
CACHE_TTL = 60  # Секунд (1 хвилина)

import os
if os.path.exists("api_cache.json"):
    try:
        with open("api_cache.json", "r", encoding="utf-8") as f:
            api_cache["data"] = json.load(f)
            print("💾 Локальний кеш api_cache.json успішно завантажено при запуску!")
    except Exception as e:
        print(f"⚠️ Помилка завантаження кешу з файлу: {e}")


def normalize_region_names(data):
    """Приводить назви регіонів до стандарту бота (для сумісності з базою)."""
    if not data or 'regions' not in data:
        return data
    for region in data['regions']:
        orig = region.get('name_ua', '')
        
        # 1. Прибираємо слово " область" з кінця
        if orig.endswith(" область"):
            orig = orig.replace(" область", "").strip()
            
        # 2. Ручна заміна специфічних назв
        if orig in REGION_NAME_NORMALIZE:
            orig = REGION_NAME_NORMALIZE[orig]
            
        region['name_ua'] = orig
        
    return data


async def fetch_primary_api():
    """Запит до основного DTEK Proxy API (нове)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PRIMARY_API_URL, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    raw = await response.json()
                    # Подвійний парсинг: body — це JSON-рядок
                    body_str = raw.get('body', '{}')
                    if isinstance(body_str, str):
                        data = json.loads(body_str)
                    else:
                        data = body_str
                    # Нормалізація назв для сумісності
                    data = normalize_region_names(data)
                    return data
                else:
                    print(f"⚠️ Primary API HTTP {response.status}")
    except asyncio.TimeoutError:
        print("⚠️ Primary API: Timeout")
    except json.JSONDecodeError as e:
        print(f"⚠️ Primary API JSON Error: {e}")
    except Exception as e:
        print(f"❌ Primary API Error: {e}")
    return None


async def fetch_backup_api():
    """Запит до резервного API (попереднє джерело)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BACKUP_API_URL, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"⚠️ Backup API HTTP {response.status}")
    except asyncio.TimeoutError:
        print("⚠️ Backup API: Timeout")
    except Exception as e:
        print(f"❌ Backup API Error: {e}")
    return None

def merge_api_data(primary, backup):
    """Гібридне злиття: Primary (детальніші черги) + Backup (більше областей і дат).
    
    Логіка:
    - Регіони з Primary мають пріоритет (бо там більше черг, наприклад Київ — 60)
    - Регіони, яких немає в Primary, добираються з Backup
    - Emergency прапорець з Primary зберігається
    - Якщо в Primary немає даних на певну дату для черги — беремо з Backup
      (наприклад: Primary має тільки 2026-04-11, а Backup має 2026-04-13)
    ВАЖЛИВО: Жодних додаткових HTTP-запитів — backup вже завжди запитується
    в гібридному режимі всередині fetch_api_data().
    """
    if not primary or 'regions' not in primary:
        return backup
    if not backup or 'regions' not in backup:
        return primary

    # Індексуємо backup регіони по назві для швидкого пошуку
    backup_by_name = {r['name_ua']: r for r in backup['regions']}

    # Індексуємо primary регіони по назві
    primary_names = {r['name_ua'] for r in primary['regions']}

    for primary_region in primary['regions']:
        p_name = primary_region.get('name_ua', '')
        p_schedule = primary_region.get('schedule')
        if not p_schedule or not isinstance(p_schedule, dict):
            continue

        # Шукаємо відповідний регіон в backup
        b_region = backup_by_name.get(p_name)
        if not b_region:
            continue
        b_schedule = b_region.get('schedule')
        if not b_schedule or not isinstance(b_schedule, dict):
            continue

        # Для кожної черги в primary — добираємо відсутні дати з backup
        for queue_id, p_queue_data in p_schedule.items():
            b_queue_data = b_schedule.get(queue_id)
            if not b_queue_data or not isinstance(b_queue_data, dict):
                continue
            if not isinstance(p_queue_data, dict):
                continue

            # Додаємо дати з backup, яких немає в primary
            for date_str, date_data in b_queue_data.items():
                if date_str not in p_queue_data:
                    p_queue_data[date_str] = date_data
                    print(f"[merge] {p_name}/{queue_id}: добавлено дату {date_str} з backup")

    # Додаємо з backup ті регіони, яких зовсім немає в primary
    for backup_region in backup['regions']:
        name = backup_region.get('name_ua', '')
        if name not in primary_names and backup_region.get('schedule') is not None:
            primary['regions'].append(backup_region)

    # Зберігаємо дати з обох (Primary має пріоритет)
    if 'date_today' not in primary and 'date_today' in backup:
        primary['date_today'] = backup['date_today']
    if 'date_tomorrow' not in primary and 'date_tomorrow' in backup:
        primary['date_tomorrow'] = backup['date_tomorrow']

    return primary


async def fetch_api_data():
    """ГОЛОВНА ФУНКЦІЯ ОТРИМАННЯ ДАНИХ — Гібридний режим з Failover."""
    global api_state, api_cache
    now = datetime.now()
    
    # 1. Перевіряємо кеш (Захист від Thundering Herd)
    if api_cache["timestamp"] is not None:
        elapsed_cache = (now - api_cache["timestamp"]).total_seconds()
        if elapsed_cache < CACHE_TTL:
            return api_cache["data"]

    primary_data = None
    backup_data = None

    if api_state["active_source"] == "primary":
        # === Активне джерело: ОСНОВНЕ (DTEK Proxy) ===
        primary_data = await fetch_primary_api()

        if primary_data:
            # Основне працює — скидаємо лічильники
            api_state["consecutive_primary_fails"] = 0
            api_state["primary_down_since"] = None
            
            # === ГІБРИД: Добираємо відсутні регіони з Backup ===
            backup_data = await fetch_backup_api()
            if backup_data:
                primary_data = merge_api_data(primary_data, backup_data)
                api_state["consecutive_backup_fails"] = 0
                api_state["backup_down_since"] = None
        else:
            # Основне НЕ працює
            api_state["consecutive_primary_fails"] += 1

            if api_state["primary_down_since"] is None:
                api_state["primary_down_since"] = now
                print(f"⚠️ Основне API не відповідає. Початок відліку failover...")

            elapsed = (now - api_state["primary_down_since"]).total_seconds()

            if elapsed >= FAILOVER_TIMEOUT:
                # 2+ години без відповіді — ПЕРЕМИКАННЯ на резерв
                print(f"🔄 FAILOVER: Перемикання на РЕЗЕРВНЕ API! (Основне не працює {elapsed/3600:.1f} год)")
                api_state["active_source"] = "backup"
                api_state["last_primary_check"] = now
                api_state["last_switch"] = now
                api_state["total_switches"] += 1
            
            # Так чи інакше — беремо з резерву
            backup_data = await fetch_backup_api()

    else:
        # === Активне джерело: РЕЗЕРВНЕ ===
        should_check_primary = False

        if api_state["last_primary_check"]:
            elapsed_since_check = (now - api_state["last_primary_check"]).total_seconds()
            if elapsed_since_check >= RECOVERY_CHECK_INTERVAL:
                should_check_primary = True
        else:
            should_check_primary = True

        if should_check_primary:
            print("🔍 Перевірка основного API (recovery check)...")
            primary_data = await fetch_primary_api()
            api_state["last_primary_check"] = now

            if primary_data:
                print("✅ Основне API відновлено! Повертаємось на PRIMARY.")
                api_state["active_source"] = "primary"
                api_state["primary_down_since"] = None
                api_state["consecutive_primary_fails"] = 0
                api_state["last_switch"] = now
                api_state["total_switches"] += 1

        # Завжди беремо backup коли в резервному режимі
        backup_data = await fetch_backup_api()

        # Лічильник помилок резервного
        if backup_data:
            api_state["consecutive_backup_fails"] = 0
            api_state["backup_down_since"] = None
        else:
            api_state["consecutive_backup_fails"] += 1
            if api_state["backup_down_since"] is None:
                api_state["backup_down_since"] = now

            # Якщо резерв теж лежить 2+ години — пробуємо основне примусово
            if api_state["backup_down_since"]:
                elapsed_backup = (now - api_state["backup_down_since"]).total_seconds()
                if elapsed_backup >= FAILOVER_TIMEOUT:
                    # Використовуємо результат recovery check якщо він вже був
                    if primary_data is None:
                        print("🔄 FAILOVER: Резервне теж не працює 2+ год! Пробуємо основне...")
                        primary_data = await fetch_primary_api()
                    if primary_data:
                        api_state["active_source"] = "primary"
                        api_state["primary_down_since"] = None
                        api_state["backup_down_since"] = None
                        api_state["last_switch"] = now
                        api_state["total_switches"] += 1
                        print("✅ Основне API працює! Повернулись.")

        # Гібрид в резервному режимі (якщо recovery check дав primary_data)
        if primary_data and backup_data:
            backup_data = merge_api_data(primary_data, backup_data)
    
    # === ЗБИРАЄМО ФІНАЛЬНІ ДАНІ ===
    data = primary_data or backup_data

    # === ОБРОБКА ЕКСТРЕНИХ ВІДКЛЮЧЕНЬ (emergency) ===
    if data and 'regions' in data:
        current_emergency = set()
        for region in data['regions']:
            if region.get('emergency', False):
                current_emergency.add(region['name_ua'])
        api_state["last_emergency_regions"] = current_emergency

    # === ІНТЕГРАЦІЯ САЙТУ HOE (як раніше) ===
    is_site_enabled = await db.get_system_config('hoe_site_enabled', '1')

    if is_site_enabled == '1':
        try:
            site_data = await fetch_hoe_site()
            if site_data and data:
                found = False
                for region in data.get('regions', []):
                    if region['name_ua'] == 'Хмельницька':
                        region['schedule'] = site_data['regions'][0]['schedule']
                        found = True
                        break
                if not found:
                    data.setdefault('regions', []).append(site_data['regions'][0])
            elif site_data and not data:
                api_cache["data"] = site_data
                api_cache["timestamp"] = now
                return site_data
        except Exception as e:
            print(f"⚠️ Помилка інтеграції сайту HOE: {e}")

    # Оновлюємо кеш ТІЛЬКИ якщо ми отримали нові дані
    if data:
        api_cache["data"] = data
        api_cache["timestamp"] = now
        try:
            with open("api_cache.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Помилка запису api_cache.json: {e}")
    else:
        # Якщо API впало, ми повертаємо старий кеш (якщо він є), щоб бот не видавав "Дані оновлюються..."
        # Змінюємо timestamp, щоб наступний запит не пішов одразу ж (throttle)
        api_cache["timestamp"] = now

    return api_cache["data"]


def get_api_status():
    """Повертає поточний стан API для адмін-панелі."""
    state = api_state.copy()
    now = datetime.now()

    result = {
        "active_source": state["active_source"],
        "active_source_name": "🟢 DTEK Proxy (Основне)" if state["active_source"] == "primary" else "🟡 Резервне API",
        "total_switches": state["total_switches"],
        "last_switch": state["last_switch"].strftime("%d.%m %H:%M") if state["last_switch"] else "—",
        "primary_fails": state["consecutive_primary_fails"],
        "backup_fails": state["consecutive_backup_fails"],
        "emergency_regions": list(state.get("last_emergency_regions", [])),
    }

    # Час downtime основного
    if state["primary_down_since"]:
        elapsed = (now - state["primary_down_since"]).total_seconds()
        result["primary_downtime"] = f"{elapsed/60:.0f} хв"
    else:
        result["primary_downtime"] = "—"

    # Час до наступної перевірки recovery
    if state["active_source"] == "backup" and state["last_primary_check"]:
        elapsed = (now - state["last_primary_check"]).total_seconds()
        remaining = max(0, RECOVERY_CHECK_INTERVAL - elapsed)
        result["next_recovery_check"] = f"{remaining/3600:.1f} год"
    else:
        result["next_recovery_check"] = "—"

    return result


# === ОРИГІНАЛЬНА ЛОГІКА (парсинг/форматування — БЕЗ ЗМІН) ===


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
        # Але для простоти, якщо це API зі статусами, краще відштовхуватися від зворотного
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

    if schedule_json is None:
        if is_tomorrow:
            return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**"
        else:
            return "⏳ **Дані оновлюються...**"

    timeline = []
    
    # === ВИПРАВЛЕННЯ 1: Якщо режим "light", НЕ показуємо години відключень ===
    if display_mode != "light":
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
    
    if display_mode == "light":
        header_text = f"Графік наявності світла"
        emoji_main = "🟢"
        empty_text_bad = "😔 **Світла не передбачено.** (Повний блекаут)" 
        empty_text_good = "✨ **Світло є весь день!** (Відключень не передбачено)"
    else:
        header_text = f"Графік відключень"
        emoji_main = "🕒"
        empty_text_good = "✅ **Відключень не передбачено.**"
        empty_text_bad = "✅ **Відключень не передбачено.**" 

    header = f"{emoji_header} **{header_text} {when}, {date_nice} ({day_name})**"

    # СТАТИСТИКА
    total_off = calculate_off_hours(schedule_json)
    total_possible = calculate_possible_hours(schedule_json)
    total_on = calculate_on_hours(schedule_json)

    # === ВИПРАВЛЕННЯ 2: Захист для "завтра" ===
    # Якщо це завтра і відключень 0 - вважаємо, що графік ще не дали
    if is_tomorrow and total_off == 0:
         return f"🕒 **Графік на завтра ({date_nice}) ще не оприлюднено.**"

    if not timeline:
        if total_off == 0:
            body = empty_text_good
        else:
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

    # === НОВЕ: Мітка джерела даних ===
    source_label = "🌐" if api_state["active_source"] == "primary" else "🔄"

    text = (
        f"{header}\n"
        f"👤 Черга: **{queue_name}**\n"
        f"──────────────────\n"
        f"{body}\n"
        f"──────────────────\n"
        f"{stats_text}"
    )
    return text