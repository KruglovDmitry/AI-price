"""Универсальный каталог прайса: xlsx + опциональные колонки/правила.

Приоритет метаданных:
1) явные колонки в xlsx (kind / family / parent)
2) overrides в assets/catalog_rules.json → products
3) умеренные эвристики (маркеры kind, порядок строк, family_rules, «для …»)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openpyxl import load_workbook

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_PATH = ROOT_DIR / "assets" / "price_01.xlsx"
DEFAULT_RULES_PATH = ROOT_DIR / "assets" / "catalog_rules.json"

ProductKind = Literal["base", "option", "accessory"]

_MODEL_TOKEN_RE = re.compile(r"\b([A-Za-z]{2,}[A-Za-z0-9]*\d[A-Za-z0-9\-]*)\b")
_FOR_TOKEN_RE = re.compile(
    r"для\s+([A-Za-zА-Яа-я0-9\-]+(?:\s*/\s*[A-Za-zА-Яа-я0-9\-]+)*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Product:
    name: str
    description: str
    price: float
    delivery_weeks: int | None
    kind: ProductKind = "base"
    family: str | None = None
    parent_name: str | None = None
    row_index: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "description": self.description,
            "price": self.price,
            "price_formatted": _format_money(self.price),
            "delivery_weeks": self.delivery_weeks,
            "kind": self.kind,
        }
        if self.family:
            data["family"] = self.family
        if self.parent_name:
            data["parent_name"] = self.parent_name
        return data


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


def _default_rules() -> dict[str, Any]:
    return {
        "synonyms": {},
        "query": {"equipment_terms": [], "option_terms": []},
        "kind_markers": {
            "base_name_prefixes": [],
            "option_name_substrings": ["опция"],
            "accessory_name_substrings": [],
            "force_base_name_substrings": [],
        },
        "family_rules": [],
        "products": {},
    }


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    rules_path = Path(path) if path else Path(os.getenv("CATALOG_RULES_PATH") or DEFAULT_RULES_PATH)
    rules = _default_rules()
    if not rules_path.exists():
        return rules
    loaded = json.loads(rules_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return rules
    rules.update({k: v for k, v in loaded.items() if v is not None})
    return rules


def _header_map(header_row: tuple[Any, ...]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    aliases = {
        "name": ("наименование", "в договор", "name", "товар", "позиция"),
        "description": ("описание", "description", "описание товара"),
        "price": ("ррц", "цена", "price", "стоимость"),
        "delivery_weeks": ("срок", "delivery", "weeks", "нед"),
        "kind": ("kind", "тип", "type"),
        "family": ("family", "семейство", "серия", "series"),
        "parent": ("parent", "родитель", "parent_name", "для прибора"),
    }
    for idx, raw in enumerate(header_row):
        if raw is None:
            continue
        key = _normalize(str(raw))
        for field, names in aliases.items():
            if any(name in key for name in names):
                mapping.setdefault(field, idx)
    # fallback на первые 4 колонки как в текущем прайсе
    mapping.setdefault("name", 0)
    mapping.setdefault("description", 1)
    mapping.setdefault("price", 2)
    mapping.setdefault("delivery_weeks", 3)
    return mapping


def _extract_models(text: str) -> list[str]:
    return [m.group(1).upper() for m in _MODEL_TOKEN_RE.finditer(text or "")]


def _family_from_rules(token: str, family_rules: list[dict[str, str]]) -> str | None:
    token_u = token.upper()
    for rule in family_rules:
        pattern = rule.get("match") or ""
        family = rule.get("family")
        if pattern and family and re.search(pattern, token_u, re.IGNORECASE):
            return family
    return None


def _infer_family(name: str, description: str, rules: dict[str, Any]) -> str | None:
    family_rules = rules.get("family_rules") or []
    for token in _extract_models(f"{name} {description}"):
        family = _family_from_rules(token, family_rules)
        if family:
            return family
    # «для SA5000A / PSA»
    for match in _FOR_TOKEN_RE.finditer(description or ""):
        chunk = match.group(1)
        for part in re.split(r"[/,]| и ", chunk):
            part = part.strip()
            if not part:
                continue
            models = _extract_models(part) or [part]
            for token in models:
                family = _family_from_rules(token, family_rules)
                if family:
                    return family
    return None


def _guess_kind(name: str, description: str, rules: dict[str, Any]) -> ProductKind:
    markers = rules.get("kind_markers") or {}
    name_l = name.lower()
    desc_l = (description or "").lower()

    for marker in markers.get("force_base_name_substrings") or []:
        if marker.lower() in name_l:
            return "base"

    for prefix in markers.get("base_name_prefixes") or []:
        if name_l.startswith(prefix.lower()):
            return "base"

    for marker in markers.get("option_name_substrings") or []:
        if marker.lower() in name_l:
            return "option"

    # Код опции: "PMK …", "HTG Опция …"
    if re.match(r"^[A-ZА-Я]{2,6}\d*\s+\S+", name):
        for marker in markers.get("accessory_name_substrings") or []:
            if marker.lower() in name_l or marker.lower() in desc_l:
                return "accessory"
        return "option"

    for marker in markers.get("accessory_name_substrings") or []:
        if marker.lower() in name_l:
            return "accessory"

    if re.search(r"\bдля\s+\S+", description or "", re.IGNORECASE):
        return "option"

    return "base"


class PriceCatalog:
    def __init__(
        self,
        xlsx_path: str | Path | None = None,
        rules_path: str | Path | None = None,
    ):
        self.xlsx_path = Path(xlsx_path) if xlsx_path else DEFAULT_PRICE_PATH
        self.rules_path = Path(rules_path) if rules_path else Path(
            os.getenv("CATALOG_RULES_PATH") or DEFAULT_RULES_PATH
        )
        self.rules = load_rules(self.rules_path)
        self.products: list[Product] = []
        self._by_norm_name: dict[str, list[Product]] = {}
        self.reload()

    def reload(self) -> None:
        if not self.xlsx_path.exists():
            raise FileNotFoundError(f"Прайс не найден: {self.xlsx_path}")

        self.rules = load_rules(self.rules_path)
        wb = load_workbook(self.xlsx_path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            self.products = []
            self._by_norm_name = {}
            return

        headers = _header_map(tuple(rows[0]))
        overrides: dict[str, dict[str, Any]] = self.rules.get("products") or {}

        raw_items: list[dict[str, Any]] = []
        for i, row in enumerate(rows[1:], start=2):
            if not row:
                continue
            name_idx = headers["name"]
            if name_idx >= len(row) or row[name_idx] is None or not str(row[name_idx]).strip():
                continue
            name = str(row[name_idx]).strip()
            description = ""
            if headers["description"] < len(row) and row[headers["description"]] is not None:
                description = str(row[headers["description"]]).strip()
            price = _to_float(row[headers["price"]]) if headers["price"] < len(row) else None
            if price is None:
                continue
            weeks = (
                _to_int(row[headers["delivery_weeks"]])
                if headers.get("delivery_weeks", 999) < len(row)
                else None
            )

            explicit_kind = None
            explicit_family = None
            explicit_parent = None
            if "kind" in headers and headers["kind"] < len(row) and row[headers["kind"]]:
                explicit_kind = str(row[headers["kind"]]).strip().lower()
            if "family" in headers and headers["family"] < len(row) and row[headers["family"]]:
                explicit_family = str(row[headers["family"]]).strip()
            if "parent" in headers and headers["parent"] < len(row) and row[headers["parent"]]:
                explicit_parent = str(row[headers["parent"]]).strip()

            override = overrides.get(name) or {}
            raw_items.append(
                {
                    "row_index": i,
                    "name": name,
                    "description": description,
                    "price": price,
                    "weeks": weeks,
                    "kind": override.get("kind") or explicit_kind,
                    "family": override.get("family") or explicit_family,
                    "parent": override.get("parent") or override.get("parent_name") or explicit_parent,
                }
            )

        products: list[Product] = []
        current_base: Product | None = None
        current_family: str | None = None

        for item in raw_items:
            name = item["name"]
            description = item["description"]
            kind_raw = item["kind"]
            if kind_raw in {"base", "option", "accessory"}:
                kind: ProductKind = kind_raw  # type: ignore[assignment]
            else:
                kind = _guess_kind(name, description, self.rules)

            family = item["family"] or _infer_family(name, description, self.rules)
            parent_name = item["parent"]

            if kind == "base":
                if not family:
                    models = _extract_models(name)
                    if models:
                        family = _family_from_rules(models[0], self.rules.get("family_rules") or []) or models[0]
                current_family = family
                parent_name = None
            else:
                if not family:
                    family = current_family
                if not parent_name:
                    if current_base and (family is None or family == current_base.family):
                        parent_name = current_base.name
                    elif family:
                        for prev in reversed(products):
                            if prev.kind == "base" and prev.family == family:
                                parent_name = prev.name
                                break
                    elif current_base:
                        parent_name = current_base.name

            product = Product(
                name=name,
                description=description,
                price=item["price"],
                delivery_weeks=item["weeks"],
                kind=kind,
                family=family,
                parent_name=parent_name,
                row_index=item["row_index"],
            )
            products.append(product)
            if kind == "base":
                current_base = product
                current_family = product.family

        self.products = products
        by_name: dict[str, list[Product]] = {}
        for product in products:
            by_name.setdefault(_normalize(product.name), []).append(product)
        self._by_norm_name = by_name

    def _expand_tokens(self, tokens: list[str]) -> list[str]:
        synonyms = self.rules.get("synonyms") or {}
        expanded = list(tokens)
        for token in tokens:
            for extra in synonyms.get(token, []):
                extra_n = _normalize(str(extra))
                if extra_n and extra_n not in expanded:
                    expanded.append(extra_n)
        return expanded

    def search(
        self,
        query: str,
        limit: int = 8,
        *,
        prefer_base: bool | None = None,
    ) -> list[Product]:
        q = _normalize(query)
        if not q:
            return []

        option_terms = [_normalize(t) for t in (self.rules.get("query") or {}).get("option_terms") or []]
        equipment_terms = [_normalize(t) for t in (self.rules.get("query") or {}).get("equipment_terms") or []]

        if prefer_base is None:
            if any(term and term in q for term in option_terms):
                prefer_base = False
            elif any(term and term in q for term in equipment_terms):
                prefer_base = True
            else:
                prefer_base = False

        tokens = [t for t in q.split() if t]
        tokens = self._expand_tokens(tokens)
        focus_terms = [t for t in tokens if t in equipment_terms]
        scored: list[tuple[int, Product]] = []

        for product in self.products:
            name_n = _normalize(product.name)
            desc_n = _normalize(product.description)
            haystack = f"{name_n} {desc_n}"
            score = 0

            if q == name_n:
                score = 1000
            elif q in name_n:
                score = 500 + len(q)
            elif tokens and all(t in haystack for t in tokens):
                score = 200 + sum(40 for t in tokens if t in name_n)
                score += sum(12 for t in tokens if t in desc_n)
            elif tokens and any(t in haystack for t in tokens):
                score = sum(25 for t in tokens if t in name_n) + sum(
                    6 for t in tokens if t in desc_n
                )

            if score <= 0:
                continue

            if prefer_base:
                if product.kind == "base":
                    score += 250
                else:
                    score -= 450 if product.kind == "option" else 280

            # Универсально: усиливать совпадение по «фокусному» типу прибора из запроса
            if focus_terms:
                matched_focus = any(term in name_n for term in focus_terms)
                other_equipment = [
                    term for term in equipment_terms if term not in focus_terms and term in name_n
                ]
                if matched_focus and product.kind == "base":
                    score += 450
                elif matched_focus and product.kind != "base":
                    score -= 500
                elif other_equipment:
                    score -= 700

            if not prefer_base and any(term and term in q for term in option_terms):
                if product.kind in {"option", "accessory"}:
                    score += 80

            if score > 0:
                scored.append((score, product))

        scored.sort(key=lambda item: (-item[0], item[1].kind != "base", item[1].name))
        return [p for _, p in scored[: max(1, limit)]]

    def resolve(self, name: str) -> tuple[Product | None, list[Product]]:
        key = _normalize(name)
        exact = self._by_norm_name.get(key) or []
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact

        candidates = self.search(name, limit=5)
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates

    def find_base(self, query: str) -> Product | None:
        product, _ = self.resolve(query)
        if product and product.kind == "base":
            return product
        bases = [p for p in self.search(query, limit=8, prefer_base=True) if p.kind == "base"]
        return bases[0] if bases else None

    def options_for(self, product_or_query: str) -> tuple[Product | None, list[Product]]:
        base = self.find_base(product_or_query)
        if base is None:
            key = _normalize(product_or_query)
            for candidate in self._by_norm_name.get(key, []):
                if candidate.kind == "base":
                    base = candidate
                    break
        if base is None:
            return None, []

        related: list[Product] = []
        base_key = _normalize(base.name)
        for product in self.products:
            if product.kind == "base":
                continue
            if product.parent_name and _normalize(product.parent_name) == base_key:
                related.append(product)
            elif base.family and product.family == base.family:
                related.append(product)

        uniq: list[Product] = []
        seen: set[tuple[str, float]] = set()
        for item in related:
            key = (item.name, item.price)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        return base, uniq


_catalog: PriceCatalog | None = None


def get_catalog(
    xlsx_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> PriceCatalog:
    global _catalog
    if (
        _catalog is None
        or (xlsx_path and Path(xlsx_path) != _catalog.xlsx_path)
        or (rules_path and Path(rules_path) != _catalog.rules_path)
    ):
        _catalog = PriceCatalog(xlsx_path, rules_path)
    return _catalog


def search_products(query: str, limit: int = 8) -> str:
    catalog = get_catalog()
    results = [p.to_public_dict() for p in catalog.search(query, limit=limit)]
    if not results:
        return json_dumps({"found": 0, "items": [], "message": "Ничего не найдено по запросу."})
    return json_dumps({"found": len(results), "items": results})


def get_product_options(product_name: str) -> str:
    catalog = get_catalog()
    base, options = catalog.options_for(product_name)
    if base is None:
        suggestions = [
            p.to_public_dict() for p in catalog.search(product_name, limit=5, prefer_base=True)
        ]
        return json_dumps(
            {
                "found": 0,
                "base": None,
                "options": [],
                "message": "Базовый прибор не найден. Уточните название.",
                "suggestions": suggestions,
            }
        )
    return json_dumps(
        {
            "found": len(options),
            "base": base.to_public_dict(),
            "options": [p.to_public_dict() for p in options],
            "message": (
                f"Для «{base.name}» найдено опций/аксессуаров: {len(options)}."
                if options
                else f"Для «{base.name}» в прайсе нет отдельных опций/аксессуаров."
            ),
        }
    )


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def product_as_dict(product: Product) -> dict[str, Any]:
    return asdict(product)
