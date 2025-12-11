# handlers.py
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, InlineKeyboardButton

import database as db
import api_utils as api

router = Router()


def get_main_keyboard():
    """Створює нижнє меню з кнопками."""
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📅 Графік на сьогодні"), KeyboardButton(text="🔮 Графік на завтра"))
    kb.row(KeyboardButton(text="📊 Аналітика"), KeyboardButton(text="⚙️ Налаштування"))
    return kb.as_markup(resize_keyboard=True)


@router.message(Command("start"))
async def start_command(message: types.Message):
    """Команда /start - вітання та початок налаштування."""
    text = (
        "👋 **Вітаю! Це бот Моніторингу Світла.**\n\n"
        "Я допоможу вам:\n"
        "💡 Дізнатися актуальний графік.\n"
        "🔔 Отримувати сповіщення.\n"
        "📊 Переглядати статистику.\n\n"
        "👇 **Оберіть вашу область:**"
    )
    await show_regions_menu(message, text)


async def show_regions_menu(message: types.Message, text):
    data = await api.fetch_api_data()
    if not data:
        await message.answer("⚠️ Помилка отримання даних.")
        return

    kb = InlineKeyboardBuilder()
    for region in data['regions']:
        kb.button(text=region['name_ua'], callback_data=f"reg|{region['name_ua']}")
    
    kb.adjust(2)
    
    # Додаємо кнопку відписки
    kb.row(InlineKeyboardButton(text="🔕 Зупинити бота (Відписатися)", callback_data="unsub"))

    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("reg|"))
async def select_region(callback: types.CallbackQuery):
    """Користувач обрав область, показуємо черги."""
    region_name = callback.data.split("|")[1]
    data = await api.fetch_api_data()

    kb = InlineKeyboardBuilder()
    for r in data['regions']:
        if r['name_ua'] == region_name:
            for q in sorted(r['schedule'].keys()):
                kb.button(text=f"Черга {q}", callback_data=f"q|{region_name}|{q}")
            break
    kb.adjust(3)
    await callback.message.edit_text(f"📍 **{region_name}**. Оберіть чергу:", reply_markup=kb.as_markup(),
                                     parse_mode="Markdown")


@router.callback_query(F.data.startswith("q|"))
async def select_queue(callback: types.CallbackQuery):
    """Користувач обрав чергу, зберігаємо в БД."""
    _, region, queue = callback.data.split("|")
    await db.save_user(callback.from_user.id, region, queue)

    await callback.message.delete()
    await callback.message.answer(f"✅ Налаштування збережено!\n📍 {region}, Черга {queue}",
                                  reply_markup=get_main_keyboard())

    # Одразу показуємо графік
    await show_today_schedule(callback.message, region, queue)


async def show_today_schedule(message, region, queue):
    today = datetime.now().strftime('%Y-%m-%d')
    data = await api.fetch_api_data()
    schedule = None

    if data:
        for r in data['regions']:
            if r['name_ua'] == region:
                schedule = r['schedule'].get(queue, {}).get(today)
                break

    # Зберігаємо статистику
    if schedule:
        await db.save_stats(region, queue, today, api.calculate_off_hours(schedule))

    text = api.format_message(schedule, queue, today, is_tomorrow=False)
    await message.answer(text, parse_mode="Markdown")


# --- Обробка кнопок меню ---

@router.message(F.text == "⚙️ Налаштування")
async def btn_settings(message: types.Message):
    await show_regions_menu(message, "⚙️ **Налаштування**\nОберіть область:")


@router.message(F.text == "📅 Графік на сьогодні")
async def btn_today(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        return await message.answer("Спочатку зробіть налаштування.")
    await show_today_schedule(message, user[0], user[1])


@router.message(F.text == "🔮 Графік на завтра")
async def btn_tomorrow(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: return await message.answer("Спочатку налаштування.")

    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    data = await api.fetch_api_data()
    schedule = None

    if data:
        for r in data['regions']:
            if r['name_ua'] == user[0]:
                schedule = r['schedule'].get(user[1], {}).get(tomorrow, None)
                break

    if schedule:
        await db.save_stats(user[0], user[1], tomorrow, api.calculate_off_hours(schedule))

    text = api.format_message(schedule, user[1], tomorrow, is_tomorrow=True)
    await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "📊 Аналітика")
async def btn_stats(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: return

    rows = await db.get_stats_data(user[0], user[1])
    if not rows:
        return await message.answer("📉 **Статистика пуста.**\nПоки що немає даних.")

    # Сортуємо: старі -> нові
    rows.sort(key=lambda x: x[0])

    total = 0
    lines = []
    for r in rows:
        val = r[1]
        total += val
        val_str = f"{int(val)}" if val.is_integer() else f"{val:.1f}"
        
        # Конвертуємо дату у формат День.Місяць
        dt_obj = datetime.strptime(r[0], "%Y-%m-%d")
        date_nice = dt_obj.strftime("%d.%m")
        
        lines.append(f"▫️ {date_nice}:  **{val_str} год.**")

    total_str = f"{int(total)}" if total.is_integer() else f"{total:.1f}"

    text = (
            f"📊 **Статистика (останні 7 днів)**\n"
            f"📍 {user[0]}, Черга {user[1]}\n\n" +
            "\n".join(lines) +
            f"\n──────────────────\n"
            f"⚡️ Загалом: **{total_str} год.**"
    )
    await message.answer(text, parse_mode="Markdown")


@router.callback_query(F.data == "unsub")
async def unsub_handler(callback: types.CallbackQuery):
    """Видаляє користувача з бази даних."""
    await db.delete_user(callback.from_user.id)
    
    await callback.message.edit_text(
        "🔕 **Ви успішно відписалися.**\n\n"
        "Бот більше не надсилатиме вам сповіщення.\n"
        "Якщо захочете повернутися — просто натисніть /start або налаштуйте область знову.",
        parse_mode="Markdown"
    )