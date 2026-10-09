from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memoria.config import Settings
from memoria.skills import SkillCatalog


def test_layer_configuration_loads_defaults_and_inherited_overrides(tmp_path: Path):
    parent = tmp_path / "parent.toml"
    parent.write_text('[memory.layers]\nepisode_ttl_days = 30\nsemantic_chars = 1500\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text('extends = "parent.toml"\n[memory.layers]\nepisodic_enabled = false\n', encoding="utf-8")
    settings = Settings.load(config)
    assert settings.memory_layers.episode_ttl_days == 30
    assert settings.memory_layers.semantic_chars == 1500
    assert settings.memory_layers.retrieval_max_calls == 6
    assert settings.public_dict()["memory_layers"]["episodic_enabled"] is False
    config.write_text('[memory.layers]\nepisode_top_k = true\n', encoding="utf-8")
    with pytest.raises(ValueError, match="整数"):
        Settings.load(config)


def test_procedural_revision_tracks_full_file_including_metadata(tmp_path: Path):
    directory = tmp_path / "deploy"
    directory.mkdir()
    path = directory / "SKILL.md"
    first = '---\nname: deploy\ndescription: 部署步骤\n---\n核对路径，再发布。\n'
    path.write_text(first, encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    skill = catalog.get("deploy")
    assert skill.revision == hashlib.sha256(first.encode("utf-8")).hexdigest()
    assert skill.public_dict()["revision"] == skill.revision
    path.write_text(first.replace("部署步骤", "受审部署步骤"), encoding="utf-8")
    catalog.reload()
    assert catalog.get("deploy").body == skill.body
    assert catalog.get("deploy").revision != skill.revision
