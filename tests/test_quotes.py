from core.quotes import MatchStatus, normalize, verify_quote

PAGE = (
    "МОСКВА, 1 октября. Министр заявил журналистам: «Цены на всё вырастут "
    "в 2027\u00a0году — примерно на 7%». Также он отметил, что решение "
    "о субсидиях будет принято до конца года, а программа продлится до 2030 года."
)


def test_normalize_basic():
    assert normalize("  «Ёлка»\u00a0—  тест ") == "елка - тест"


def test_exact_with_other_quotes_and_yo():
    m = verify_quote(PAGE, '"цены на все вырастут в 2027 году - примерно на 7%"')
    assert m.status == MatchStatus.EXACT
    assert m.verified


def test_fragment_taken_from_original_text():
    m = verify_quote(PAGE, "Цены на всё вырастут")
    assert "«Цены на всё вырастут" in m.fragment


def test_ellipsis_parts_in_order():
    m = verify_quote(PAGE, "решение о субсидиях […] программа продлится до 2030 года")
    assert m.status == MatchStatus.EXACT


def test_ellipsis_parts_wrong_order():
    m = verify_quote(PAGE, "программа продлится до 2030 года … решение о субсидиях")
    assert m.status != MatchStatus.EXACT


def test_fuzzy_small_wording_change():
    m = verify_quote(PAGE, "он отметил что решение о субсидиях будет принято к концу года")
    assert m.status == MatchStatus.FUZZY
    assert m.score >= 90


def test_changed_number_is_not_exact():
    m = verify_quote(PAGE, "Цены вырастут на 12%")
    assert m.status == MatchStatus.NOT_FOUND


def test_changed_number_in_long_quote_rejected():
    # символьно почти идентично, но год другой — это другой факт
    m = verify_quote(PAGE, "а программа продлится до 2031 года")
    assert m.status == MatchStatus.NOT_FOUND


def test_absent_quote():
    m = verify_quote(PAGE, "Министр подал в отставку после заседания правительства")
    assert m.status == MatchStatus.NOT_FOUND
    assert not m.verified


def test_short_quote_no_fuzzy():
    m = verify_quote(PAGE, "цены упадут")
    assert m.status == MatchStatus.NOT_FOUND


def test_empty_inputs():
    assert verify_quote("", "что-то").status == MatchStatus.NOT_FOUND
    assert verify_quote(PAGE, "  ").status == MatchStatus.NOT_FOUND
