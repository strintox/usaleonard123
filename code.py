import logging
import requests
import base64
import io
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ApplicationBuilder
)
from telegram.constants import ParseMode, ChatAction

TELEGRAM_BOT_TOKEN = "7639285272:AAH-vhuRyoVDMNjqyvkDgfsZw7_d5GEc77Q" # <<< ВАШ ОПУБЛИКОВАННЫЙ TELEGRAM TOKEN
LANGDOCK_API_KEY = "sk-OP8Ybcki6KOxtIcWZCFmrdNGizFUSiMLIu7sncfB0Pzqi1mfSFVhlz1x-GwBRZ1aPCWwglAY2V5bjNsA4c_Zfw" # <<< ВАШ ОПУБЛИКОВАННЫЙ LANGDOCK KEY

LANGDOCK_API_URL = "https://api.langdock.com/anthropic/eu/v1/messages"
CLAUDE_MODEL = "claude-3-7-sonnet-20250219"
MAX_MESSAGE_LENGTH = 4096
API_TIMEOUT = 180 


logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ТелеграмБот")


async def send_long_message(update: Update, text: str):
    """Отправляет длинные сообщения частями."""
    if not text: return
    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        chunk = text[i:i + MAX_MESSAGE_LENGTH]
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
            if len(text) > MAX_MESSAGE_LENGTH: await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            try:
                error_msg = (
                    "<b>⚠️ Ошибка при отправке части ответа</b>\n\n"
                    "Не удалось отправить полный ответ. Возможно, в тексте содержится неподдерживаемое форматирование."
                )
                await update.message.reply_html(error_msg)
            except Exception as inner_e:
                logger.error(f"Невозможно отправить сообщение об ошибке: {inner_e}")
            break

async def call_claude_api(user_id: int, context: ContextTypes.DEFAULT_TYPE, new_user_content: list | str) -> str | None:
    """Вызывает API Claude, возвращает ответ или сообщение об ошибке."""
    if 'history' not in context.user_data: context.user_data['history'] = []
    history = context.user_data['history']
    history.append({"role": "user", "content": new_user_content})

    max_history_messages = 10
    if len(history) > max_history_messages:
        history = history[-max_history_messages:]
        context.user_data['history'] = history

    headers = {"Authorization": f"Bearer {LANGDOCK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": CLAUDE_MODEL, "messages": history, "max_tokens": 4000}
    logger.info(f"Отправка запроса к API для пользователя {user_id}. История: {len(history)} сообщений")

    try:
        response = requests.post(LANGDOCK_API_URL, headers=headers, json=payload, timeout=API_TIMEOUT)
        response.raise_for_status()
        response_data = response.json()

        if response_data.get("content") and isinstance(response_data["content"], list) and len(response_data["content"]) > 0:
            assistant_response_block = response_data["content"][0]
            if assistant_response_block.get("type") == "text":
                assistant_text = assistant_response_block.get("text", "").strip()
                if assistant_text:
                    history.append({"role": "assistant", "content": assistant_text})
                    context.user_data['history'] = history
                    logger.info(f"Получен успешный ответ от API для пользователя {user_id}. Длина: {len(assistant_text)} символов")
                    return assistant_text
                else:
                    logger.error(f"API вернуло пустой текстовый блок: {response_data}")
                    return "<b>⚠️ Ошибка:</b> Получен пустой ответ от нейросети. Попробуйте повторить запрос."
            else:
                 logger.error(f"API вернуло нетекстовый блок: {response_data}")
                 return "<b>⚠️ Ошибка:</b> Получен ответ в неподдерживаемом формате."
        else:
            logger.error(f"Неожиданная структура ответа API: {response_data}")
            stop_reason = response_data.get("stop_reason")
            if stop_reason == "max_tokens":
                 return "<b>⚠️ Внимание:</b> Ответ получился слишком длинным и был обрезан. Попробуйте переформулировать вопрос или используйте команду /clear."
            return f"<b>⚠️ Ошибка:</b> Проблема с ответом нейросети. (Причина: {stop_reason or 'Неизвестная ошибка'})"

    except requests.exceptions.Timeout:
        logger.error(f"Таймаут API ({API_TIMEOUT} сек) для пользователя {user_id}")
        return f"<b>⏳ Превышено время ожидания</b>\n\nНейросеть отвечает слишком долго (более {API_TIMEOUT} сек). Пожалуйста, попробуйте позже или задайте более короткий вопрос."
    except requests.exceptions.HTTPError as e:
        logger.error(f"Ошибка HTTP {e.response.status_code}: {e.response.text}")
        error_details = e.response.text
        try: error_json = e.response.json(); error_details = error_json.get('error', {}).get('message', error_details) or error_json.get('detail', error_details)
        except ValueError: pass
        
        # Понятное сообщение для пользователя
        if e.response.status_code == 401:
            return "<b>❌ Ошибка авторизации</b>\n\nПроблема с доступом к API нейросети. Пожалуйста, сообщите администратору."
        elif e.response.status_code == 429:
            return "<b>⚠️ Превышен лимит запросов</b>\n\nСлишком много запросов к нейросети. Пожалуйста, подождите несколько минут и попробуйте снова."
        elif e.response.status_code >= 500:
            return "<b>🛠️ Технические работы</b>\n\nСервис нейросети временно недоступен. Пожалуйста, попробуйте позже."
        else:
            return f"<b>❌ Ошибка {e.response.status_code}</b>\n\nПроизошла проблема при обработке запроса."
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при запросе к API: {e}")
        return "<b>🔌 Ошибка подключения</b>\n\nНе удалось подключиться к нейросети. Пожалуйста, проверьте подключение к интернету."
    except Exception as e:
        logger.exception(f"Неожиданная ошибка API для пользователя {user_id}: {e}")
        return f"<b>💥 Непредвиденная ошибка</b>\n\nПроизошла неизвестная ошибка при обработке запроса. Пожалуйста, попробуйте позже."

# --- Обработчики Telegram ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start с улучшенным дизайном."""
    user = update.effective_user
    context.user_data['history'] = [] # Очищаем историю
    logger.info(f"Пользователь {user.id} ({user.username or 'без имени'}) запустил бота")

    # Формируем приветственное сообщение с HTML-разметкой
    welcome_message = (
        f"<b>🌟 Добро пожаловать, {user.first_name}! 🌟</b>\n\n"
        f"Я ваш персональный ассистент на базе <b>Claude 3.7 Sonnet</b>. Готов помочь с любыми вопросами!\n\n"
        f"<b>📋 Мои возможности:</b>\n"
        f"  • 💬 Отвечу на ваши вопросы\n"
        f"  • 🖼️ Проанализирую изображения\n"
        f"  • 📚 Помогу с поиском информации\n"
        f"  • 💾 Запомню контекст нашей беседы\n\n"
        f"<b>⚙️ Доступные команды:</b>\n"
        f"  • /start - перезапуск бота\n"
        f"  • /clear - очистка истории диалога\n\n"
        f"<b>👇 Напишите сообщение или отправьте фото</b>"
    )

    await update.message.reply_html(welcome_message)
    logger.info(f"Приветственное сообщение отправлено пользователю {user.id}")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /clear."""
    user_id = update.effective_user.id
    context.user_data['history'] = []
    logger.info(f"История очищена для пользователя {user_id}")
    
    clear_message = (
        "<b>🧹 История диалога очищена!</b>\n\n"
        "Все предыдущие сообщения забыты. Можем начать общение заново."
    )
    
    await update.message.reply_html(clear_message)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений."""
    user_id = update.effective_user.id
    user_text = update.message.text
    if not user_text: return

    logger.info(f"Получено сообщение от пользователя {user_id}. Длина: {len(user_text)} символов")
    # Показываем "печатает..." для лучшего UX
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    response_text = await call_claude_api(user_id, context, user_text)
    if response_text: 
        logger.info(f"Отправка ответа пользователю {user_id}. Длина ответа: {len(response_text)} символов")
        await send_long_message(update, response_text)

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик сообщений с фото."""
    user_id = update.effective_user.id
    logger.info(f"Получено изображение от пользователя {user_id}")
    # Показываем "загрузка фото..." (или "печатает...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        photo_file = await update.message.photo[-1].get_file()
        with io.BytesIO() as buf:
            await photo_file.download_to_memory(buf)
            buf.seek(0)
            image_bytes = buf.read()
            base64_image = base64.b64encode(image_bytes).decode('utf-8')

        media_type = "image/jpeg" # По умолчанию
        if photo_file.file_path:
            ext = photo_file.file_path.split('.')[-1].lower()
            if ext == 'png': media_type = "image/png"
            elif ext == 'gif': media_type = "image/gif"
            elif ext == 'webp': media_type = "image/webp"
        logger.info(f"Тип изображения: {media_type}")

        caption = update.message.caption if update.message.caption else "Опиши это изображение."
        logger.info(f"Подпись к изображению: '{caption}'")

        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64_image}},
            {"type": "text", "text": caption}
        ]
        # Показываем "печатает..." перед долгим запросом к API
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        # Отправляем уведомление о начале обработки изображения
        processing_msg = await update.message.reply_html("<i>🔍 Анализирую изображение, пожалуйста, подождите...</i>")
        
        response_text = await call_claude_api(user_id, context, user_content)
        
        # Удаляем сообщение об обработке
        await processing_msg.delete()
        
        if response_text: 
            logger.info(f"Отправка ответа по изображению пользователю {user_id}")
            await send_long_message(update, response_text)

    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}", exc_info=True)
        error_msg = (
            "<b>⚠️ Ошибка обработки изображения</b>\n\n"
            "К сожалению, не удалось обработать ваше изображение.\n"
            "Пожалуйста, попробуйте отправить другое изображение или в другом формате."
        )
        await update.message.reply_html(error_msg)

# --- Основная функция запуска ---

def main() -> None:
    """Запуск бота."""
    print("\n" + "★" * 60)
    print("★    Запуск Телеграм бота на базе Claude 3.7 Sonnet    ★")
    print("★" * 60 + "\n")
    
    logger.info("Инициализация приложения...")
    
    try:
        # Явно отключаем JobQueue для обхода ошибки 'weak reference'
        builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).job_queue(None)
        logger.info("Создание приложения с настройками по умолчанию")
        application = builder.build()

        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("clear", clear_command))
        application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo_message))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

        print("┌" + "─" * 50 + "┐")
        print("│" + " " * 15 + "СТАТУС СИСТЕМЫ" + " " * 16 + "│")
        print("├" + "─" * 50 + "┤")
        print("│ ✅ Бот успешно инициализирован                  │")
        print("│ ✅ Обработчики команд зарегистрированы          │")
        print("│ ✅ Поддержка изображений активна                │")
        print("│ ⚙️  Запуск бота в режиме опроса...              │")
        print("└" + "─" * 50 + "┘\n")
        
        logger.info("Бот запущен в режиме опроса")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        print("\n" + "⚠️ " * 10)
        print("❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ БОТА:")
        print(f"❌ {e}")
        print("⚠️ " * 10 + "\n")
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    main()
