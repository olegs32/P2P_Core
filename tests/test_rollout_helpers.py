def test_health_confirm_retries_default():
    from src.internal_modules.config import Config
    cfg = Config()
    assert cfg.update.health_confirm_retries == 3
