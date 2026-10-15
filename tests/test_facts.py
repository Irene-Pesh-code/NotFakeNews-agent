from core.facts import extract_facts, match_facts, stem

CLAIM = "Полиция задержала 12 участников согласованного митинга в Екатеринбурге у здания мэрии"

PARAPHRASE = (
    "Новости региона. В Екатеринбурге силовики задержали 12 человек. "
    "Задержания прошли на согласованном митинге возле мэрии, участников увезли в отделы полиции. "
    "Организаторы обещают обжаловать действия силовиков."
)


def test_stem_matches_word_forms():
    assert stem("митинга") == stem("митинге")
    assert stem("Москве") == stem("Москва")
    assert stem("задержала") == stem("задержали")


def test_extract_facts():
    f = extract_facts(CLAIM)
    assert f.numbers == ["12"]
    assert stem("Екатеринбурге") in f.entities
    assert stem("Полиция") not in f.entities      # первое слово предложения — не имя


def test_paraphrase_matches():
    m = match_facts(PARAPHRASE, CLAIM)
    assert m is not None
    assert "12 человек" in m.fragment


def test_different_number_rejected():
    assert match_facts(PARAPHRASE.replace("12", "40"), CLAIM) is None


def test_different_city_rejected():
    assert match_facts(PARAPHRASE.replace("Екатеринбурге", "Новосибирске"), CLAIM) is None


def test_unrelated_text_rejected():
    text = "В Екатеринбурге открыли новый парк. В мэрии рассказали о планах благоустройства на 12 лет."
    assert match_facts(text, CLAIM) is None


def test_facts_must_be_close_together():
    # слова разбросаны по разным частям длинной страницы — это не подтверждение
    far = ("В Екатеринбурге прошёл фестиваль. " + "Обычный текст без связи. " * 30 +
           "Полиция задержала 12 нарушителей ПДД. " + "Ещё текст. " * 30 +
           "Митинг у мэрии был согласован, участников было много.")
    assert match_facts(far, CLAIM) is None


def test_too_short_claim_not_checked():
    assert match_facts(PARAPHRASE, "Задержали людей") is None
