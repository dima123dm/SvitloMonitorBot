# handlers.py
from datetime import datetime, timedelta
import asyncio
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, InlineKeyboardButton

import database as db
import api_utils as api
import scheduler

ADMIN_ID = 723550550  # Ваш ID адміна

router = Router()

# --- ДОПОМІЖНА ФУНКЦІЯ ЧАСУ ---
def get_local_now():
    """Повертає поточний час."""
    return datetime.now()

def get_main_keyboard(user_id=None):
    """Створює нижнє меню. Якщо user_id == ADMIN_ID, додає кнопку панелі."""
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📅 Графік на сьогодні"), KeyboardButton(text="🔮 Графік на завтра"))
    kb.row(KeyboardButton(text="📊 Аналітика"), KeyboardButton(text="⚙️ Налаштування"))
    kb.row(KeyboardButton(text="💬 Підтримка"))
    
    # Додаємо кнопку тільки Адміну
    if user_id == ADMIN_ID:
        kb.row(KeyboardButton(text="👨‍💼 Адмін-панель"))
        
    return kb.as_markup(resize_keyboard=True)


@router.message(Command("start"))
async def start_command(message: types.Message, command: CommandObject):
    """Команда /start."""
    # Підтримка Deep Linking (якщо перейшли по посиланню налаштувань)
    if command.args == "settings":
        # Перевіряємо, чи є користувач в базі взагалі
        user = await db.get_user(message.from_user.id)
        if user:
            await show_settings_main(message, message.from_user.id)
            return

    # Перевіряємо, чи знаємо ми цього юзера глобально
    user = await db.get_user(message.from_user.id)
    
    if user:
        # Текст вітання залежить від того, де ми (група чи особисті)
        if message.chat.type in ['group', 'supergroup']:
            welcome_text = f"👋 **Привіт!**\nЯ знаю твої налаштування: **{user[0]}, Черга {user[1]}**.\nТи можеш налаштувати сповіщення окремо для цієї групи в меню."
        else:
            welcome_text = f"👋 **Ласкаво просимо назад!**\n📍 Ваш вибір: **{user[0]}, Черга {user[1]}**"
            
        await message.answer(
            welcome_text,
            reply_markup=get_main_keyboard(message.from_user.id),
            parse_mode="Markdown"
        )
        return
    
    # Якщо юзера немає в базі
    text = (
        "👋 **Вітаю! Це бот Моніторингу Світла.**\n"
        "👇 **Оберіть вашу область:**"
    )
    await show_regions_menu(message, text)


# === НОВА КОМАНДА /grafik ===
@router.message(Command("grafik"))
async def grafik_command(message: types.Message):
    """Виводить графік на сьогодні для користувача."""
    user = await db.get_user(message.from_user.id)
    
    # Якщо юзер не налаштований
    if not user:
        if message.chat.type in ['group', 'supergroup']:
             # У групі просимо писати в лічку
             await message.reply("⚠️ Я не знаю вашого регіону. Напишіть мені /start в особисті повідомлення, щоб налаштувати.")
        else:
             # В особистих просто кажемо налаштувати
             await message.answer("Спочатку зробіть налаштування через /start.")
        return

    # Викликаємо функцію показу графіку
    await show_today_schedule(message, user[0], user[1], user_id=message.from_user.id)


# ==========================================
# === НОВЕ ЗРУЧНЕ МЕНЮ НАЛАШТУВАНЬ ===
# ==========================================

# --- 1. ГОЛОВНЕ МЕНЮ НАЛАШТУВАНЬ ---
async def show_settings_main(message: types.Message, user_id, edit=False):
    """Головна сторінка налаштувань."""
    user = await db.get_user(user_id)
    if not user:
        if edit: await message.edit_text("⚠️ Спочатку оберіть регіон через /start")
        else: await message.answer("⚠️ Спочатку оберіть регіон через /start")
        return

    settings = await db.get_user_settings(user_id)
    
    if settings['display_mode'] == 'light':
        mode_status = "🟢 Показую, коли світло Є"
    else:
        mode_status = "⬛️ Показую, коли світла НЕМАЄ"
    
    # Відображаємо статус таймерів у меню (красиво)
    t_out = f"{settings['notify_before']} хв" if settings['notify_before'] > 0 else "Вимкнено"
    t_in = f"{settings['notify_return_before']} хв" if settings['notify_return_before'] > 0 else "Вимкнено"

    text = (
        f"⚙️ **Головні налаштування**\n"
        f"📍 Локація: **{user[0]}, Черга {user[1]}**\n\n"
        f"⏰ Таймер відключення: **{t_out}**\n"
        f"⏰ Таймер включення: **{t_in}**\n"
        f"🎨 Вигляд графіку: **{mode_status}**"
    )

    kb = InlineKeyboardBuilder()
    
    # Кнопки навігації (ієрархія)
    kb.button(text="⏰ Налаштувати таймери >", callback_data="menu_time_select")
    kb.button(text="🔔 Налаштування сповіщень >", callback_data="menu_types")
    kb.button(text="🎨 Вигляд графіку >", callback_data="menu_mode")
    kb.button(text="📍 Змінити область/чергу >", callback_data="open_regions")
    kb.button(text="❌ Закрити меню", callback_data="close_settings")

    kb.adjust(1) 

    if edit:
        await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    else:
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- 2. ПІДМЕНЮ: ВИБІР ТАЙМЕРА (НОВЕ) ---
async def show_time_type_selection(message: types.Message):
    """Меню вибору: який таймер налаштовуємо?"""
    text = "⏰ **Налаштування часу**\n\nЯкий таймер ви хочете змінити?"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔦 Відключення", callback_data="time_edit|outage")
    kb.button(text="💡 До включення", callback_data="time_edit|return")
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main"))
    
    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- 3. ПІДМЕНЮ: ВИБІР ХВИЛИН ---
async def show_minutes_menu(message: types.Message, user_id, timer_type):
    """Меню вибору хвилин для конкретного таймера."""
    settings = await db.get_user_settings(user_id)
    
    # Визначаємо, яку колонку редагуємо і який заголовок
    if timer_type == "outage":
        current = settings['notify_before']
        title = "🔦 **Попередження про ВІДКЛЮЧЕННЯ**"
    else:
        current = settings['notify_return_before']
        title = "💡 **Попередження про ВКЛЮЧЕННЯ**"

    text = (
        f"{title}\n\n"
        f"За скільки хвилин вас попередити?"
    )
    
    kb = InlineKeyboardBuilder()
    times = [5, 15, 30, 60]
    
    for t in times:
        mark = "✅" if current == t else ""
        label = "1 год" if t == 60 else f"{t} хв"
        # Передаємо тип таймера далі в callback
        kb.button(text=f"{mark} {label}", callback_data=f"set_time|{timer_type}|{t}")
    
    # Кнопка вимкнення (встановлює 0)
    mark_off = "✅" if current == 0 else ""
    kb.row(InlineKeyboardButton(text=f"{mark_off} 🔕 Не нагадувати", callback_data=f"set_time|{timer_type}|0"))
    
    kb.adjust(2, 2, 1) 
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_time_select"))
    
    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- 4. ПІДМЕНЮ: ТИПИ СПОВІЩЕНЬ ---
async def show_types_menu(message: types.Message, user_id):
    settings = await db.get_user_settings(user_id)
    
    text = (
        f"🔔 **Налаштування сповіщень**\n\n"
        f"Увімкніть або вимкніть повідомлення:"
    )
    
    kb = InlineKeyboardBuilder()
    
    # 1. Відключення
    icon_out = "✅" if settings['notify_outage'] else "❌"
    kb.button(text=f"{icon_out} Коли зникає світло", callback_data="toggle|notify_outage")
    
    # 2. Включення
    icon_ret = "✅" if settings['notify_return'] else "❌"
    kb.button(text=f"{icon_ret} Коли з'являється світло", callback_data="toggle|notify_return")
    
    # 3. Зміни
    icon_chg = "✅" if settings['notify_changes'] else "❌"
    kb.button(text=f"{icon_chg} Якщо змінився графік", callback_data="toggle|notify_changes")
    
    kb.adjust(1) 
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main"))
    
    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- 5. ПІДМЕНЮ: ВИГЛЯД ГРАФІКУ ---
async def show_mode_menu(message: types.Message, user_id):
    settings = await db.get_user_settings(user_id)
    current = settings['display_mode']
    
    text = (
        f"🎨 **Вигляд графіку**\n\n"
        f"Що показувати на картинці?"
    )
    
    kb = InlineKeyboardBuilder()
    
    mark_b = "✅" if current == "blackout" else ""
    kb.button(text=f"{mark_b} ⬛️ Коли світла НЕМАЄ", callback_data="set_mode|blackout")
    
    mark_l = "✅" if current == "light" else ""
    kb.button(text=f"{mark_l} 🟢 Коли світло Є", callback_data="set_mode|light")
    
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main"))
    
    await message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- ОБРОБНИКИ НАВІГАЦІЇ ТА ДІЙ ---

@router.callback_query(F.data == "menu_main")
async def nav_main(callback: types.CallbackQuery):
    await show_settings_main(callback.message, callback.from_user.id, edit=True)

@router.callback_query(F.data == "menu_time_select")
async def nav_time_select(callback: types.CallbackQuery):
    """Показує вибір типу таймера."""
    await show_time_type_selection(callback.message)

@router.callback_query(F.data.startswith("time_edit|"))
async def nav_time_edit(callback: types.CallbackQuery):
    """Показує вибір хвилин для конкретного типу."""
    timer_type = callback.data.split("|")[1] # outage або return
    await show_minutes_menu(callback.message, callback.from_user.id, timer_type)

@router.callback_query(F.data == "menu_types")
async def nav_types(callback: types.CallbackQuery):
    await show_types_menu(callback.message, callback.from_user.id)

@router.callback_query(F.data == "menu_mode")
async def nav_mode(callback: types.CallbackQuery):
    await show_mode_menu(callback.message, callback.from_user.id)

@router.callback_query(F.data.startswith("set_time|"))
async def set_notify_time(callback: types.CallbackQuery):
    """Встановлює час (універсальна функція)."""
    parts = callback.data.split("|")
    timer_type = parts[1] # outage або return
    minutes = int(parts[2])
    
    # Визначаємо, в яку колонку писати
    col_name = "notify_before" if timer_type == "outage" else "notify_return_before"
    
    await db.update_user_setting(callback.from_user.id, col_name, minutes)
    
    # Оновлюємо це ж меню, щоб показати нову галочку
    await show_minutes_menu(callback.message, callback.from_user.id, timer_type)

@router.callback_query(F.data.startswith("toggle|"))
async def toggle_setting(callback: types.CallbackQuery):
    key = callback.data.split("|")[1]
    settings = await db.get_user_settings(callback.from_user.id)
    new_val = 0 if settings[key] else 1
    await db.update_user_setting(callback.from_user.id, key, new_val)
    await show_types_menu(callback.message, callback.from_user.id)

@router.callback_query(F.data.startswith("set_mode|"))
async def set_display_mode(callback: types.CallbackQuery):
    new_mode = callback.data.split("|")[1]
    await db.update_user_setting(callback.from_user.id, "display_mode", new_mode)
    await show_mode_menu(callback.message, callback.from_user.id)

@router.callback_query(F.data == "open_regions")
async def open_regions_handler(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_regions_menu(callback.message, "👇 **Оберіть вашу область:**")

@router.callback_query(F.data == "close_settings")
async def close_settings_handler(callback: types.CallbackQuery):
    await callback.message.delete()


# ==========================================
# === ЛОГІКА ВИБОРУ РЕГІОНУ (СТАРА) ===
# ==========================================

async def show_regions_menu(message: types.Message, text):
    data = await api.fetch_api_data()
    if not data:
        await message.answer("⚠️ Помилка отримання даних.")
        return

    kb = InlineKeyboardBuilder()
    for region in data['regions']:
        kb.button(text=region['name_ua'], callback_data=f"reg|{region['name_ua']}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="🔕 Зупинити бота (Відписатися)", callback_data="unsub"))
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("reg|"))
async def select_region(callback: types.CallbackQuery):
    region_name = callback.data.split("|")[1]
    data = await api.fetch_api_data()
    kb = InlineKeyboardBuilder()
    for r in data['regions']:
        if r['name_ua'] == region_name:
            for q in sorted(r['schedule'].keys()):
                kb.button(text=f"Черга {q}", callback_data=f"q|{region_name}|{q}")
            break
    kb.adjust(3)
    await callback.message.edit_text(f"📍 **{region_name}**. Оберіть чергу:", reply_markup=kb.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("q|"))
async def select_queue(callback: types.CallbackQuery):
    _, region, queue = callback.data.split("|")
    await db.save_user(callback.from_user.id, region, queue)
    await callback.message.delete()
    await callback.message.answer(
        f"✅ Налаштування збережено!\n📍 {region}, Черга {queue}", 
        reply_markup=get_main_keyboard(callback.from_user.id)
    )
    
    # 1. Показуємо графік
    await show_today_schedule(callback.message, region, queue, user_id=callback.from_user.id)
    
    # 2. НОВА ФІЧА: Відправляємо підказку про налаштування
    await asyncio.sleep(0.5) 
    await callback.message.answer(
        "💡 **Маленька порада!**\n\n"
        "У меню **⚙️ Налаштування** ви можете:\n"
        "⏰ Змінити час сповіщення\n"
        "🎨 Вибрати «зелений» графік (коли світло є)\n"
        "🔔 Налаштувати повідомлення під себе",
        parse_mode="Markdown"
    )


async def show_today_schedule(message, region, queue, user_id=None):
    uid = user_id if user_id else message.from_user.id
    
    today = get_local_now().strftime('%Y-%m-%d')
    schedule = None
    
    settings = await db.get_user_settings(uid)
    display_mode = settings.get('display_mode', 'blackout')

    cached_data = scheduler.schedules_cache.get((region, queue))
    
    if cached_data is not None:
        schedule = cached_data.get("today")
    else:
        data = await api.fetch_api_data()
        if data:
            for r in data['regions']:
                if r['name_ua'] == region:
                    schedule = r['schedule'].get(queue, {}).get(today)
                    break
    
    if schedule:
        await db.save_stats(region, queue, today, api.calculate_off_hours(schedule))
    
    text = api.format_message(schedule, queue, today, is_tomorrow=False, display_mode=display_mode)
    
    # Якщо це група, додаємо згадку користувача, щоб він знав, що це ЙОГО графік
    if message.chat.type in ['group', 'supergroup']:
        user_name = message.from_user.first_name
        text = f"👤 **{user_name}**, твій графік:\n" + text

    await message.answer(text, parse_mode="Markdown")


# --- КНОПКИ МЕНЮ ---

@router.message(F.text == "⚙️ Налаштування")
async def btn_settings(message: types.Message):
    # ВІДКРИВАЄ НОВЕ ГОЛОВНЕ МЕНЮ
    await show_settings_main(message, message.from_user.id)

@router.message(F.text == "📅 Графік на сьогодні")
async def btn_today(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: 
        return await message.answer("Спочатку зробіть налаштування.")
    await show_today_schedule(message, user[0], user[1], user_id=message.from_user.id)

@router.message(F.text == "🔮 Графік на завтра")
async def btn_tomorrow(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: 
        return await message.answer("Спочатку налаштування.")
    
    settings = await db.get_user_settings(message.from_user.id)
    display_mode = settings.get('display_mode', 'blackout')

    tomorrow = (get_local_now() + timedelta(days=1)).strftime('%Y-%m-%d')
    
    schedule = None
    
    cached_data = scheduler.schedules_cache.get((user[0], user[1]))
    
    if cached_data is not None:
        schedule = cached_data.get("tomorrow")
    else:
        data = await api.fetch_api_data()
        if data:
            for r in data['regions']:
                if r['name_ua'] == user[0]:
                    schedule = r['schedule'].get(user[1], {}).get(tomorrow, None)
                    break
                    
    if schedule:
        await db.save_stats(user[0], user[1], tomorrow, api.calculate_off_hours(schedule))
    
    text = api.format_message(schedule, user[1], tomorrow, is_tomorrow=True, display_mode=display_mode)
    
    if message.chat.type in ['group', 'supergroup']:
        user_name = message.from_user.first_name
        text = f"👤 **{user_name}**, твій графік:\n" + text
        
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "📊 Аналітика")
async def btn_stats(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: 
        if message.chat.type in ['group', 'supergroup']:
             await message.answer("Налаштуйте бота в особистих повідомленнях.")
        return

    api_data = await api.fetch_api_data()

    total = 0
    lines = []

    current_date = get_local_now()

    for i in range(6, -1, -1):
        d = current_date - timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')

        val = await db.get_off_hours_for_date(user[0], user[1], d_str)
        if val is None and api_data:
            schedule = None
            for r in api_data['regions']:
                if r['name_ua'] == user[0]:
                    schedule = r['schedule'].get(user[1], {}).get(d_str)
                    break
            if schedule:
                val = api.calculate_off_hours(schedule)
                await db.save_stats(user[0], user[1], d_str, val)
            else:
                val = 0
        elif val is None:
            val = 0

        total += val

        val_str = f"{int(val)}" if val == int(val) else f"{val:.1f}"
        d_nice = d.strftime('%d.%m')

        lines.append(f"▫️ {d_nice}:  **{val_str} год.**")

    total_str = f"{int(total)}" if total == int(total) else f"{total:.1f}"

    text = (
        f"📊 **Статистика відключень (останні 7 днів)**\n"
        f"📍 {user[0]}, Черга {user[1]}\n\n" +
        "\n".join(lines) +
        f"\n──────────────────\n"
        f"⚡️ Загалом: **{total_str} год.**"
    )
    await message.answer(text, parse_mode="Markdown")

@router.callback_query(F.data == "unsub")
async def unsub_handler(callback: types.CallbackQuery):
    await db.delete_user(callback.from_user.id)
    await callback.message.edit_text("🔕 **Ви успішно відписалися.**", parse_mode="Markdown")


# ========== СИСТЕМА ПІДТРИМКИ ==========

@router.message(F.text == "💬 Підтримка")
async def btn_support(message: types.Message):
    if message.chat.type in ['group', 'supergroup']:
        await message.answer("💬 Пишіть у підтримку в особисті повідомлення боту.")
        return

    await message.answer(
        "💬 **Служба підтримки**\n\n"
        "Напишіть ваше повідомлення, і адміністратор відповість вам найближчим часом.", 
        parse_mode="Markdown"
    )
    await db.set_user_mode(message.from_user.id, "support")


@router.callback_query(F.data.startswith("user_reply|"))
async def user_reply_click(callback: types.CallbackQuery):
    ticket_id = callback.data.split("|")[1]
    
    ticket_info = await db.get_ticket_info(int(ticket_id))
    if not ticket_info:
        await callback.answer("❌ Помилка: тікет не знайдено", show_alert=True)
        return
    
    await db.set_user_mode(callback.from_user.id, f"user_replying:{ticket_id}")
    await callback.message.answer("✍️ **Напишіть вашу відповідь:**", parse_mode="Markdown")
    await callback.answer()


# ========== АДМІН-ПАНЕЛЬ ==========

@router.message(F.text == "👨‍💼 Адмін-панель")
@router.message(Command("admin"))
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    
    unread_count = await db.get_unread_count()
    
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📨 Розсилка всім"))
    
    support_text = f"📋 Підтримка"
    if unread_count > 0:
        support_text += f" ({unread_count})"
    
    kb.row(KeyboardButton(text=support_text), KeyboardButton(text="👥 Користувачів"))
    kb.row(KeyboardButton(text="🏠 Меню"))
    
    await message.answer(
        "👨‍💼 **Панель адміністратора**", 
        reply_markup=kb.as_markup(resize_keyboard=True), 
        parse_mode="Markdown"
    )


@router.message(F.text == "📨 Розсилка всім")
async def broadcast_start(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    await message.answer("📨 **Розсилка всім**\nНапишіть текст повідомлення (максимум 4000 символів):")
    await db.set_user_mode(ADMIN_ID, "broadcast")


@router.message(F.text.startswith("📋 Підтримка"))
async def support_tickets_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔔 Непрочитані", callback_data="tickets|unread")
    kb.button(text="📋 Всі звернення", callback_data="tickets|all")
    kb.adjust(2)
    
    unread_count = await db.get_unread_count()
    
    text = f"📋 **Служба підтримки**\n\n📌 Непрочитані: **{unread_count}**"
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("tickets|"))
async def show_tickets_list(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_type = callback.data.split("|")[1]
    
    if ticket_type == "unread":
        tickets = await db.get_unread_tickets()
        title = "🔔 Непрочитані звернення"
    else:
        tickets = await db.get_all_tickets()
        title = "📋 Всі звернення"
    
    if not tickets:
        await callback.message.edit_text(f"{title}\n\n✅ Немає звернень", parse_mode="Markdown")
        return
    
    kb = InlineKeyboardBuilder()
    
    for ticket in tickets:
        ticket_id, user_id, username, *rest = ticket
        display_name = f"@{username}" if username else f"ID: {user_id}"

        if ticket_type == "all":
            status = rest[0]
            status_icon = "🔴" if status == "unread" else "✅" if status == "read" else "🔒"
            button_text = f"{status_icon} {display_name}"
        else:
            button_text = f"🔴 {display_name}"
        
        kb.button(text=button_text, callback_data=f"viewticket|{ticket_id}")
    
    kb.adjust(1)
    await callback.message.edit_text(f"{title}:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("viewticket|"))
async def view_ticket(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    
    ticket_info = await db.get_ticket_info(ticket_id)
    if not ticket_info:
        await callback.message.edit_text("❌ Тікет не знайдено")
        return
    
    user_id, username, status = ticket_info
    
    messages = await db.get_ticket_messages(ticket_id)
    
    text = f"💬 **Звернення #{ticket_id}**\n"
    text += f"👤 @{username or 'Unknown'} (ID: {user_id})\n"
    text += f"📊 Статус: {status}\n"
    text += "─────────────────\n\n"
    
    for from_user, msg_text, created_at in messages:
        icon = "👤" if from_user == "user" else "👨‍💼"
        display_text = msg_text[:200] + "..." if len(msg_text) > 200 else msg_text
        text += f"{icon} **{from_user}**: {display_text}\n⏰ {created_at}\n\n"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Відповісти", callback_data=f"reply|{ticket_id}")
    if status != "closed":
        kb.button(text="🔒 Закрити", callback_data=f"close|{ticket_id}")
    else:
        kb.button(text="🔓 Відкрити знову", callback_data=f"reopen|{ticket_id}")
    kb.button(text="◀️ Назад", callback_data="tickets|unread")
    kb.adjust(2, 2, 1)
    
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("reply|"))
async def admin_reply_click(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = callback.data.split("|")[1]
    await db.set_user_mode(ADMIN_ID, f"replying:{ticket_id}")
    await callback.message.answer(f"✍️ **Введіть відповідь для тікету #{ticket_id}:**", parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("close|"))
async def close_ticket_handler(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    await db.close_ticket(ticket_id)
    await callback.answer("✅ Тікет закрито", show_alert=True)
    await view_ticket(callback)


@router.callback_query(F.data.startswith("reopen|"))
async def reopen_ticket_handler(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    await db.reopen_ticket(ticket_id)
    await callback.answer("✅ Тікет відкрито знову", show_alert=True)
    await view_ticket(callback)


@router.message(F.text == "👥 Користувачів")
async def users_count(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    count = await db.get_users_count()
    await message.answer(f"👥 **Всього користувачів:** {count}", parse_mode="Markdown")


@router.message(F.text == "🏠 Меню")
async def back_to_main(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    await message.answer("🏠 **Головне меню.**", reply_markup=get_main_keyboard(ADMIN_ID), parse_mode="Markdown")
    await db.set_user_mode(ADMIN_ID, "normal")


# ========== ОБРОБКА ТЕКСТОВИХ ПОВІДОМЛЕНЬ ==========

@router.message(F.text)
async def handle_text_messages(message: types.Message):
    user_id = message.from_user.id
    mode = await db.get_user_mode(user_id)

    # 1. АДМІН: РОЗСИЛКА
    if user_id == ADMIN_ID and mode == "broadcast":
        if len(message.text) > 4000:
            await message.answer("❌ **Повідомлення занадто довге!**", parse_mode="Markdown")
            return
        
        users = await db.get_all_users_for_broadcast()
        sent, failed = 0, 0
        if users:
            await message.answer(f"📤 Відправка {len(users)} користувачам...")
            for (uid,) in users:
                try:
                    await message.bot.send_message(uid, f"📢 **Сповіщення:**\n\n{message.text}", parse_mode="Markdown")
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    failed += 1
            
            await message.answer(f"✅ **Розсилка завершена!**\n✓ {sent} / ✗ {failed}", parse_mode="Markdown")
        else:
            await message.answer("❌ Немає користувачів.")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 2. АДМІН: ВІДПОВІДЬ НА ТІКЕТ
    if user_id == ADMIN_ID and mode.startswith("replying:"):
        ticket_id = int(mode.split(":")[1])
        
        if len(message.text) > 3000:
            await message.answer("❌ **Повідомлення занадто довге!**", parse_mode="Markdown")
            return
        
        ticket_info = await db.get_ticket_info(ticket_id)
        if not ticket_info:
            await message.answer("❌ Тікет не знайдено")
            await db.set_user_mode(ADMIN_ID, "normal")
            return
        
        target_user_id, username, status = ticket_info
        
        await db.save_support_message(ticket_id, "admin", message.text)
        await db.mark_ticket_read(ticket_id)
        
        try:
            kb = InlineKeyboardBuilder()
            kb.button(text="✍️ Відповісти", callback_data=f"user_reply|{ticket_id}")
            
            await message.bot.send_message(
                target_user_id,
                f"📞 **Служба підтримки:**\n\n{message.text}",
                reply_markup=kb.as_markup(),
                parse_mode="Markdown"
            )
            await message.answer("✅ Відповідь надіслано!")
        except Exception as e:
            await message.answer(f"❌ Не вдалося надіслати: {e}")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 3. КОРИСТУВАЧ: ПІДТРИМКА
    if mode == "support":
        if message.chat.type in ['group', 'supergroup']:
            return 
            
        if len(message.text) > 3000:
            await message.answer("❌ **Повідомлення занадто довге!**", parse_mode="Markdown")
            return
        
        username = message.from_user.username or "Unknown"
        ticket_id = await db.create_or_get_ticket(user_id, username)
        
        await db.save_support_message(ticket_id, "user", message.text)
        
        try:
            kb = InlineKeyboardBuilder()
            kb.button(text="✍️ Відповісти", callback_data=f"reply|{ticket_id}")
            kb.button(text="📋 Переглянути", callback_data=f"viewticket|{ticket_id}")
            
            display_text = message.text[:500] + "..." if len(message.text) > 500 else message.text
            
            # === ФІКС: ПРИБРАНО parse_mode ДЛЯ АДМІНА ===
            await message.bot.send_message(
                ADMIN_ID,
                f"🔔 Нове повідомлення в тікеті #{ticket_id}\n"
                f"👤 @{username} (ID: {user_id})\n\n"
                f"💬 {display_text}",
                reply_markup=kb.as_markup()
            )
            await message.answer("✅ Повідомлення відправлено! Адміністратор відповість найближчим часом.")
        except Exception as e:
            print(f"Помилка відправки адміну: {e}")
            await message.answer("✅ Повідомлення збережено!")
        
        await db.set_user_mode(user_id, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(user_id))
        return

    # 4. КОРИСТУВАЧ: ВІДПОВІДЬ
    if mode.startswith("user_replying:"):
        ticket_id = int(mode.split(":")[1])
        username = message.from_user.username or "Unknown"
        
        if len(message.text) > 3000:
            await message.answer("❌ **Занадто довге!**", parse_mode="Markdown")
            return
        
        await db.save_support_message(ticket_id, "user", message.text)
        await db.reopen_ticket(ticket_id)
        
        try:
            kb = InlineKeyboardBuilder()
            kb.button(text="✍️ Відповісти", callback_data=f"reply|{ticket_id}")
            kb.button(text="📋 Переглянути", callback_data=f"viewticket|{ticket_id}")
            
            display_text = message.text[:500] + "..." if len(message.text) > 500 else message.text
            
            # === ФІКС: ПРИБРАНО parse_mode ДЛЯ АДМІНА ===
            await message.bot.send_message(
                ADMIN_ID,
                f"🔔 Нова відповідь в тікеті #{ticket_id}\n"
                f"👤 @{username} (ID: {user_id})\n\n"
                f"💬 {display_text}",
                reply_markup=kb.as_markup()
            )
            await message.answer("✅ Відповідь відправлена!")
        except Exception as e:
            print(f"Помилка: {e}")
            await message.answer("✅ Відповідь збережена!")
        
        await db.set_user_mode(user_id, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(user_id))
        return

    await message.answer("❓ Не розумію вашу команду. Використовуйте кнопки меню.")