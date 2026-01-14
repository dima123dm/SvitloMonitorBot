# handlers.py
from datetime import datetime, timedelta, timezone
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton, InlineKeyboardButton

import database as db
import api_utils as api

ADMIN_ID = 723550550  # ID адміна @dima123dm

router = Router()

# --- ДОПОМІЖНА ФУНКЦІЯ ЧАСУ ---
def get_local_now():
    """Повертає поточний час (UTC+2/UTC+3). 
    Використовуємо це, щоб уникнути проблем, якщо сервер в UTC."""
    # Якщо сервер налаштований правильно - достатньо datetime.now()
    # Якщо сервер в UTC, додаємо 2 години (або 3 літом) вручну, або використовуємо pytz.
    # Тут базовий варіант: беремо системний час. 
    # ПЕРЕВІРТЕ ЧАС НА СЕРВЕРІ командою: date
    return datetime.now()

def get_main_keyboard():
    """Створює нижнє меню з кнопками."""
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📅 Графік на сьогодні"), KeyboardButton(text="🔮 Графік на завтра"))
    kb.row(KeyboardButton(text="📊 Аналітика"), KeyboardButton(text="⚙️ Налаштування"))
    kb.row(KeyboardButton(text="💬 Підтримка"))
    return kb.as_markup(resize_keyboard=True)


@router.message(Command("start"))
async def start_command(message: types.Message):
    """Команда /start - вітання та початок налаштування."""
    user = await db.get_user(message.from_user.id)
    
    # Якщо користувач вже обрав регіон, показуємо меню одразу
    if user:
        await message.answer(
            "👋 **Ласкаво просимо назад!**\n\n"
            f"📍 Ваш вибір: **{user[0]}, Черга {user[1]}**",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    # Якщо новий користувач, пропонуємо вибрати область
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
    today = get_local_now().strftime('%Y-%m-%d')
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

    tomorrow = (get_local_now() + timedelta(days=1)).strftime('%Y-%m-%d')
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

    # Дані вже сортовані за датою (ASC) з БД
    total = 0
    lines = []
    
    # Фільтрація майбутніх дат (якщо серверний час "полетів" вперед)
    current_date = get_local_now().strftime('%Y-%m-%d')

    for r in rows:
        r_date = r[0]
        # Показуємо тільки якщо дата <= сьогодні (хоча SQL запит це вже робить, це перестраховка)
        if r_date > current_date:
            continue

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


@router.message(F.text == "💬 Підтримка")
async def btn_support(message: types.Message):
    """Користувач натиснув кнопку Підтримка."""
    await message.answer(
        "💬 **Служба підтримки**\n\n"
        "Напишіть ваше повідомлення для підтримки, і адміністратор відповість вам якомога швидше.\n"
        "Зверху бачитимете ваш нік, щоб адміністратор міг вас знайти.",
        parse_mode="Markdown"
    )
    await db.set_user_mode(message.from_user.id, "support")


# ========== КОМАНДИ ДЛЯ АДМІНІСТРАТОРА ==========

@router.message(Command("admin"))
async def admin_menu(message: types.Message):
    """Панель адміністратора."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас немає доступу до цієї команди.")
        return
    
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📨 Розсилка всім"))
    kb.row(KeyboardButton(text="📋 Підтримка"), KeyboardButton(text="👥 Користувачів"))
    kb.row(KeyboardButton(text="🏠 Меню"))
    
    await message.answer(
        "👨‍💼 **Панель адміністратора**\n\n"
        "Виберіть дію:",
        reply_markup=kb.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )


@router.message(F.text == "📨 Розсилка всім")
async def broadcast_start(message: types.Message):
    """Адміністратор починає розсилку."""
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "📨 **Розсилка всім користувачам**\n\n"
        "Напишіть текст повідомлення, яке хочете відправити всім користувачам:"
    )
    await db.set_user_mode(ADMIN_ID, "broadcast")


@router.message(F.text == "📋 Підтримка")
async def support_messages_list(message: types.Message):
    """Показує всі повідомлення підтримки."""
    if message.from_user.id != ADMIN_ID:
        return
    
    messages = await db.get_all_support_messages()
    
    if not messages:
        await message.answer("📋 **Немає повідомлень підтримки.**")
        return
    
    # Показуємо по 5 повідомлень
    text = "📋 **Останні повідомлення підтримки:**\n\n"
    for msg in messages[:5]:
        msg_id, user_id, username, text_msg, timestamp = msg
        text += (
            f"👤 @{username} (ID: {user_id})\n"
            f"💬 {text_msg}\n"
            f"⏰ {timestamp}\n"
            f"─────────────────\n"
        )
    
    await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "👥 Користувачів")
async def users_count(message: types.Message):
    """Показує кількість користувачів."""
    if message.from_user.id != ADMIN_ID:
        return
    
    count = await db.get_users_count()
    
    await message.answer(
        f"👥 **Статистика користувачів**\n\n"
        f"📊 Всього користувачів: **{count}**",
        parse_mode="Markdown"
    )


@router.message(F.text == "🏠 Меню")
async def back_to_main(message: types.Message):
    """Повернення на головне меню."""
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "🏠 **Ви повернулися на головне меню.**",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    await db.set_user_mode(ADMIN_ID, "normal")


# ========== ЄДИНИЙ ОБРОБНИК ТЕКСТУ (РОЗСИЛКА + ПІДТРИМКА) ==========

@router.message(F.text)
async def handle_text_messages(message: types.Message):
    """
    Цей хендлер ловить ВСІ текстові повідомлення, які не потрапили в кнопки вище.
    Тут обробляємо:
    1. Розсилку від адміна.
    2. Повідомлення в підтримку від користувача.
    """
    user_id = message.from_user.id
    mode = await db.get_user_mode(user_id)

    # --- 1. ЛОГІКА АДМІНА (РОЗСИЛКА) ---
    if user_id == ADMIN_ID and mode == "broadcast":
        users = await db.get_all_users_for_broadcast()
        
        if not users:
            await message.answer("❌ Немає користувачів для розсилки.")
        else:
            await message.answer(f"📤 Відправка {len(users)} користувачам...")
            sent_count = 0
            failed_count = 0
            
            for (uid,) in users:
                try:
                    await message.bot.send_message(
                        uid,
                        f"📢 **Сповіщення:**\n\n{message.text}",
                        parse_mode="Markdown"
                    )
                    sent_count += 1
                except:
                    failed_count += 1
            
            await message.answer(
                f"✅ **Розсилка завершена!**\n"
                f"✓ Відправлено: {sent_count}\n"
                f"✗ Помилок: {failed_count}",
                parse_mode="Markdown"
            )
        
        # Повертаємо адміна в звичайний режим
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("", reply_markup=get_main_keyboard())
        return

    # --- 2. ЛОГІКА КОРИСТУВАЧА (ПІДТРИМКА) ---
    if mode == "support":
        # Зберігаємо в БД
        await db.save_support_message(
            user_id=user_id,
            username=message.from_user.username or f"ID{user_id}",
            text=message.text
        )
        
        # Відправляємо адміну
        try:
            await message.bot.send_message(
                ADMIN_ID,
                f"💬 **Нове повідомлення підтримки!**\n"
                f"👤: @{message.from_user.username or 'NoNick'} (ID: {user_id})\n\n"
                f"{message.text}",
                parse_mode="Markdown"
            )
            await message.answer("✅ Ваше повідомлення відправлено адміністратору!")
        except Exception as e:
            print(f"Failed to send support msg to admin: {e}")
            await message.answer("❌ Сталася помилка при відправці, спробуйте пізніше.")

        # Повертаємо користувача в нормальний режим
        await db.set_user_mode(user_id, "normal")
        await message.answer("", reply_markup=get_main_keyboard())
        return

    # Якщо текст не підходить ні під одну команду
    # Можна нічого не відповідати або сказати "Користуйтеся меню"
    # await message.answer("ℹ️ Будь ласка, скористайтеся кнопками меню.")