"""Оценка «с кем связаться первым»: баллы с объяснением и вопросы на звонок.

Вес у признаков простой и открытый: закупщик должен видеть, за что поставщик
поднялся в списке, и спорить с этим. Чёрный ящик с «рейтингом 87» он не
примет — и будет прав.
"""
from __future__ import annotations

WEIGHTS = {
    "same_city": 20, "delivers": 12, "producer": 10, "importer": 6, "distributor": 5,
    "phone": 8, "email": 4, "inn": 6, "documents": 10, "doc_mention": 6,
    "min_order": 6, "price": 8, "delivery": 8,
}
MAX_SCORE = (WEIGHTS["same_city"] + WEIGHTS["producer"] + WEIGHTS["phone"]
             + WEIGHTS["email"] + WEIGHTS["inn"] + WEIGHTS["documents"]
             + WEIGHTS["min_order"] + WEIGHTS["price"] + WEIGHTS["delivery"])
NATIONWIDE = ("вся россия", "всей россии", "по россии", "по всей", "рф", "регионы")


def _has(card: dict, field: str) -> bool:
    return bool(card.get(field) and card[field].get("value"))


def _city_root(city: str) -> str:
    # «Екатеринбург» и «Екатеринбурге», «Москва» и «Москве»
    return city.lower()[:-1] if len(city) > 5 else city.lower()


def score(card: dict, city: str) -> dict:
    """Добавляет к карточке score (0–100), reasons, minuses, ask."""
    reasons, minuses, ask, points = [], [], [], 0
    root = _city_root(city)
    regions = (card.get("regions") or {}).get("value", "") if _has(card, "regions") else ""
    delivery = (card.get("delivery") or {}).get("value", "") if _has(card, "delivery") else ""
    where = f"{regions} {delivery}".lower()

    if root in (card.get("city") or "").lower():
        points += WEIGHTS["same_city"]
        reasons.append(f"Склад или офис в городе {city}")
    elif root in where or any(w in where for w in NATIONWIDE):
        points += WEIGHTS["delivers"]
        reasons.append(f"Пишут, что возят в {city} или по России")
    else:
        minuses.append("Не видно, возят ли в ваш город")
        ask.append(f"Возите ли в {city}, за сколько дней и сколько стоит доставка?")

    kind = card.get("kind") or ""
    if kind == "производитель":
        points += WEIGHTS["producer"]
        reasons.append("Производитель — цена без посредника")
    elif kind == "импортёр":
        points += WEIGHTS["importer"]
        reasons.append("Импортёр — товар из первых рук")
    elif kind in ("дистрибьютор", "оптовая база"):
        points += WEIGHTS["distributor"]
        reasons.append("Оптовый поставщик с широким ассортиментом")

    if card.get("phones"):
        points += WEIGHTS["phone"]
    if card.get("emails"):
        points += WEIGHTS["email"]
    if not card.get("phones") and not card.get("emails"):
        minuses.append("На сайте нет телефона и почты")
    if card.get("inn"):
        points += WEIGHTS["inn"]
        reasons.append("ИНН на сайте — компанию можно проверить в ФНС")
    else:
        minuses.append("ИНН на сайте не найден")

    if _has(card, "documents"):
        points += WEIGHTS["documents"]
        reasons.append("Пишут про документы на продукцию")
    elif card.get("doc_mentions"):
        points += WEIGHTS["doc_mention"]
        reasons.append("Упоминают " + ", ".join(card["doc_mentions"][:2]))
    else:
        ask.append("Какие документы даёте на партию: декларация, ветсвидетельство?")

    for field, label, question in (
        ("min_order", "Известна минимальная партия", "Какая минимальная партия или сумма заказа?"),
        ("price", "Цена есть на сайте", "Цена за единицу и от какого объёма скидка?"),
        ("delivery", "Описаны условия доставки", None),
    ):
        if _has(card, field):
            points += WEIGHTS[field]
            reasons.append(label)
        elif question:
            ask.append(question)

    filled = sum(_has(card, f) for f in ("min_order", "price", "delivery", "regions", "documents"))
    filled += bool(card.get("phones") or card.get("emails")) + bool(card.get("inn"))
    card.update(score=round(100 * points / MAX_SCORE), reasons=reasons,
                minuses=minuses, ask=ask[:3], completeness=round(100 * filled / 7))
    return card
