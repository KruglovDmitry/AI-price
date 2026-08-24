"""Загрузка и поиск по прайсу price_01.xlsx."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_PATH = ROOT_DIR / "assets" / "price_01.xlsx"


@dataclass(frozen=True)
class Product:
    name: str
    description: str
    price: float
    delivery_weeks: int | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "price": self.price,
            "price_formatted": _format_money(self.price),
            "delivery_weeks": self.delivery_weeks,
        }


def _format_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[\s\-_/]+", " ", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    text = re.sub(r"[^\d.]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


class PriceCatalog:
    def __init__(self, xlsx_path: str | Path | None = None):
        self.xlsx_path = Path(xlsx_path) if xlsx_path else DEFAULT_PRICE_PATH
        self.products: list[Product] = []
        self._by_norm_name: dict[str, Product] = {}
        self.reload()

    def reload(self) -> None:
        if not self.xlsx_path.exists():
            raise FileNotFoundError(f"Прайс не найден: {self.xlsx_path}")

        wb = load_workbook(self.xlsx_path, data_only=True)
        ws = wb.active
        products: list[Product] = []

        for i, row in enumerate(ws.iter_rows(min_col=1, max_col=4, values_only=True), start=1):
            if i == 1:
                continue
            name, description, price_raw, weeks_raw = row
            if not name or not str(name).strip():
                continue
            price = _to_float(price_raw)
            if price is None:
                continue
            product = Product(
                name=str(name).strip(),
                description=(str(description).strip() if description else ""),
                price=price,
                delivery_weeks=_to_int(weeks_raw),
            )
            products.append(product)

        self.products = products
        self._by_norm_name = {_normalize(p.name): p for p in products}

    def search(self, query: str, limit: int = 8) -> list[Product]:
        q = _normalize(query)
        if not q:
            return []

        scored: list[tuple[int, Product]] = []
        tokens = [t for t in q.split() if t]

        for product in self.products:
            name_n = _normalize(product.name)
            desc_n = _normalize(product.description)
            haystack = f"{name_n} {desc_n}"
            score = 0

            if q == name_n:
                score = 1000
            elif q in name_n:
                score = 500 + len(q)
            elif all(t in haystack for t in tokens):
                score = 200 + sum(30 for t in tokens if t in name_n)
                score += sum(10 for t in tokens if t in desc_n)
            elif any(t in haystack for t in tokens):
                score = sum(20 for t in tokens if t in name_n) + sum(
                    5 for t in tokens if t in desc_n
                )

            if score > 0:
                scored.append((score, product))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [p for _, p in scored[: max(1, limit)]]

    def resolve(self, name: str) -> tuple[Product | None, list[Product]]:
        """Точный матч или единственный кандидат поиска; иначе None + список."""
        key = _normalize(name)
        if key in self._by_norm_name:
            return self._by_norm_name[key], []

        candidates = self.search(name, limit=5)
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates


_catalog: PriceCatalog | None = None


def get_catalog(xlsx_path: str | Path | None = None) -> PriceCatalog:
    global _catalog
    if _catalog is None or (xlsx_path and Path(xlsx_path) != _catalog.xlsx_path):
        _catalog = PriceCatalog(xlsx_path)
    return _catalog


def search_products(query: str, limit: int = 8) -> str:
    """Tool-friendly: JSON-строка с результатами поиска."""
    catalog = get_catalog()
    results = [p.to_public_dict() for p in catalog.search(query, limit=limit)]
    if not results:
        return json_dumps({"found": 0, "items": [], "message": "Ничего не найдено по запросу."})
    return json_dumps({"found": len(results), "items": results})


def json_dumps(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)


def product_as_dict(product: Product) -> dict[str, Any]:
    return asdict(product)
