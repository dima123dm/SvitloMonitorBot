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


def parse_intervals(schedule_json):
    """Перетворює JSON у список інтервалів часу (початок, кінець)."""
    if not schedule_json:
        return []

    times = sorted([k for k in schedule_json.keys() if k != "24:00"])
    intervals = []
    current_start = None
    is_offline = False

    for t in times:
        status = schedule_json.get(t)
        # Якщо почалося відключення
        if status == 2 and not is_offline:
            current_start = t
            is_offline = True
        # Якщо світло увімкнули
        elif status != 2 and is_offline:
            if current_start:
                intervals.append((current_start, t))
            is_offline = False
            current_start = None

    # Якщо відключення триває до кінця доби
    if is_offline and current_start:
        intervals.append((current_start, "24:00"))

    return intervals


def format_message(schedule_json, queue_name, date_str, is_tomorrow=False):
    """Створює красивий текст повідомлення."""
    # Якщо даних немає взагалі
    if schedule_json is None:
        if is_tomorrow:
            return "🕒 **Графік на завтра ще не оприлюднено.**"
        else:
            return "⏳ **Дані оновлюються...**"

    # Визначаємо день тижня
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    days = {"Monday": "Понеділок", "Tuesday": "Вівторок", "Wednesday": "Середа",
            "Thursday": "Четвер", "Friday": "П'ятниця", "Saturday": "Субота", "Sunday": "Неділя"}
    day_name = days.get(dt.strftime("%A"), dt.strftime("%A"))

    # Заголовок
    if is_tomorrow:
        header = f"🔮 **Графік відключень світла на завтра, {dt.strftime('%d.%m')} ({day_name})**"
    else:
        header = f"💡 **Графік відключень світла на сьогодні, {dt.strftime('%d.%m')} ({day_name})**"

    intervals = parse_intervals(schedule_json)

    # Якщо це завтра і список порожній -> графіку ще немає
    if is_tomorrow and not intervals:
        return f"🕒 **Графік на завтра ({dt.strftime('%d.%m')}) ще не оприлюднено.**\n(Або відключень не планується)"

    if not intervals:
        body = "✅ **Відключень не передбачено.**"
    else:
        lines = []
        for start, end in intervals:
            # Вираховуємо тривалість для кожного відключення
            t1 = datetime.strptime(start, "%H:%M")
            if end == "24:00":
                diff = 24 - t1.hour - (t1.minute / 60)
            else:
                t2 = datetime.strptime(end, "%H:%M")
                diff = (t2 - t1).seconds / 3600

            # Форматуємо число (прибираємо .0)
            diff_str = f"{int(diff)}" if diff.is_integer() else f"{diff:.1f}"
            lines.append(f"🕒 **{start} — {end}** _({diff_str} год)_")
        body = "\n".join(lines)

    total = calculate_off_hours(schedule_json)
    total_str = f"{int(total)}" if total.is_integer() else f"{total:.1f}"

    text = (
        f"{header}\n"
        f"👤 Черга: **{queue_name}**\n"
        f"──────────────────\n"
        f"{body}\n"
        f"──────────────────\n"
    )
    if total > 0:
        text += f"⚡️ Всього: **{total_str} год.**"

    return text