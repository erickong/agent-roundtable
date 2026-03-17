from i18n import is_chinese, t, get_default_experts


def test_is_chinese_detects_cjk():
    assert is_chinese("如何设计系统？") is True
    assert is_chinese("How to design a system?") is False
    assert is_chinese("混合 mixed text 测试") is True
    assert is_chinese("") is False


def test_t_selects_correct_language():
    assert t("中文议题", "你好", "hello") == "你好"
    assert t("English topic", "你好", "hello") == "hello"


def test_get_default_experts_returns_correct_language():
    zh_experts = get_default_experts("如何提高代码质量？")
    en_experts = get_default_experts("How to improve code quality?")

    assert zh_experts[0]["name"] == "创新专家"
    assert en_experts[0]["name"] == "Innovation Expert"
    assert len(zh_experts) == len(en_experts) == 4
