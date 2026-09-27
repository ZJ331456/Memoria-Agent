from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]{2,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SkillRecord:
    name: str
    description: str
    body: str
    triggers: tuple[str, ...] = ()
    always: bool = False
    available: bool = True
    missing: str = ""
    root_dir: Path | None = None
    requires_bins: tuple[str, ...] = ()
    requires_env: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": list(self.triggers),
            "always": self.always,
            "available": self.available,
            "missing": self.missing,
            "body_chars": len(self.body),
        }


@dataclass(slots=True)
class SkillMatch:
    skill: SkillRecord
    score: float
    reason: str


@dataclass(slots=True)
class SkillCatalog:
    """Discover `skills/*/SKILL.md` and select relevant playbooks for a turn."""

    root: Path
    max_inject: int = 2
    max_body_chars: int = 4000
    _skills: dict[str, SkillRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> list[SkillRecord]:
        records: dict[str, SkillRecord] = {}
        if self.root.is_dir():
            for skill_dir in sorted(self.root.iterdir(), key=lambda item: item.name):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.is_file():
                    continue
                record = self._load_file(skill_dir.name, skill_file, skill_dir)
                records[record.name] = record
        self._skills = records
        return self.list()

    def list(self) -> list[SkillRecord]:
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str) -> SkillRecord | None:
        return self._skills.get(name)

    def catalog_text(self) -> str:
        lines = []
        for skill in self.list():
            status = "可用" if skill.available else f"不可用({skill.missing})"
            triggers = "、".join(skill.triggers[:8]) if skill.triggers else "（见 description）"
            lines.append(f"- `{skill.name}` [{status}] {skill.description} | 触发：{triggers}")
        return "\n".join(lines)

    def select(self, user_text: str, *, explicit: list[str] | None = None) -> list[SkillMatch]:
        selected: dict[str, SkillMatch] = {}
        for skill in self.list():
            if skill.always and skill.available:
                selected[skill.name] = SkillMatch(skill, 100.0, "always")
        for name in explicit or []:
            skill = self.get(name)
            if skill and skill.available:
                selected[skill.name] = SkillMatch(skill, 90.0, "explicit")
        scored: list[SkillMatch] = []
        for skill in self.list():
            if not skill.available or skill.name in selected:
                continue
            score, reason = self._score(skill, user_text)
            if score > 0:
                scored.append(SkillMatch(skill, score, reason))
        scored.sort(key=lambda item: (-item.score, item.skill.name))
        for match in scored:
            if len([item for item in selected.values() if item.reason != "always"]) >= self.max_inject:
                break
            if match.reason == "always":
                continue
            selected[match.skill.name] = match
        # keep always + top matches, prefer higher score
        ordered = sorted(selected.values(), key=lambda item: (-item.score, item.skill.name))
        return ordered

    def render_sections(self, matches: list[SkillMatch]) -> tuple[str, str]:
        catalog = self.catalog_text()
        if not matches:
            return catalog, ""
        parts = []
        for match in matches:
            body = match.skill.body.strip()
            if len(body) > self.max_body_chars:
                body = body[: self.max_body_chars] + "\n\n[技能正文已截断]"
            parts.append(
                f"### skill:{match.skill.name} (score={match.score:.1f}, {match.reason})\n{body}"
            )
        return catalog, "\n\n".join(parts)

    def _load_file(self, fallback_name: str, path: Path, root_dir: Path) -> SkillRecord:
        content = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(content)
        name = str(meta.get("name") or fallback_name).strip() or fallback_name
        description = str(meta.get("description") or name).strip()
        triggers = self._as_triggers(meta.get("triggers"), description)
        config = self._parse_metadata(meta.get("metadata"))
        memoria = config.get("memoria") if isinstance(config.get("memoria"), dict) else {}
        akashic = config.get("akashic") if isinstance(config.get("akashic"), dict) else {}
        nested = memoria or akashic
        requires = nested.get("requires") if isinstance(nested.get("requires"), dict) else {}
        bins = tuple(str(item) for item in (requires.get("bins") or []) if str(item).strip())
        envs = tuple(str(item) for item in (requires.get("env") or []) if str(item).strip())
        always = self._as_bool(meta.get("always")) or self._as_bool(nested.get("always"))
        missing_parts = []
        for binary in bins:
            if shutil.which(binary) is None:
                missing_parts.append(f"bin:{binary}")
        for env_name in envs:
            if not os.getenv(env_name):
                missing_parts.append(f"env:{env_name}")
        missing = ",".join(missing_parts)
        return SkillRecord(
            name=name,
            description=description,
            body=body.strip(),
            triggers=triggers,
            always=always,
            available=not missing,
            missing=missing,
            root_dir=root_dir,
            requires_bins=bins,
            requires_env=envs,
        )

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        match = _FRONTMATTER.match(content)
        if not match:
            return {}, content.strip()
        meta: dict[str, Any] = {}
        for raw_line in match.group(1).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
        return meta, match.group(2).strip()

    @staticmethod
    def _parse_metadata(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    _STOPWORDS = {
        "the", "and", "for", "with", "from", "this", "that", "when", "use", "using",
        "技能", "触发", "用户", "时候", "以及", "或者", "一个", "进行", "通过", "可以",
    }

    @classmethod
    def _as_triggers(cls, value: Any, description: str) -> tuple[str, ...]:
        items: list[str] = []
        if isinstance(value, (list, tuple)):
            items.extend(str(item).strip() for item in value)
        elif value:
            items.extend(part.strip() for part in re.split(r"[,，|/]", str(value)) if part.strip())
        if not items:
            for token in _TOKEN.findall(description.lower()):
                if token in cls._STOPWORDS or len(token) < 2:
                    continue
                items.append(token)
        seen: set[str] = set()
        result = []
        for item in items:
            key = item.lower()
            if key in seen or key in cls._STOPWORDS:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= 24:
                break
        return tuple(result)

    @staticmethod
    def _score(skill: SkillRecord, user_text: str) -> tuple[float, str]:
        text = user_text.lower()
        if not text.strip():
            return 0.0, ""
        score = 0.0
        hits = []
        for trigger in skill.triggers:
            needle = trigger.lower()
            if len(needle) < 2:
                continue
            if needle in text:
                weight = 3.0 if len(needle) >= 4 else 1.5
                score += weight
                hits.append(trigger)
                continue
            # Chinese compound soft match: require most bigrams of the trigger.
            if len(needle) >= 4 and any("\u4e00" <= ch <= "\u9fff" for ch in needle):
                grams = [needle[i : i + 2] for i in range(len(needle) - 1)]
                hit = sum(1 for gram in grams if gram in text)
                if grams and hit >= max(2, (len(grams) + 1) // 2):
                    score += 1.0 + hit / len(grams)
                    hits.append(trigger)
        if skill.name.replace("-", " ") in text or skill.name in text:
            score += 4.0
            hits.append(skill.name)
        if score <= 0:
            return 0.0, ""
        return score, "triggers:" + ",".join(hits[:6])
