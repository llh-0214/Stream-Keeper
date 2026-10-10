from live_recorder.recorder import safe_name


def test_safe_name_removes_forbidden_characters():
    assert safe_name('主播/标题:*?"<>|') == "主播_标题"


def test_safe_name_has_fallback():
    assert safe_name("...") == "直播"
