# AI-price — Telegram-бот коммерческих предложений

Бот в Telegram принимает текст или голос, ищет позиции в прайсе (`assets/price_01.xlsx`) и собирает КП по шаблону (`assets/example.docx`).

Стек: LangChain + DeepSeek, Yandex SpeechKit STT, `python-telegram-bot`.

## Быстрый старт

```bash
cd AI-price
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # заполните ключи
python -m agent.bots.telegram_bot
```

Альтернатива: `python -m agent.telegram_bot`

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DEEPSEEK_API_KEY` | API-ключ DeepSeek |
| `TELEGRAM_BOT_TOKEN` | токен бота от BotFather |
| `YANDEX_FOLDER_ID` | folder id Yandex Cloud |
| `YANDEX_OAUTH_TOKEN` | OAuth для IAM (STT) |
| `PRICE_XLSX_PATH` | опционально, путь к прайсу |
| `PROPOSAL_TEMPLATE_PATH` | опционально, шаблон DOCX |
| `OUTPUT_DIR` | папка для готовых КП (по умолчанию `output/`) |
| `CATALOG_RULES_PATH` | правила каталога (синонимы, family, overrides) |

Каталог универсальный: метаданные берутся из колонок прайса (`kind`/`family`/`parent`, если есть), затем из [`assets/catalog_rules.json`](assets/catalog_rules.json), и только потом — общие эвристики (порядок строк, маркеры «опция», фокусные слова запроса).

## Пример диалога

Пользователь: `Нужен анализатор HSP18 и опция измерения 5G NR, по одному. Сделай КП.`

Бот: ищет в прайсе → уточняет при необходимости → генерирует `output/KP_....docx` → отправляет файл в чат.

## Локальная проверка без бота

```bash
python -c "from agent.services.catalog import get_catalog; c=get_catalog(); print(len(c.products), c.search('HSP18')[0].name)"
python -c "from agent.services.proposal import build_proposal; print(build_proposal([{'name':'Анализатор спектра HSP18','qty':1},{'name':'Опция измерения 5G NR','qty':1}]))"
```

## Структура

```
agent/
  core.py              # LangChain agent + tools
  prompt.txt
  utils.py             # Yandex STT
  telegram_bot.py      # удобный entrypoint
  bots/telegram_bot.py # Telegram handlers
  services/
    catalog.py         # прайс
    proposal.py        # сборка DOCX
    token_service.py   # IAM Yandex
assets/
  price_01.xlsx
  example.docx
output/
```
