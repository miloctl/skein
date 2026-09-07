"""Deployment grants are bounded, credential-free inventory documents."""

import json

import pytest


@pytest.mark.parametrize(
    "value",
    [
        [{"repository": "octocat/Hello-World", "hook_id": 1}],
        [{"repository": "internal/project", "hook_id": 9223372036854775807}],
    ],
)
def test_inventory_inline_and_file_use_identical_validation(monkeypatch, tmp_path, value):
    from app import config

    monkeypatch.setenv("SKEIN_GITHUB_HOOKS", json.dumps(value))
    api, inline, error = config._github_settings()
    assert not error
    assert inline[0]["repository"] == value[0]["repository"].lower()
    path = tmp_path / "hooks.yaml"
    import yaml

    path.write_text(yaml.safe_dump(value))
    monkeypatch.setenv("SKEIN_GITHUB_HOOKS", "")
    monkeypatch.setenv("SKEIN_GITHUB_HOOKS_FILE", str(path))
    assert config._github_settings() == (api, inline, "")


@pytest.mark.parametrize(
    "value",
    [
        [{"repository": "owner/repo", "hook_id": True}],
        [{"repository": "owner/repo", "hook_id": 0}],
        [{"repository": "owner/repo", "hook_id": 2**63}],
        [{"repository": "owner/repo", "hook_id": "1"}],
        [{"repository": "owner/../secret", "hook_id": 1}],
        [{"repository": "owner/..", "hook_id": 1}],
        [{"repository": "owner/repo", "hook_id": 1, "token": "fixture-private"}],
        [{"repository": "owner/repo", "hook_id": 1}] * 2,
        [{"repository": "owner/repo", "hook_id": i + 1} for i in range(33)],
        {"owner/repo": 1},
    ],
)
def test_bad_inventory_fails_closed_without_echoing_content(monkeypatch, value):
    from app import config

    monkeypatch.setenv("SKEIN_GITHUB_HOOKS", json.dumps(value))
    _, hooks, error = config._github_settings()
    assert error and hooks == []
    assert "fixture-private" not in error and "owner" not in error


@pytest.mark.parametrize(
    "api",
    [
        "http://api.github.com",
        "https://token@api.github.com",
        "https://@api.github.com",
        "https://api.github.com?token=fixture-private",
        "https://api.github.com/#fragment",
        "https://api.github.com/api/../evil",
        "https://api.github.com:invalid",
        "https://api.github.com\\evil",
    ],
)
def test_api_destination_never_accepts_credentials_or_ambiguous_structure(monkeypatch, api):
    from app import config

    monkeypatch.setenv("SKEIN_GITHUB_API_URL", api)
    _, hooks, error = config._github_settings()
    assert error and not hooks
    assert "fixture-private" not in error and "token@" not in error


def test_enterprise_api_grants_only_an_explicit_https_base(monkeypatch):
    from app import config

    monkeypatch.setenv("SKEIN_GITHUB_API_URL", "https://git.corp.example/api/v3/")
    api, _, error = config._github_settings()
    assert not error and api == "https://git.corp.example/api/v3"


def test_ambiguous_duplicate_or_unreadable_inventory_keeps_rest_configuration_usable(
    monkeypatch, tmp_path
):
    from app import config

    monkeypatch.setenv(
        "SKEIN_GITHUB_HOOKS", '[{"repository":"owner/repo","hook_id":1,"hook_id":2}]'
    )
    assert config._github_settings()[2]
    monkeypatch.setenv("SKEIN_GITHUB_HOOKS", "[]")
    monkeypatch.setenv("SKEIN_GITHUB_HOOKS_FILE", str(tmp_path / "private-name"))
    assert config._github_settings()[2]
    monkeypatch.setenv("SKEIN_GITHUB_HOOKS", "")
    error = config._github_settings()[2]
    assert error and "private-name" not in error
