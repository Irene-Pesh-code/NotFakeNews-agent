from core.urls import canonical_url, domain


def test_canonical_strips_tracking_and_www():
    a = canonical_url("http://www.ria.ru/news/123/?utm_source=tg&id=5#top")
    b = canonical_url("https://ria.ru/news/123?id=5")
    assert a == b == "https://ria.ru/news/123?id=5"


def test_domain():
    assert domain("https://m.lenta.ru/a") == "lenta.ru"
