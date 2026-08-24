"""MAX-бот: текст/голос → агент → КП (.docx)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from maxapi import Bot, Command, Dispatcher

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

MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
if not MAX_BOT_TOKEN:
    raise ValueError("Необходимо установить MAX_BOT_TOKEN в .env")

YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")

yandex_token_service = None
if YANDEX_FOLDER_ID and YANDEX_OAUTH_TOKEN:
    yandex_token_service = token_service.TokenManager(YANDEX_OAUTH_TOKEN)
else:
    logger.warning(
        "YANDEX_FOLDER_ID / YANDEX_OAUTH_TOKEN не заданы — голосовые сообщения недоступны"
    )

bot = Bot(
    token=MAX_BOT_TOKEN,
    api_url=os.getenv("MAX_API_URL", "https://platform-api2.max.ru"),
)
dispatcher = Dispatcher()

chat_histories: dict[int, list] = {}
HISTORY_LIMIT = 30

DOCX_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)[^\s\"']+\.docx|output[\\/][^\s\"']+\.docx",
    re.IGNORECASE,
)


def _extract_text(message) -> str:
    body = getattr(message, "body", None)
    if body is not None and getattr(body, "text", None):
        return (body.text or "").strip()
    return (getattr(message, "text", None) or "").strip()


def _attachment_url(attachment) -> str | None:
    url = getattr(attachment, "url", None)
    if url:
        return url
    payload = getattr(attachment, "payload", None)
    if isinstance(payload, dict):
        value = payload.get("url")
        return value if isinstance(value, str) else None
    if payload is not None:
        return getattr(payload, "url", None)
    return None


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


async def _download_url(url: str, dest_path: Path) -> Path:
    headers = {"Authorization": MAX_BOT_TOKEN}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        dest_path.write_bytes(response.content)
    return dest_path


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


async def reply_with_agent(event, text: str) -> None:
    user_id = event.user_id or 0
    chat_id = event.chat_id
    try:
        result = await run_agent(user_id, text)
    except Exception:
        logger.exception("Ошибка агента")
        await event.message.answer("Произошла внутренняя ошибка. Попробуйте ещё раз.")
        return

    output = (result.get("output") or "").strip() or "Готово."
    docx_path = _find_docx_path(result)

    await event.message.answer(output)

    if docx_path and chat_id is not None:
        try:
            await bot.send_file(
                path=docx_path,
                chat_id=chat_id,
                text="Коммерческое предложение",
            )
        except Exception:
            logger.exception("Не удалось отправить файл КП")
            await event.message.answer(
                f"КП сформировано, но не удалось отправить файл. Путь: {docx_path}"
            )


@dispatcher.message_created(Command("start"))
async def handle_start(event):
    await event.message.answer(
        "Здравствуйте! Я помогу подобрать оборудование из прайса и собрать "
        "коммерческое предложение.\n\n"
        "Напишите или надиктуйте, что нужно, например:\n"
        "«КП на анализатор спектра HSP18 и опцию 5G NR, по 1 шт»."
    )


@dispatcher.message_created()
async def handle_message(event):
    text = _extract_text(event.message)
    if text.startswith("/"):
        return

    voices = list(getattr(event.message, "voices", None) or [])
    audios = list(getattr(event.message, "audios", None) or [])
    # voices уже == audios в SDK; объединяем без дублей
    media = voices or audios

    if media:
        if not yandex_token_service or not YANDEX_FOLDER_ID:
            await event.message.answer(
                "Голосовые сообщения сейчас недоступны: не настроен Yandex STT."
            )
            return

        url = _attachment_url(media[0])
        if not url:
            # fallback: сырые attachments
            for raw in getattr(event.message, "attachments", None) or []:
                if not isinstance(raw, dict):
                    continue
                if raw.get("type") in {"audio", "voice", "file"}:
                    payload = raw.get("payload") or {}
                    if isinstance(payload, dict) and payload.get("url"):
                        url = payload["url"]
                        break
        if not url:
            await event.message.answer("Не удалось получить голосовое сообщение.")
            return

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "voice.ogg"
            try:
                await _download_url(url, file_path)
            except Exception:
                logger.exception("Ошибка скачивания голоса")
                await event.message.answer("Не удалось скачать голосовое сообщение.")
                return

            try:
                recognized = await asyncio.to_thread(
                    speech_to_text_yandex,
                    str(file_path),
                    YANDEX_FOLDER_ID,
                    yandex_token_service,
                )
            except Exception:
                logger.exception("Ошибка STT")
                await event.message.answer("Не удалось распознать речь. Попробуйте ещё раз.")
                return

        if not recognized:
            await event.message.answer(
                "Речь не распознана. Попробуйте ещё раз или напишите текстом."
            )
            return

        await event.message.answer(f"🎤 Распознано: {recognized}")
        await reply_with_agent(event, recognized)
        return

    if not text:
        await event.message.answer(
            "Пришлите текст или голосовое сообщение с запросом на КП."
        )
        return

    await reply_with_agent(event, text)


async def main() -> None:
    logger.info("Запуск MAX-бота (long polling)...")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
