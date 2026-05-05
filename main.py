# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
import database
import handlers
import scheduler

# Налаштування логування (щоб бачити помилки в консолі)
logging.basicConfig(level=logging.INFO)


async def main():
    # 1. Ініціалізація бази даних
    await database.init_db()
    print("✅ База даних підключена")

    # 2. Створення бота і диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # 3. Підключення роутера з handlers.py
    dp.include_router(handlers.router)

    # 4. Запуск фонових задач (передаємо бота, щоб вони могли слати повідомлення)
    asyncio.create_task(scheduler.check_updates(bot))
    asyncio.create_task(scheduler.check_alerts(bot))

    # === НОВЕ: ЗАПУСК БЕКАПЕРА ===
    asyncio.create_task(scheduler.auto_backup(bot))

    print("✅ Фонові процеси запущені")

    # 5. Старт бота
    print("🤖 Бот запущено! Натисніть Ctrl+C для зупинки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинений.")
