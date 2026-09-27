import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.skills import SkillCatalog
from memoria.tools.http_get import host_allowed


def test_skill_catalog_loads_and_matches(tmp_path: Path):
    skill_dir = tmp_path / "weather"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: weather
description: Get weather
triggers: 天气, weather, forecast
---

# Weather
Use http_get.
""",
        encoding="utf-8",
    )
    catalog = SkillCatalog(tmp_path, max_inject=2)
    assert [item.name for item in catalog.list()] == ["weather"]
    matches = catalog.select("今天北京天气怎么样")
    assert matches and matches[0].skill.name == "weather"
    catalog_text, active = catalog.render_sections(matches)
    assert "weather" in catalog_text and "http_get" in active


def test_builtin_skills_directory_present():
    root = Path(__file__).resolve().parents[1] / "skills"
    catalog = SkillCatalog(root)
    names = {item.name for item in catalog.list()}
    assert {"summarize", "weather", "skill-creator", "memory-review"} <= names
    matches = catalog.select("帮我整理一下长期记忆里互相冲突的内容")
    assert any(item.skill.name == "memory-review" for item in matches)


def test_http_host_allowlist():
    assert host_allowed("wttr.in", ("wttr.in",))
    assert host_allowed("api.open-meteo.com", ("open-meteo.com",))
    assert not host_allowed("evil.example", ("wttr.in",))


def test_skills_api_and_load_tool(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skill = skills_root / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        """---
name: demo
description: demo skill
triggers: demo
---

# Demo
hello
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="x"
base_url="http://example.test/v1"
[agent.skills]
enabled=true
directory="{skills_root}"
[storage]
database="{tmp_path / 'skills.db'}"
''',
        encoding="utf-8",
    )
    app = create_app(config)
    with TestClient(app) as client:
        listed = client.get("/api/skills").json()
        assert listed and listed[0]["name"] == "demo"
        detail = client.get("/api/skills/demo").json()
        assert "hello" in detail["body"]
        overview = client.get("/api/overview").json()
        assert overview["skills"][0]["name"] == "demo"
        result = asyncio.run(app.state.service.runtime.tools.execute("load_skill", {"name": "demo"}))
        assert result.ok and "hello" in result.content
