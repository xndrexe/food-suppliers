"""Карточка поставщика из текста его сайта: правила плюс языковая модель.

Контакты и ИНН достаём правилами — регулярные выражения тут надёжнее модели.
Условия (минимальный заказ, цены, доставка, документы) пишут как попало, их
читает модель. Каждое поле модель обязана подтвердить цитатой со страницы:
цитаты в тексте нет — поле выбрасываем, а не показываем догадку.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PHONE_RE = re.compile(r"(?:\+7|(?<!\d)8)[\s\- ]*\(?\d{3,4}\)?[\s\- ]*\d{1,3}"
                      r"[\s\- ]*\d{2}[\s\- ]*\d{2}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Zа-яА-Я]{2,}")
INN_RE = re.compile(r"ИНН\s*[:№]?\s*(\d{10}|\d{12})(?!\d)")
DOC_WORDS = {
    "декларац": "декларации соответствия", "сертификат": "сертификаты",
    "гост": "ГОСТ", "хассп": "ХАССП", "haccp": "ХАССП", "меркурий": "ВСД «Меркурий»",
    "ветеринарн": "ветеринарные документы", "iso 22000": "ISO 22000",
    "халяль": "халяль", "честный знак": "маркировка «Честный знак»",
}

FIELDS = ("min_order", "price", "delivery", "regions", "documents")


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return None


def inn_valid(inn: str) -> bool:
    """Контрольные цифры ИНН (10 знаков — юрлицо, 12 — ИП)."""
    d = [int(c) for c in inn]

    def check(weights):
        return sum(w * x for w, x in zip(weights, d)) % 11 % 10

    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
                and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])
    return False


def rule_fields(text: str, links: list[str]) -> dict:
    """Телефоны, почты, ИНН и упоминания документов — без модели."""
    phones = []
    for raw in PHONE_RE.findall(text) + [h[4:] for h in links if h.startswith("tel:")]:
        p = normalize_phone(raw)
        if p and p not in phones:
            phones.append(p)
    emails = []
    for e in EMAIL_RE.findall(text) + [h[7:].split("?")[0] for h in links
                                       if h.startswith("mailto:")]:
        e = e.lower().strip(".")
        if e not in emails and not e.endswith((".png", ".jpg", ".webp", ".svg")):
            emails.append(e)
    inns = [i for i in dict.fromkeys(INN_RE.findall(text)) if inn_valid(i)]
    low = text.lower()
    docs = sorted({name for word, name in DOC_WORDS.items() if word in low})
    return {"phones": phones[:3], "emails": emails[:3], "inn": inns[0] if inns else None,
            "doc_mentions": docs}


PROMPT = """Ты помогаешь закупщику сети кафе и магазинов найти поставщика.
Категория, которую ищут: «{category}». Город закупщика: {city}.
Ниже текст сайта компании (несколько страниц). Верни ТОЛЬКО JSON такого вида:
{{
 "is_supplier": true/false,      // компания сама продаёт оптом бизнесу (производитель, дистрибьютор, импортёр). false — розница, доска объявлений, СМИ, справочник
 "matches_category": true/false, // продаёт именно эту категорию
 "name": "название компании как на сайте",
 "kind": "производитель" | "дистрибьютор" | "импортёр" | "оптовая база" | "другое",
 "products": ["до 6 позиций из категории, как на сайте"],
 "city": "город, где компания находится, если указан",
 "summary": "одна фраза: чем компания полезна закупщику",
 "min_order": {{"value": "...", "quote": "..."}},
 "price": {{"value": "...", "quote": "..."}},
 "delivery": {{"value": "...", "quote": "..."}},
 "regions": {{"value": "...", "quote": "..."}},
 "documents": {{"value": "...", "quote": "..."}}
}}
Правила: quote — дословный кусок текста ниже (5–25 слов), из которого взято value.
Нет в тексте — поле null. Ничего не придумывай и не обобщай.
min_order — минимальная партия или сумма заказа; price — цена с единицей;
delivery — как и куда возят, сроки, бесплатно ли; regions — куда работают;
documents — сертификаты, декларации, ХАССП, ветдокументы.

ТЕКСТ САЙТА:
{text}
"""


def _llm_headers() -> dict:
    """Bearer для OpenAI-совместимых шлюзов; Yandex AI Studio хочет «Api-Key» и каталог."""
    h = {"Authorization": f"{os.environ.get('LLM_AUTH', 'Bearer')} {os.environ['LLM_API_KEY']}",
         "Content-Type": "application/json"}
    if os.environ.get("LLM_PROJECT"):
        h["OpenAI-Project"] = os.environ["LLM_PROJECT"]
    return h


def call_llm(prompt: str, attempts: int = 4) -> str:
    """Любая модель с OpenAI-совместимым API: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL.
    На 429 и 5xx ждёт и повторяет: при сборе десятков сайтов лимит провайдера — норма."""
    body = {"model": os.environ.get("LLM_MODEL", "gemini-flash"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 4000,
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=_llm_headers())
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or i == attempts - 1:
                raise
            time.sleep(5 * 2 ** i)
    raise RuntimeError("unreachable")


def parse_json(answer: str) -> dict:
    """Первый JSON-объект из ответа модели (она любит обёртку ```json)."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json in answer")
    data = json.loads(answer[start:end + 1])
    if not isinstance(data, dict) or "is_supplier" not in data:
        raise ValueError("answer is not a supplier card")  # модель вернула эхо схемы или мусор
    return data


def _squash(s: str) -> str:
    """Только буквы и цифры через пробел: кавычки, тире и маркеры списков не мешают сверке."""
    return " ".join(re.sub(r"[^\w]+", " ", s.lower().replace("ё", "е")).split())


def quote_found(quote: str, page: str) -> bool:
    """Цитата есть на странице. Модель склеивает куски через «...» —
    сверяем каждый кусок, нужно найти не меньше 70% текста цитаты."""
    parts = [_squash(x) for x in re.split(r"\.\.\.|…|(?<=[.!?;])\s", quote)]
    parts = [x for x in parts if len(x) >= 12]
    if not parts:
        return False
    found = sum(len(x) for x in parts if x in page)
    return found >= 0.7 * sum(len(x) for x in parts)


def verify_quotes(card: dict, text: str) -> dict:
    """Оставляет поля условий, только если их цитата есть на сайте."""
    page = _squash(text)
    for f in FIELDS:
        item = card.get(f)
        if not isinstance(item, dict) or not item.get("value"):
            card[f] = None
            continue
        if not quote_found(str(item.get("quote") or ""), page):
            card[f] = None  # цитаты нет на странице — значит, догадка модели
    return card


def llm_fields(text: str, category: str, city: str, llm=call_llm, check: str | None = None) -> dict:
    """Поля модели с проверкой цитат по check (весь текст сайта; по умолчанию — text).
    Битый JSON — ещё одна попытка."""
    prompt = PROMPT.format(category=category, city=city, text=text[:30000])
    try:
        card = parse_json(llm(prompt))
    except ValueError:  # json.JSONDecodeError — тоже ValueError
        card = parse_json(llm(prompt))
    return verify_quotes(card, check or text)
