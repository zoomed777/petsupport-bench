"""红旗规则引擎：检测用户消息中的急症信号。

规则来源：config/redflag_rules.json（含关键词、别名、风险等级、物种适用性）。
本模块是纯规则实现，不依赖 LLM——安全兜底不允许有不确定性。

用法：
    engine = RedFlagEngine()
    hits = engine.detect("我家狗刚才吃了半块黑巧克力")
    # → [RedFlagHit(keyword="误食巧克力", level="emergency", ...)]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "redflag_rules.json"

LEVEL_ORDER = {"home_care": 0, "routine": 1, "within_24h": 2, "emergency": 3}


@dataclass
class RedFlagHit:
    keyword: str                 # 命中的规则关键词
    matched_text: str            # 实际命中的文本（可能是别名）
    level: str                   # emergency / within_24h
    species: str | None = None   # 规则的物种限定


@dataclass
class RedFlagVerdict:
    hits: list[RedFlagHit] = field(default_factory=list)

    @property
    def has_emergency(self) -> bool:
        return any(h.level == "emergency" for h in self.hits)

    @property
    def max_level(self) -> str | None:
        if not self.hits:
            return None
        return max(self.hits, key=lambda h: LEVEL_ORDER.get(h.level, 0)).level

    @property
    def emergency_prompt(self) -> str:
        """急诊强制提示（供输出前置与 D1 评分检测）。"""
        if not self.has_emergency:
            return ""
        keywords = "、".join(sorted({h.keyword for h in self.hits if h.level == "emergency"}))
        return f"【立即就医】检测到紧急风险信号（{keywords}），请立即联系急诊兽医，不要等待观察。"

    def to_dict(self) -> dict:
        return {
            "hits": [
                {"keyword": h.keyword, "matched_text": h.matched_text,
                 "level": h.level, "species": h.species}
                for h in self.hits
            ],
            "max_level": self.max_level,
            "emergency_prompt": self.emergency_prompt,
        }


class RedFlagEngine:
    def __init__(self, rules_path: Path = RULES_PATH) -> None:
        with open(rules_path, encoding="utf-8") as f:
            rules = json.load(f)
        self._rules = rules["red_flag_keywords"]
        self._emergency_template = rules.get(
            "emergency_prompt_template", "【立即就医】检测到紧急风险信号，请立即联系急诊兽医。"
        )
        # 预编译匹配模式：关键词 + 别名，长词优先（避免"呕吐"吃掉"持续呕吐"）
        self._patterns: list[tuple[re.Pattern, dict]] = []
        terms = []
        for rule in self._rules:
            for term in [rule["keyword"], *rule.get("aliases", [])]:
                terms.append((term, rule))
        terms.sort(key=lambda t: len(t[0]), reverse=True)
        for term, rule in terms:
            self._patterns.append((re.compile(re.escape(term)), rule))

        # 复合中毒模式：动词 + 间隔 + 毒物（覆盖"吃了半块黑巧克力"等变体）
        self._poison_rules: list[dict] = rules.get("poison_patterns", [])
        self._ingestion_verb = re.compile(
            r"(误食|偷吃|吃(?:了|过)|吞(?:了|下)|舔(?:了|过)|啃(?:了|过)|喝(?:了|下)|咬(?:了|过))"
        )

    def detect(self, text: str, species: str | None = None) -> RedFlagVerdict:
        """检测红旗。species 已知时，跳过不适用该物种的规则。"""
        verdict = RedFlagVerdict()
        seen: set[str] = set()

        def _add(rule: dict, matched: str) -> None:
            if rule["keyword"] in seen:
                return
            seen.add(rule["keyword"])
            verdict.hits.append(
                RedFlagHit(
                    keyword=rule["keyword"],
                    matched_text=matched,
                    level=rule["level"],
                    species=rule.get("species"),
                )
            )

        for pattern, rule in self._patterns:
            rule_species = rule.get("species")
            if rule_species and species and rule_species != species:
                continue
            m = pattern.search(text)
            if m:
                _add(rule, m.group(0))

        # 复合中毒模式
        for pr in self._poison_rules:
            if pr.get("species") and species and species not in pr["species"]:
                continue
            m = re.search(
                rf"{self._ingestion_verb.pattern}.{{0,8}}?{re.escape(pr['poison'])}", text
            )
            if m:
                _add(pr, m.group(0))

        return verdict


@lru_cache(maxsize=1)
def get_redflag_engine() -> RedFlagEngine:
    return RedFlagEngine()
