"""Telegram-бот: текст/голос → агент → КП (.docx)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

from agent.core import agent_executor  # noqa: E402
from agent.services import token_service  # noqa: E402
from agent.services.proposal import pop_last_proposal_path  # noqa: E402
from agent.utils import speech_to_text_yandex  # noqa: E402

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Необходимо установить TELEGRAM_BOT_TOKEN в .env")

YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")

yandex_token_service = None
if YANDEX_FOLDER_ID and YANDEX_OAUTH_TOKEN:
    yandex_token_service = token_service.TokenManager(
        YANDEX_OAUTH_TOKEN,
        token_file=str(ROOT_DIR / "tokens.json"),
    )
else:
    logger.warning(
        "YANDEX_FOLDER_ID / YANDEX_OAUTH_TOKEN не заданы — голосовые сообщения недоступны"
    )

chat_histories: dict[int, list] = {}
HISTORY_LIMIT = 30

DOCX_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)[^\s\"']+\.docx|output[\\/][^\s\"']+\.docx",
    re.IGNORECASE,
)

START_TEXT = (
    "Здравствуйте! Я помогу подобрать оборудование из прайса и собрать "
    "коммерческое предложение.\n\n"
    "Напишите или надиктуйте, что нужно, например:\n"
    "«КП на анализатор спектра HSP18 и опцию 5G NR, по 1 шт»."
)


def _history_for(user_id: int) -> list:
    return chat_histories.setdefault(user_id, [])


def _trim_history(user_id: int) -> None:
    hist = chat_histories.get(user_id, [])
    if len(hist) > HISTORY_LIMIT:
        chat_histories[user_id] = hist[-HISTORY_LIMIT:]


def _find_docx_path(agent_result: dict) -> str | None:
    path = pop_last_proposal_path()
    if path and Path(path).exists():
        return path

    for step in agent_result.get("intermediate_steps") or []:
        try:
            observation = step[1]
        except (TypeError, IndexError):
            continue
        text = observation if isinstance(observation, str) else str(observation)
        match = DOCX_PATH_RE.search(text)
        if match and Path(match.group(0)).exists():
            return match.group(0)

    match = DOCX_PATH_RE.search(agent_result.get("output") or "")
    if match and Path(match.group(0)).exists():
        return match.group(0)
    return None


async def run_agent(user_id: int, text: str) -> dict:
    current_date = datetime.now().strftime("%d.%m.%Y")
    history = _history_for(user_id)
    result = await agent_executor.ainvoke(
        {
            "input": text,
            "chat_history": history,
            "current_date": current_date,
        }
    )
    history.append(HumanMessage(content=text))
    history.append(AIMessage(content=result.get("output") or ""))
    _trim_history(user_id)
    return result


async def reply_with_agent(update: Update, text: str) -> None:
    user = update.message.from_user
    user_id = user.id if user else 0
    try:
        result = await run_agent(user_id, text)
    except Exception:
        logger.exception("Ошибка агента")
        await update.message.reply_text("Произошла внутренняя ошибка. Попробуйте ещё раз.")
        return

    output = (result.get("output") or "").strip() or "Готово."
    docx_path = _find_docx_path(result)

    await update.message.reply_text(output)

    if docx_path:
        try:
            with open(docx_path, "rb") as doc_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=Path(docx_path).name,
                    caption="Коммерческое предложение",
                )
        except Exception:
            logger.exception("Не удалось отправить файл КП")
            await update.message.reply_text(
                f"КП сформировано, но не удалось отправить файл. Путь: {docx_path}"
            )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Произошла внутренняя ошибка. Попробуйте ещё раз.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(START_TEXT)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text(
            "Пришлите текст или голосовое сообщение с запросом на КП."
        )
        return
    await reply_with_agent(update, text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not yandex_token_service or not YANDEX_FOLDER_ID:
        await update.message.reply_text(
            "Голосовые сообщения сейчас недоступны: не настроен Yandex STT."
        )
        return

    try:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = str(Path(tmp) / "voice_message.ogg")
            voice_file = await update.message.voice.get_file()
            await voice_file.download_to_drive(file_path)
            text = speech_to_text_yandex(
                file_path,
                YANDEX_FOLDER_ID,
                yandex_token_service,
            )

        if not text:
            await update.message.reply_text(
                "Речь не распознана. Попробуйте ещё раз или напишите текстом."
            )
            return

        await update.message.reply_text(f"🎤 Распознано: {text}")
        await reply_with_agent(update, text)
    except Exception:
        logger.exception("Ошибка обработки голоса")
        await update.message.reply_text(
            "🔇 Ошибка обработки голоса. Попробуйте текстовое сообщение."
        )


def main() -> None:
    logger.info("Запуск Telegram-бота...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_error_handler(error_handler)
    app.run_polling()
    logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()
