"""Проверки сборщика без сети: правила, сверка цитат, оценка, отбор кандидатов."""
import json

from collector import extract, fetch, run, score, search

SITE = """ООО «Морской берег». ИНН 7707083893. Телефон: +7 (343) 379-00-39, 8 800 555-35-35.
Почта: sale@bereg.ru. Минимальная сумма заказа — 15 000 рублей.
Доставка по Екатеринбургу бесплатно от 30 000 ₽, в регионы — транспортной компанией.
На всю продукцию есть декларации соответствия и ветеринарные свидетельства «Меркурий»."""


def test_inn_checksum():
    assert extract.inn_valid("7707083893")        # ИНН Сбербанка
    assert extract.inn_valid("500100732259")      # ИП, 12 знаков
    assert not extract.inn_valid("7707083894")    # сбита последняя цифра
    assert not extract.inn_valid("123")


def test_normalize_phone():
    assert extract.normalize_phone("8 (343) 379-00-39") == "+73433790039"
    assert extract.normalize_phone("+7 343 379 00 39") == "+73433790039"
    assert extract.normalize_phone("379-00-39") is None


def test_rule_fields():
    r = extract.rule_fields(SITE, ["tel:+73432887388", "mailto:Opt@Bereg.ru?subject=x"])
    assert r["phones"] == ["+73433790039", "+78005553535", "+73432887388"]
    assert r["emails"] == ["sale@bereg.ru", "opt@bereg.ru"]
    assert r["inn"] == "7707083893"
    assert "декларации соответствия" in r["doc_mentions"]
    assert "ВСД «Меркурий»" in r["doc_mentions"]


def test_wrong_inn_is_dropped():
    assert extract.rule_fields("ИНН 7707083894", [])["inn"] is None


def test_quote_survives_punctuation_and_ellipsis():
    q = "Минимальная сумма заказа - 15000 рублей... Доставка по Екатеринбургу бесплатно от 30 000"
    assert extract.quote_found(q, extract._squash(SITE.replace("15 000", "15000")))


def test_invented_quote_is_dropped():
    card = {"min_order": {"value": "от 5 кг", "quote": "Минимальный заказ от 5 килограммов любой рыбы"},
            "delivery": {"value": "бесплатно от 30 000 ₽",
                         "quote": "Доставка по Екатеринбургу бесплатно от 30 000 ₽"},
            "price": {"value": "450 ₽/кг", "quote": ""}, "regions": None, "documents": "да"}
    out = extract.verify_quotes(card, SITE)
    assert out["min_order"] is None          # выдумка модели
    assert out["price"] is None              # без цитаты не верим
    assert out["documents"] is None          # не тот формат
    assert out["delivery"]["value"] == "бесплатно от 30 000 ₽"


def test_llm_fields_retries_broken_json():
    answers = iter(["{oops", json.dumps({"is_supplier": True, "min_order": {
        "value": "15 000 ₽", "quote": "Минимальная сумма заказа — 15 000 рублей"}})])
    card = extract.llm_fields(SITE, "Рыба", "Екатеринбург", llm=lambda _p: next(answers))
    assert card["min_order"]["value"] == "15 000 ₽"


def test_parse_json_strips_wrapper():
    assert extract.parse_json('```json\n{"is_supplier": false}\n```') == {"is_supplier": False}


def test_parse_json_rejects_schema_echo():
    import pytest
    with pytest.raises(ValueError):
        extract.parse_json('{"type": "json_object", "min_order": null}')


def test_aggregators():
    assert search.is_aggregator("avito.ru")
    assert search.is_aggregator("ekaterinburg.pulscen.ru")
    assert not search.is_aggregator("tkdip.ru")


def test_pick_subpages_prefers_conditions():
    links = [("/contacts/", "Контакты"), ("/blog/1", "Рецепт ролла"),
             ("/dostavka", "Доставка и оплата"), ("https://other.ru/opt", "Опт")]
    subs = fetch.pick_subpages("https://bereg.ru/", links)
    assert "https://bereg.ru/contacts/" in subs and "https://bereg.ru/dostavka" in subs
    assert all(s.startswith("https://bereg.ru/") for s in subs)


def _card(**kw):
    base = {"city": "", "kind": "другое", "phones": [], "emails": [], "inn": None,
            "doc_mentions": [], **{f: None for f in extract.FIELDS}}
    return {**base, **kw}


def test_score_local_full_card_beats_empty():
    full = score.score(_card(city="Екатеринбург", kind="производитель", phones=["+7"],
                             emails=["a@b.ru"], inn="7707083893",
                             **{f: {"value": "x", "quote": "y"} for f in extract.FIELDS}),
                       "Екатеринбург")
    empty = score.score(_card(), "Екатеринбург")
    assert full["score"] == 100 and full["completeness"] == 100
    assert empty["score"] == 0
    assert "На сайте нет телефона и почты" in empty["minuses"]
    assert len(empty["ask"]) == 3


def test_score_counts_delivery_to_city():
    c = score.score(_card(city="Москва", delivery={"value": "по всей России", "quote": "q"}),
                    "Екатеринбург")
    assert "Пишут, что возят в Екатеринбург или по России" in c["reasons"]


def test_candidates_skip_aggregators_and_repeats(monkeypatch, tmp_path):
    hits = [search.Hit("a", "https://avito.ru/x", ""), search.Hit("b", "https://bereg.ru/", ""),
            search.Hit("c", "https://bereg.ru/opt", ""), search.Hit("d", "https://www.ryba.ru/", "")]
    monkeypatch.setattr(run, "CACHE", tmp_path)
    monkeypatch.setattr(run, "SEARCH", lambda q, n, r: hits)
    got = run.candidates({"queries": ["рыба оптом {city}"]}, {"name": "Москва", "yandex_region": 213}, 5)
    assert [h.domain for h in got] == ["bereg.ru", "ryba.ru"]


def test_parse_yandex_xml():
    raw = """<?xml version="1.0" encoding="utf-8"?><yandexsearch><response><results><grouping>
    <group><doc><url>https://bereg.ru/</url><title>Рыба <hlword>оптом</hlword></title>
    <passages><passage>Свежая рыба для ресторанов</passage></passages></doc></group>
    </grouping></results></response></yandexsearch>""".encode()
    hits = search.parse_xml(raw)
    assert [(h.domain, h.title, h.snippet) for h in hits] == [
        ("bereg.ru", "Рыба оптом", "Свежая рыба для ресторанов")]
    empty = b'<yandexsearch><response><error code="15">nothing</error></response></yandexsearch>'
    assert search.parse_xml(empty) == []


def test_candidates_mix_queries(monkeypatch, tmp_path):
    by_query = {"q1": [search.Hit("", f"https://a{i}.ru/", "") for i in range(5)],
                "q2": [search.Hit("", f"https://b{i}.ru/", "") for i in range(5)]}
    monkeypatch.setattr(run, "CACHE", tmp_path)
    monkeypatch.setattr(run, "SEARCH", lambda q, n, r: by_query[q])
    got = run.candidates({"queries": ["q1", "q2"]}, {"name": "Москва", "yandex_region": 213}, 4)
    assert [h.domain for h in got] == ["a0.ru", "b0.ru", "a1.ru", "b1.ru"]


def test_score_halves_when_region_excludes_city():
    terms = {f: {"value": "x", "quote": "y"} for f in ("min_order", "price", "delivery", "documents")}
    far = score.score(_card(city="Москва", phones=["+7"], regions={"value": "Москва и МО", "quote": "q"},
                            **terms), "Екатеринбург")
    near = score.score(_card(city="Екатеринбург", phones=["+7"], **terms), "Екатеринбург")
    assert far["score"] < near["score"] / 2
    assert "По сайту работают только: Москва и МО" in far["minuses"]


def test_score_understands_federal_district():
    c = score.score(_card(city="Челябинск", regions={"value": "УрФО", "quote": "q"}), "Екатеринбург")
    assert "Пишут, что возят в Екатеринбург или по России" in c["reasons"]
