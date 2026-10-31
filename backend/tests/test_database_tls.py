import ssl

import pytest
from app.config import Settings
from app.main import create_app
from pydantic import ValidationError


def test_production_tls_verifies_certificate_and_hostname():
    settings = Settings(_env_file=None, database_ssl_mode="verify-full")
    context = settings.database_connect_args()["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_tls_cannot_silently_downgrade_or_apply_to_sqlite():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_ssl_mode="require")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite://",
            allow_sqlite=True,
            database_ssl_mode="verify-full",
        )


def test_application_passes_tls_context_to_asyncpg(monkeypatch):
    from app import store

    original = store.create_async_engine
    captured = {}

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "create_async_engine", capture)
    app = create_app(Settings(_env_file=None, database_ssl_mode="verify-full"))
    assert app.state.service.store.engine
    assert captured["connect_args"]["ssl"].check_hostname
