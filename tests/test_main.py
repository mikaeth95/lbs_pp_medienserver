import main


def test_local_mode_uses_localhost(monkeypatch):
    monkeypatch.setenv("FLASK_PORT", "5050")
    monkeypatch.setenv("FLASK_DEBUG", "1")

    assert main.server_settings(False) == {
        "host": "127.0.0.1",
        "port": 5050,
        "debug": True,
    }


def test_raspberry_mode_uses_network_address_without_debug(monkeypatch):
    monkeypatch.setenv("FLASK_PORT", "5050")
    monkeypatch.setenv("FLASK_DEBUG", "1")

    assert main.server_settings(True) == {
        "host": "0.0.0.0",
        "port": 5050,
        "debug": False,
    }


def test_raspberry_mode_can_be_enabled_with_environment(monkeypatch):
    seen = {}

    class FakeApp:
        def run(self, **settings):
            seen.update(settings)

    monkeypatch.setenv("RASPBERRY_MODE", "1")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setattr(main, "RASPBERRY_MODE", False)
    monkeypatch.setattr(main, "create_app", FakeApp)

    main.main()

    assert seen["host"] == "0.0.0.0"
    assert seen["debug"] is False
