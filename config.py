import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Создайте бота у @BotFather и укажите токен "
        "в переменной окружения BOT_TOKEN (см. .env.example)."
    )
