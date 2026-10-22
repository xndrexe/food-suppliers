"""Поиск кандидатов в поставщики через Yandex Search API.

Яндекс выбран потому, что поставщики food в России живут в рунете: у многих
нет страниц в Google, а агрегаторы вроде regtorg и agroserver Яндекс знает
лучше. Ключ и каталог — из окружения, в репозитории их нет.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

API_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

# Доски объявлений, каталоги и маркетплейсы: на них нет одного поставщика,
# а есть сотни чужих объявлений. Их сайты не разбираем — ищем сам источник.
AGGREGATORS = {
    "avito.ru", "regtorg.ru", "agroserver.ru", "pulscen.ru", "tiu.ru", "satom.ru",
    "flagma.ru", "yell.ru", "zoon.ru", "2gis.ru", "flamp.ru", "ozon.ru",
    "wildberries.ru", "market.yandex.ru", "yandex.ru", "vk.com", "ok.ru",
    "youtube.com", "dzen.ru", "rusprofile.ru", "list-org.com", "checko.ru",
    "spark-interfax.ru", "orgpage.ru", "fabrikant.ru", "b2b-center.ru",
    "optlist.ru", "productcenter.ru", "foodmarkets.ru", "deal.by",
    "allbiz.ru", "all.biz", "ru.all.biz", "spr.ru", "blizko.ru", "unibo.ru",
    "prodtorg.ru", "opt-union.ru", "torg.ru", "metaprom.ru", "vsenamore.ru",
    "hh.ru", "otzovik.com", "irecommend.ru", "wikipedia.org", "rbc.ru",
    "lavka.yandex.ru", "sbermarket.ru", "kuper.ru", "vkusvill.ru", "perekrestok.ru",
    "magnit.ru", "lenta.com", "auchan.ru", "metro-cc.ru", "chibbis.ru",
    "delivery-club.ru", "eda.yandex.ru", "tenchat.ru", "pikabu.ru",
}


@dataclass
class Hit:
    title: str
    url: str
    snippet: str

    @property
    def domain(self) -> str:
        host = urllib.parse.urlsplit(self.url).hostname or ""
        return host.removeprefix("www.")


def is_aggregator(domain: str) -> bool:
    """Домен — площадка с чужими объявлениями (поддомены городов тоже)."""
    return any(domain == a or domain.endswith("." + a) for a in AGGREGATORS)


def _text(node) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_xml(raw: bytes) -> list[Hit]:
    """XML выдачи Яндекса → список находок."""
    root = ET.fromstring(raw)
    err = root.find(".//response/error")
    if err is not None:
        if err.get("code") == "15":  # «ничего не нашлось» — пустая выдача
            return []
        raise RuntimeError(f"yandex: {_text(err)}")
    hits = []
    for doc in root.iter("doc"):
        url = _text(doc.find("url"))
        if not url:
            continue
        passages = " ".join(_text(p) for p in doc.iter("passage"))
        hits.append(Hit(_text(doc.find("title")), url,
                        (passages or _text(doc.find("headline")))[:400]))
    return hits


def yandex_search(query: str, count: int = 20, region: int | None = None) -> list[Hit]:
    """Один запрос к Yandex Search API (YANDEX_SEARCH_KEY, YANDEX_FOLDER_ID)."""
    key = os.environ["YANDEX_SEARCH_KEY"]
    folder = os.environ["YANDEX_FOLDER_ID"]
    body = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
        "folderId": folder,
        "responseFormat": "FORMAT_XML",
        "groupSpec": {"groupMode": "GROUP_MODE_FLAT",
                      "groupsOnPage": str(max(1, min(count, 50))), "docsInGroup": "1"},
    }
    if region:
        body["region"] = str(region)
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Api-Key {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8", "replace")).get("rawData") or ""
    return parse_xml(base64.b64decode(raw)) if raw else []
