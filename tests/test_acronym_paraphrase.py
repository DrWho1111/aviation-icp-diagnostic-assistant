import pytest
from icp.answering import validate_grounded


def test_chinese_words_between_acronym_and_parenthetical_do_not_bypass_source_guard():
    with pytest.raises(ValueError,match='English acronym'):
        validate_grounded({'answer':'ICU是输入调理模块（Input Conditioning Unit）。','citations':['source'],'sufficient':True},
            [{'id':'source','raw_text':'ICU 输入调理模块完成滤波与检测。'}])
