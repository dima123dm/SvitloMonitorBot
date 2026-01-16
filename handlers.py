# handlers.py
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, InlineKeyboardButton

import database as db
import api_utils as api
import scheduler  # <--- Імпорт для доступу до кешу

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
async def start_command(message: types.Message):
    """Команда /start."""
    user = await db.get_user(message.from_user.id)
    if user:
        await message.answer(
            f"👋 **Ласкаво просимо назад!**\n📍 Ваш вибір: **{user[0]}, Черга {user[1]}**",
            reply_markup=get_main_keyboard(message.from_user.id),
            parse_mode="Markdown"
        )
        return
    
    text = (
        "👋 **Вітаю! Це бот Моніторингу Світла.**\n"
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
    await show_today_schedule(callback.message, region, queue)


async def show_today_schedule(message, region, queue):
    today = get_local_now().strftime('%Y-%m-%d')
    schedule = None
    
    # --- ОПТИМІЗАЦІЯ (SMART CACHE) ---
    # Перевіряємо, чи є запис у кеші
    cached_data = scheduler.schedules_cache.get((region, queue))
    
    if cached_data is not None:
        # Якщо запис є - беремо з нього (навіть якщо там None)
        # Ми НЕ йдемо до API, бо кеш знає, що графіка немає.
        schedule = cached_data.get("today")
    else:
        # Кеш порожній (бот тільки запустився) - йдемо до API
        data = await api.fetch_api_data()
        if data:
            for r in data['regions']:
                if r['name_ua'] == region:
                    schedule = r['schedule'].get(queue, {}).get(today)
                    break
    
    if schedule:
        await db.save_stats(region, queue, today, api.calculate_off_hours(schedule))
    text = api.format_message(schedule, queue, today, is_tomorrow=False)
    await message.answer(text, parse_mode="Markdown")


# --- КНОПКИ МЕНЮ ---

@router.message(F.text == "⚙️ Налаштування")
async def btn_settings(message: types.Message):
    await show_regions_menu(message, "⚙️ **Налаштування**\nОберіть область:")

@router.message(F.text == "📅 Графік на сьогодні")
async def btn_today(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: return await message.answer("Спочатку зробіть налаштування.")
    await show_today_schedule(message, user[0], user[1])

@router.message(F.text == "🔮 Графік на завтра")
async def btn_tomorrow(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: return await message.answer("Спочатку налаштування.")
    tomorrow = (get_local_now() + timedelta(days=1)).strftime('%Y-%m-%d')
    
    schedule = None
    
    # --- ОПТИМІЗАЦІЯ (SMART CACHE) ---
    cached_data = scheduler.schedules_cache.get((user[0], user[1]))
    
    if cached_data is not None:
        # Якщо кеш існує - довіряємо йому на 100%
        # Якщо там None, значить API ще не дав графік, і ми не спамимо запитами.
        schedule = cached_data.get("tomorrow")
    else:
        # Тільки якщо бот після рестарту і кеш пустий
        data = await api.fetch_api_data()
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

    # Отримуємо дані з API для заповнення пропусків
    api_data = await api.fetch_api_data()

    total = 0
    lines = []

    # Генеруємо список останніх 7 днів вручну
    current_date = get_local_now()

    # Цикл: 6, 5, 4, 3, 2, 1, 0 (днів тому)
    for i in range(6, -1, -1):
        d = current_date - timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')

        # Спочатку перевіряємо в БД
        val = await db.get_off_hours_for_date(user[0], user[1], d_str)
        if val is None and api_data:
            # Якщо немає в БД, пробуємо отримати з API
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
        f"📊 **Статистика (останні 7 днів)**\n"
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

@router.message(F.text == "💬 Підтримка")
async def btn_support(message: types.Message):
    await message.answer("💬 **Служба підтримки**\nНапишіть ваше повідомлення, і адміністратор відповість вам.", parse_mode="Markdown")
    await db.set_user_mode(message.from_user.id, "support")


# ========== АДМІН-ПАНЕЛЬ ==========

# Цей хендлер ловить і команду /admin, і текст кнопки
@router.message(F.text == "👨‍💼 Адмін-панель")
@router.message(Command("admin"))
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📨 Розсилка всім"))
    kb.row(KeyboardButton(text="📋 Підтримка"), KeyboardButton(text="👥 Користувачів"))
    kb.row(KeyboardButton(text="🏠 Меню"))
    
    await message.answer("👨‍💼 **Панель адміністратора**", reply_markup=kb.as_markup(resize_keyboard=True), parse_mode="Markdown")

@router.message(F.text == "📨 Розсилка всім")
async def broadcast_start(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("📨 **Розсилка всім**\nНапишіть текст повідомлення:")
    await db.set_user_mode(ADMIN_ID, "broadcast")

@router.message(F.text == "📋 Підтримка")
async def support_messages_list(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    messages = await db.get_all_support_messages()
    if not messages: return await message.answer("📋 **Немає повідомлень.**")
    text = "📋 **Останні повідомлення:**\n\n"
    for msg in messages[:5]:
        text += (f"👤 @{msg[2]} (ID: {msg[1]})\n💬 {msg[3]}\n⏰ {msg[4]}\n─────────────────\n")
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "👥 Користувачів")
async def users_count(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    count = await db.get_users_count()
    await message.answer(f"👥 **Всього користувачів:** {count}", parse_mode="Markdown")

@router.message(F.text == "🏠 Меню")
async def back_to_main(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    # Повертаємо клавіатуру, передаючи ID, щоб кнопка адміна не зникла
    await message.answer("🏠 **Головне меню.**", reply_markup=get_main_keyboard(ADMIN_ID), parse_mode="Markdown")
    await db.set_user_mode(ADMIN_ID, "normal")


# ========== ОБРОБКА ВІДПОВІДІ АДМІНА (КНОПКА) ==========

@router.callback_query(F.data.startswith("reply_user|"))
async def admin_reply_click(callback: types.CallbackQuery):
    """Адмін натиснув кнопку 'Відповісти' під повідомленням."""
    if callback.from_user.id != ADMIN_ID: return

    user_id = callback.data.split("|")[1]
    # Встановлюємо спец-режим для адміна: "replyING:12345"
    await db.set_user_mode(ADMIN_ID, f"replyING:{user_id}")
    await callback.message.answer(f"✍️ **Введіть відповідь для користувача (ID {user_id}):**")
    await callback.answer()


# ========== ЄДИНИЙ ОБРОБНИК ТЕКСТУ ==========

@router.message(F.text)
async def handle_text_messages(message: types.Message):
    user_id = message.from_user.id
    mode = await db.get_user_mode(user_id)

    # 1. АДМІН: РОЗСИЛКА
    if user_id == ADMIN_ID and mode == "broadcast":
        users = await db.get_all_users_for_broadcast()
        sent, failed = 0, 0
        if users:
            await message.answer(f"📤 Відправка {len(users)} користувачам...")
            for (uid,) in users:
                try:
                    await message.bot.send_message(uid, f"📢 **Сповіщення:**\n\n{message.text}", parse_mode="Markdown")
                    sent += 1
                except: failed += 1
            await message.answer(f"✅ **Розсилка завершена!**\n✓ {sent} / ✗ {failed}", parse_mode="Markdown")
        else:
            await message.answer("❌ Немає користувачів.")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 2. АДМІН: ВІДПОВІДЬ (режим replyING)
    if user_id == ADMIN_ID and mode.startswith("replyING:"):
        target_user_id = mode.split(":")[1]
        try:
            await message.bot.send_message(
                target_user_id, 
                f"📞 **Служба підтримки:**\n\n{message.text}", 
                parse_mode="Markdown"
            )
            await message.answer(f"✅ Відповідь надіслано користувачу {target_user_id}!")
        except Exception as e:
            await message.answer(f"❌ Не вдалося надіслати: {e}")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 3. КОРИСТУВАЧ: ПІДТРИМКА
    if mode == "support":
        await db.save_support_message(user_id, message.from_user.username or "Unknown", message.text)
        
        # Створюємо кнопку для відповіді
        kb = InlineKeyboardBuilder()
        kb.button(text="↩️ Відповісти", callback_data=f"reply_user|{user_id}")
        
        try:
            await message.bot.send_message(
                ADMIN_ID,
                f"💬 **Нове повідомлення!**\n👤 @{message.from_user.username} (ID: {user_id})\n\n{message.text}",
                reply_markup=kb.as_markup(),
                parse_mode="Markdown"
            )
            await message.answer("✅ Повідомлення відправлено!")
        except:
            await message.answer("❌ Помилка відправки (можливо, адмін не запустив бота).")
        
        await db.set_user_mode(user_id, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(user_id))
        return