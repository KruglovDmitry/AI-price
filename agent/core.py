"""LangChain-агент: поиск по прайсу и генерация КП."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.tools import StructuredTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

from .services.catalog import get_catalog, get_product_options, search_products
from .services.proposal import create_commercial_proposal

ROOT_DIR = Path(__file__).resolve().parents[1]
PROMPT_FILE = Path(__file__).resolve().parent / "prompt.txt"

load_dotenv(ROOT_DIR / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError(
        f"Необходимо установить DEEPSEEK_API_KEY в {ROOT_DIR / '.env'} "
        "(сохраните файл на диск, Ctrl+S)."
    )

PRICE_XLSX_PATH = os.getenv("PRICE_XLSX_PATH")
if PRICE_XLSX_PATH:
    get_catalog(PRICE_XLSX_PATH)
else:
    get_catalog()


class SearchInput(BaseModel):
    query: str = Field(..., description="Поисковый запрос: модель, опция или ключевые слова")
    limit: int = Field(8, description="Максимум результатов")


class OptionsInput(BaseModel):
    product_name: str = Field(
        ...,
        description="Название или модель базового прибора, для которого нужны опции/аксессуары",
    )


class ProposalInput(BaseModel):
    items_json: str = Field(
        ...,
        description=(
            'JSON-массив позиций: [{"name":"Анализатор спектра HSP18","qty":1}]. '
            "name — точное название из прайса."
        ),
    )


def _load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


search_tool = StructuredTool.from_function(
    func=search_products,
    name="search_products",
    description=(
        "Поиск позиций в прайсе по названию или описанию. "
        "Возвращает name, description, price, delivery_weeks, kind "
        "(base/option/accessory), family, parent_name."
    ),
    args_schema=SearchInput,
)

options_tool = StructuredTool.from_function(
    func=get_product_options,
    name="get_product_options",
    description=(
        "Вернуть опции и аксессуары для конкретного базового прибора из прайса. "
        "Использовать при вопросах «какие опции к …», «что ещё нужно к модели»."
    ),
    args_schema=OptionsInput,
)

proposal_tool = StructuredTool.from_function(
    func=create_commercial_proposal,
    name="create_commercial_proposal",
    description=(
        "Создаёт DOCX коммерческое предложение по списку позиций из прайса. "
        "Вызывать после уточнения состава и количества."
    ),
    args_schema=ProposalInput,
)

tools = [search_tool, options_tool, proposal_tool]

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _load_prompt()),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ]
)

llm = ChatDeepSeek(model="deepseek-chat", temperature=0, api_key=DEEPSEEK_API_KEY)
agent = create_openai_tools_agent(llm, tools, prompt)

agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,
    max_iterations=6,
    return_intermediate_steps=True,
    handle_parsing_errors=True,
)
