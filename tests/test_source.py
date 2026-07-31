"""Unit tests for InfisicalSettingsSource — the Infisical SDK is faked (no network)."""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pydantic_settings_infisical import InfisicalSettingsSource

BASE_CFG = {"infisical_project_id": "proj-123"}


class _Secret:
    def __init__(self, key: str, value: str) -> None:
        self.secretKey = key
        self.secretValue = value


def install_fake_sdk(monkeypatch, secrets: dict, fail: Exception | None = None) -> dict:
    """Install a fake `infisical_sdk` module; returns a record of the calls made to it."""
    record: dict = {}

    class _UniversalAuth:
        def login(self, client_id, client_secret):
            record["login"] = (client_id, client_secret)

    class _Auth:
        universal_auth = _UniversalAuth()

    class _Secrets:
        def list_secrets(self, project_id, environment_slug, secret_path):
            record["list_args"] = dict(
                project_id=project_id, environment_slug=environment_slug, secret_path=secret_path
            )
            if fail is not None:
                raise fail
            return types.SimpleNamespace(secrets=[_Secret(k, v) for k, v in secrets.items()])

    class InfisicalSDKClient:
        def __init__(self, host, token=None):
            record["client"] = dict(host=host, token=token)
            self.auth = _Auth()
            self.secrets = _Secrets()

    mod = types.ModuleType("infisical_sdk")
    mod.InfisicalSDKClient = InfisicalSDKClient
    monkeypatch.setitem(sys.modules, "infisical_sdk", mod)
    return record


def make_settings_cls(config: dict | None = None, source_kwargs: dict | None = None, **fields) -> type[BaseSettings]:
    """Build a Settings class wired to the Infisical source. Fields are `name=(type, default)`."""

    def customise(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        source = InfisicalSettingsSource(settings_cls, **(source_kwargs or {}))
        return (init_settings, source, env_settings, dotenv_settings)

    ns = {
        "__annotations__": {name: typ for name, (typ, _) in fields.items()},
        "model_config": SettingsConfigDict(**(BASE_CFG if config is None else config)),
        "settings_customise_sources": classmethod(customise),
        **{name: default for name, (_, default) in fields.items() if default is not ...},
    }
    return type("Settings", (BaseSettings,), ns)


def test_reads_secret_by_field_name(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    install_fake_sdk(monkeypatch, {"DB_HOST": "db.internal", "DB_PASSWORD": "s3cr3t"})
    s = make_settings_cls(DB_HOST=(str, ...), DB_PASSWORD=(str, ...))()
    assert s.DB_HOST == "db.internal"
    assert s.DB_PASSWORD == "s3cr3t"


def test_alias_choices_maps_to_first_string(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    install_fake_sdk(monkeypatch, {"POSTGRES--HOST": "aliased.internal"})
    alias = Field(validation_alias=AliasChoices("POSTGRES--HOST", "DB_HOST"))
    assert make_settings_cls(db_host=(str, alias))().db_host == "aliased.internal"


def test_complex_field_is_json_decoded(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    install_fake_sdk(monkeypatch, {"HOSTS": '["a", "b"]', "LIMITS": '{"rps": 10}'})
    s = make_settings_cls(HOSTS=(list[str], ...), LIMITS=(dict[str, int], ...))()
    assert s.HOSTS == ["a", "b"]
    assert s.LIMITS == {"rps": 10}


def test_missing_secret_falls_through_to_env(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    monkeypatch.setenv("DB_HOST", "from-env")
    install_fake_sdk(monkeypatch, {})  # Infisical returns nothing
    assert make_settings_cls(DB_HOST=(str, ...))().DB_HOST == "from-env"


def test_error_is_non_fatal_by_default(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    monkeypatch.setenv("DB_HOST", "from-env")
    install_fake_sdk(monkeypatch, {}, fail=RuntimeError("boom"))
    assert make_settings_cls(DB_HOST=(str, ...))().DB_HOST == "from-env"  # error swallowed


def test_raise_on_error(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    install_fake_sdk(monkeypatch, {}, fail=RuntimeError("boom"))
    Settings = make_settings_cls(source_kwargs={"raise_on_error": True}, DB_HOST=(str, "x"))
    with pytest.raises(RuntimeError, match="boom"):
        Settings()


def test_machine_identity_login(monkeypatch):
    monkeypatch.delenv("INFISICAL_TOKEN", raising=False)
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "csecret")
    record = install_fake_sdk(monkeypatch, {"K": "v"})
    assert make_settings_cls(K=(str, ...))().K == "v"
    assert record["login"] == ("cid", "csecret")
    assert record["client"]["token"] is None


def test_kwarg_and_config_precedence(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "dev")  # lowest priority
    record = install_fake_sdk(monkeypatch, {"K": "v"})
    make_settings_cls(
        {**BASE_CFG, "infisical_environment": "staging"},
        source_kwargs={"environment": "prod"},  # explicit kwarg beats model_config beats env
        K=(str, "x"),
    )()
    assert record["list_args"]["environment_slug"] == "prod"


def test_no_project_id_is_noop(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "tok")
    monkeypatch.delenv("INFISICAL_PROJECT_ID", raising=False)
    monkeypatch.setenv("DB_HOST", "from-env")
    install_fake_sdk(monkeypatch, {"DB_HOST": "should-not-be-used"})
    assert make_settings_cls({}, DB_HOST=(str, "x"))().DB_HOST == "from-env"
