def test_sdk_import_unit():
    from chayuan.settings import Settings, XF_MODELS_TYPES

    assert Settings is not None
    assert XF_MODELS_TYPES is not None