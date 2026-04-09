# config.py
import os
from dotenv import load_dotenv

# Завантажуємо змінні з .env
load_dotenv()

# Токен від BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Адміністратори бота
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in admin_ids_raw.split(",") if i.strip().isdigit()]

# === API ДЖЕРЕЛА ===
PRIMARY_API_URL = os.getenv("PRIMARY_API_URL")
BACKUP_API_URL = os.getenv("BACKUP_API_URL")
API_URL = BACKUP_API_URL
HOE_SITE_URL = os.getenv("HOE_SITE_URL")

# === FAILOVER ===
FAILOVER_TIMEOUT = int(os.getenv("FAILOVER_TIMEOUT"))
RECOVERY_CHECK_INTERVAL = int(os.getenv("RECOVERY_CHECK_INTERVAL"))

# База даних
DB_NAME = os.getenv("DB_NAME")

# Інтервал оновлення
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL"))