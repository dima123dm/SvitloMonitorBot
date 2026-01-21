# handlers.py
from datetime import datetime, timedelta
import asyncio
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
    if not user: 
        return await message.answer("Спочатку зробіть налаштування.")
    await show_today_schedule(message, user[0], user[1])

@router.message(F.text == "🔮 Графік на завтра")
async def btn_tomorrow(message: types.Message):
    user = await db.get_user(message.from_user.id)
    if not user: 
        return await message.answer("Спочатку налаштування.")
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
    if not user: 
        return

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


# ========== СИСТЕМА ПІДТРИМКИ ==========

@router.message(F.text == "💬 Підтримка")
async def btn_support(message: types.Message):
    """Кнопка підтримки - переводить користувача в режим відправки повідомлення."""
    await message.answer(
        "💬 **Служба підтримки**\n\n"
        "Напишіть ваше повідомлення, і адміністратор відповість вам найближчим часом.", 
        parse_mode="Markdown"
    )
    await db.set_user_mode(message.from_user.id, "support")


@router.callback_query(F.data.startswith("user_reply|"))
async def user_reply_click(callback: types.CallbackQuery):
    """Користувач натиснув кнопку 'Відповісти' під повідомленням адміна."""
    ticket_id = callback.data.split("|")[1]
    
    # Перевіряємо чи існує тікет
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
    """Відкриває адмін-панель."""
    if message.from_user.id != ADMIN_ID: 
        return
    
    unread_count = await db.get_unread_count()
    
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="📨 Розсилка всім"))
    
    # Показуємо кількість непрочитаних
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
    """Починає процес розсилки."""
    if message.from_user.id != ADMIN_ID: 
        return
    await message.answer("📨 **Розсилка всім**\nНапишіть текст повідомлення (максимум 4000 символів):")
    await db.set_user_mode(ADMIN_ID, "broadcast")


@router.message(F.text.startswith("📋 Підтримка"))
async def support_tickets_menu(message: types.Message):
    """Відкриває меню тікетів підтримки."""
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
    """Показує список тікетів."""
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
        
        if ticket_type == "all":
            status = rest[0]
            status_icon = "🔴" if status == "unread" else "✅" if status == "read" else "🔒"
            button_text = f"{status_icon} {username or 'User'} (ID: {user_id})"
        else:
            button_text = f"🔴 {username or 'User'} (ID: {user_id})"
        
        kb.button(text=button_text, callback_data=f"viewticket|{ticket_id}")
    
    kb.adjust(1)
    await callback.message.edit_text(f"{title}:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("viewticket|"))
async def view_ticket(callback: types.CallbackQuery):
    """Показує деталі тікету."""
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    
    # Позначаємо як прочитане
    await db.mark_ticket_read(ticket_id)
    
    # Отримуємо інфо про тікет
    ticket_info = await db.get_ticket_info(ticket_id)
    if not ticket_info:
        await callback.message.edit_text("❌ Тікет не знайдено")
        return
    
    user_id, username, status = ticket_info
    
    # Отримуємо всі повідомлення
    messages = await db.get_ticket_messages(ticket_id)
    
    text = f"💬 **Звернення #{ticket_id}**\n"
    text += f"👤 {username or 'Unknown'} (ID: {user_id})\n"
    text += f"📊 Статус: {status}\n"
    text += "─────────────────\n\n"
    
    for from_user, msg_text, created_at in messages:
        icon = "👤" if from_user == "user" else "👨‍💼"
        # Обрізаємо довгі повідомлення
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
    """Адмін натискає кнопку відповіді."""
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = callback.data.split("|")[1]
    await db.set_user_mode(ADMIN_ID, f"replying:{ticket_id}")
    await callback.message.answer(f"✍️ **Введіть відповідь для тікету #{ticket_id}:**", parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("close|"))
async def close_ticket_handler(callback: types.CallbackQuery):
    """Закриває тікет."""
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    await db.close_ticket(ticket_id)
    await callback.answer("✅ Тікет закрито", show_alert=True)
    
    # Оновлюємо відображення
    await view_ticket(callback)


@router.callback_query(F.data.startswith("reopen|"))
async def reopen_ticket_handler(callback: types.CallbackQuery):
    """Відкриває тікет знову."""
    if callback.from_user.id != ADMIN_ID: 
        return
    
    ticket_id = int(callback.data.split("|")[1])
    await db.reopen_ticket(ticket_id)
    await callback.answer("✅ Тікет відкрито знову", show_alert=True)
    
    # Оновлюємо відображення
    await view_ticket(callback)


@router.message(F.text == "👥 Користувачів")
async def users_count(message: types.Message):
    """Показує кількість користувачів."""
    if message.from_user.id != ADMIN_ID: 
        return
    count = await db.get_users_count()
    await message.answer(f"👥 **Всього користувачів:** {count}", parse_mode="Markdown")


@router.message(F.text == "🏠 Меню")
async def back_to_main(message: types.Message):
    """Повертається в головне меню."""
    if message.from_user.id != ADMIN_ID: 
        return
    await message.answer("🏠 **Головне меню.**", reply_markup=get_main_keyboard(ADMIN_ID), parse_mode="Markdown")
    await db.set_user_mode(ADMIN_ID, "normal")


# ========== ОБРОБКА ТЕКСТОВИХ ПОВІДОМЛЕНЬ ==========

@router.message(F.text)
async def handle_text_messages(message: types.Message):
    """Єдиний обробник всіх текстових повідомлень залежно від режиму користувача."""
    user_id = message.from_user.id
    mode = await db.get_user_mode(user_id)

    # 1. АДМІН: РОЗСИЛКА
    if user_id == ADMIN_ID and mode == "broadcast":
        # Перевірка довжини повідомлення
        if len(message.text) > 4000:
            await message.answer("❌ **Повідомлення занадто довге!** Максимум 4000 символів.", parse_mode="Markdown")
            return
        
        users = await db.get_all_users_for_broadcast()
        sent, failed = 0, 0
        if users:
            await message.answer(f"📤 Відправка {len(users)} користувачам...")
            for (uid,) in users:
                try:
                    await message.bot.send_message(
                        uid, 
                        f"📢 **Сповіщення:**\n\n{message.text}", 
                        parse_mode="Markdown"
                    )
                    sent += 1
                    # Невелика затримка щоб уникнути rate limit
                    await asyncio.sleep(0.05)
                except Exception as e:
                    failed += 1
                    print(f"Помилка відправки користувачу {uid}: {e}")
            
            await message.answer(f"✅ **Розсилка завершена!**\n✓ {sent} / ✗ {failed}", parse_mode="Markdown")
        else:
            await message.answer("❌ Немає користувачів.")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 2. АДМІН: ВІДПОВІДЬ НА ТІКЕТ
    if user_id == ADMIN_ID and mode.startswith("replying:"):
        ticket_id = int(mode.split(":")[1])
        
        # Перевірка довжини повідомлення
        if len(message.text) > 3000:
            await message.answer("❌ **Повідомлення занадто довге!** Максимум 3000 символів.", parse_mode="Markdown")
            return
        
        # Отримуємо інфо про тікет
        ticket_info = await db.get_ticket_info(ticket_id)
        if not ticket_info:
            await message.answer("❌ Тікет не знайдено")
            await db.set_user_mode(ADMIN_ID, "normal")
            return
        
        target_user_id, username, status = ticket_info
        
        # Зберігаємо повідомлення адміна
        await db.save_support_message(ticket_id, "admin", message.text)
        
        # Відправляємо користувачу
        try:
            # Створюємо кнопку для відповіді
            kb = InlineKeyboardBuilder()
            kb.button(text="✍️ Відповісти", callback_data=f"user_reply|{ticket_id}")
            
            await message.bot.send_message(
                target_user_id,
                f"📞 **Служба підтримки:**\n\n{message.text}",
                reply_markup=kb.as_markup(),
                parse_mode="Markdown"
            )
            await message.answer("✅ Відповідь надіслано користувачу!")
        except Exception as e:
            await message.answer(f"❌ Не вдалося надіслати: {e}")
        
        await db.set_user_mode(ADMIN_ID, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    # 3. КОРИСТУВАЧ: ПІДТРИМКА (перше повідомлення)
    if mode == "support":
        # Перевірка довжини повідомлення
        if len(message.text) > 3000:
            await message.answer("❌ **Повідомлення занадто довге!** Максимум 3000 символів.\n\nСпробуйте коротше або розділіть на кілька повідомлень.", parse_mode="Markdown")
            return
        
        # Створюємо або отримуємо тікет
        username = message.from_user.username or "Unknown"
        ticket_id = await db.create_or_get_ticket(user_id, username)
        
        # Зберігаємо повідомлення
        await db.save_support_message(ticket_id, "user", message.text)
        
        try:
            # Створюємо кнопку для адміна
            kb = InlineKeyboardBuilder()
            kb.button(text="📋 Переглянути тікет", callback_data=f"viewticket|{ticket_id}")
            
            # Обрізаємо текст якщо він занадто довгий
            display_text = message.text[:500] + "..." if len(message.text) > 500 else message.text
            
            await message.bot.send_message(
                ADMIN_ID,
                f"🔔 **Нове повідомлення в тікеті #{ticket_id}**\n"
                f"👤 @{username} (ID: {user_id})\n\n"
                f"💬 {display_text}",
                reply_markup=kb.as_markup(),
                parse_mode="Markdown"
            )
            await message.answer("✅ Повідомлення відправлено! Адміністратор відповість найближчим часом.")
        except Exception as e:
            print(f"Помилка відправки повідомлення адміну: {e}")
            await message.answer("✅ Повідомлення збережено!")
        
        await db.set_user_mode(user_id, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(user_id))
        return

    # 4. КОРИСТУВАЧ: ВІДПОВІДЬ В ТІКЕТ
    if mode.startswith("user_replying:"):
        ticket_id = int(mode.split(":")[1])
        username = message.from_user.username or "Unknown"
        
        # Перевірка довжини повідомлення
        if len(message.text) > 3000:
            await message.answer("❌ **Повідомлення занадто довге!** Максимум 3000 символів.", parse_mode="Markdown")
            return
        
        # Зберігаємо повідомлення
        await db.save_support_message(ticket_id, "user", message.text)
        
        # Знову відкриваємо тікет якщо він був закритий
        await db.reopen_ticket(ticket_id)
        
        try:
            # Створюємо кнопку для адміна
            kb = InlineKeyboardBuilder()
            kb.button(text="📋 Переглянути тікет", callback_data=f"viewticket|{ticket_id}")
            
            display_text = message.text[:500] + "..." if len(message.text) > 500 else message.text
            
            await message.bot.send_message(
                ADMIN_ID,
                f"🔔 **Нова відповідь в тікеті #{ticket_id}**\n"
                f"👤 @{username} (ID: {user_id})\n\n"
                f"💬 {display_text}",
                reply_markup=kb.as_markup(),
                parse_mode="Markdown"
            )
            await message.answer("✅ Відповідь відправлена!")
        except Exception as e:
            print(f"Помилка: {e}")
            await message.answer("✅ Відповідь збережена!")
        
        await db.set_user_mode(user_id, "normal")
        await message.answer("🏠 Головне меню", reply_markup=get_main_keyboard(user_id))
        return

    # 5. НЕРОЗПІЗНАНА КОМАНДА
    await message.answer("❓ Не розумію вашу команду. Використовуйте кнопки меню.")
