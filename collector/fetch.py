"""Загрузка сайта поставщика: главная плюс страницы контактов и условий."""
from __future__ import annotations

import http.client
import logging
import re
import ssl
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

# Страницы, где обычно лежит то, что нужно закупщику.
SUBPAGE_WORDS = ("контакт", "о компании", "о нас", "доставк", "оплат", "условия",
                 "сертификат", "документ", "оптов", "опт", "прайс", "сотруднич",
                 "партнер", "партнёр", "contact", "about", "delivery", "price")
MAX_SUBPAGES = 3
MAX_PAGE_CHARS = 12000
SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
# Чем падает загрузка: сеть и HTTP-коды (OSError), битая кодировка (ValueError).
FETCH_ERRORS = (OSError, ValueError, http.client.HTTPException)
log = logging.getLogger(__name__)


class _Page(HTMLParser):
    """Видимый текст страницы и ссылки с подписями."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []
        elif tag == "title":
            self._in_title = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "td"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._anchor).split())))
            self._href = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._anchor.append(data)
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = (" ".join(line.split()) for line in raw.splitlines())
        return "\n".join(line for line in lines if line)


def get(url: str, timeout: int = 15) -> str:
    """HTML страницы; кодировку берём из заголовка или meta."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # у части мелких поставщиков просроченный сертификат
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read(2_000_000)
        charset = resp.headers.get_content_charset()
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', body[:3000])
        charset = m.group(1).decode() if m else "utf-8"
    return body.decode(charset, "replace")


def parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    return page


def pick_subpages(base: str, links: list[tuple[str, str]]) -> list[str]:
    """Ссылки того же сайта на контакты, доставку, документы — до MAX_SUBPAGES."""
    host = urllib.parse.urlsplit(base).hostname
    seen, out = {base.rstrip("/")}, []
    for href, anchor in links:
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        url = urllib.parse.urljoin(base, href).split("#")[0]
        if urllib.parse.urlsplit(url).hostname != host or url.rstrip("/") in seen:
            continue
        label = (anchor + " " + url).lower()
        if any(w in label for w in SUBPAGE_WORDS):
            seen.add(url.rstrip("/"))
            out.append(url)
        if len(out) >= MAX_SUBPAGES:
            break
    return out


def fetch_site(url: str) -> dict:
    """{url, title, pages: [{url, text}], links} — главная и до трёх подстраниц."""
    root = urllib.parse.urlsplit(url)
    home = f"{root.scheme}://{root.netloc}/"
    main = parse(get(home))
    pages = [{"url": home, "text": main.text()[:MAX_PAGE_CHARS]}]
    for sub in pick_subpages(home, main.links):
        try:
            pages.append({"url": sub, "text": parse(get(sub)).text()[:MAX_PAGE_CHARS]})
        except FETCH_ERRORS as e:  # подстраница не открылась — живём с главной
            log.info("subpage %s failed: %s", sub, e)
    return {"url": home, "title": " ".join(main.title.split()), "pages": pages,
            "links": [h for h, _ in main.links if h and h.startswith(("tel:", "mailto:"))]}
