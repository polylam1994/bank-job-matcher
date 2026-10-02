from bjm.common import canonical_supplier, is_ignored, name_key, tokens


def test_tokens_drop_initials_stopwords_and_expand_abbreviations():
    assert tokens("MR J & M SMITH") == ["SMITH"]
    assert tokens("HWS BONDI") == ["HOT", "WATER", "BONDI"]
    assert tokens("O'Brien Pty Ltd") == ["OBRIEN"]


def test_name_key_is_order_independent():
    assert name_key("SMITH JOHN") == name_key("John Smith")


def test_supplier_alias_and_ignore_rules():
    assert canonical_supplier("BUNNINGS 742000 ALEXANDRIA") == "Bunnings"
    assert canonical_supplier("J SMITH") is None
    assert is_ignored("PAY - J OCONNOR")
    assert not is_ignored("REECE PTY LTD")
