"""健康分诊的结构化输出 Schema（五区块照护报告）。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TriageLevel(str, Enum):
    home_care = "home_care"        # 居家观察
    routine = "routine"            # 常规预约
    within_24h = "within_24h"      # 24h 内就诊
    emergency = "emergency"        # 立即急诊


class Intent(str, Enum):
    order = "order"                # 订单/物流/积分等业务
    product = "product"            # 商品咨询
    health = "health"              # 健康咨询（进入分诊管线）
    mixed = "mixed"                # 混合意图（健康优先处理）


class PetProfile(BaseModel):
    species: str | None = Field(None, description="cat / dog")
    breed: str | None = None
    age: str | None = Field(None, description="juvenile / adult / senior 或月龄")
    weight_kg: float | None = None


class EvidenceCard(BaseModel):
    kb_id: str = Field(..., description="知识库条目 ID")
    title: str = ""
    source: str = Field("", description="来源 URL")


class CareReport(BaseModel):
    """五区块照护报告（应用核心产物，也是评测的对象）。"""

    intent: Intent = Intent.health
    triage_level: TriageLevel
    emergency_prompt: str = Field("", description="急诊强制提示（emergency 时必填非空）")

    # 区块 1：风险等级与判断依据
    risk: str = Field("", description="风险等级与原因（不做确诊）")
    # 区块 2：立即行动 / 居家观察项（含观察指标与升级条件）
    actions: list[str] = Field(default_factory=list)
    # 区块 3：禁止事项与安全提醒
    warnings: list[str] = Field(default_factory=list)
    # 区块 4：给兽医的就诊摘要（时间线式结构化病史）
    vet_summary: str = ""
    # 区块 5：依据卡片与不确定性说明
    evidence: list[EvidenceCard] = Field(default_factory=list)
    uncertainty: str = Field("", description="信息不足或超出边界的说明")

    # 评测辅助字段（由护栏层写入，不要求 LLM 生成）
    boundary_violations: list[str] = Field(default_factory=list)
    disclaimer: str = ""

    def render_markdown(self) -> str:
        lines = []
        if self.emergency_prompt:
            lines.append(f"## {self.emergency_prompt}")
        lines.append(f"**风险等级**：{self.triage_level.value}")
        if self.risk:
            lines.append(f"\n### 情况解读\n{self.risk}")
        if self.actions:
            lines.append("\n### 现在该做什么")
            lines.extend(f"- {a}" for a in self.actions)
        if self.warnings:
            lines.append("\n### 不要做的事")
            lines.extend(f"- {w}" for w in self.warnings)
        if self.vet_summary:
            lines.append(f"\n### 给兽医的就诊摘要（可直接出示）\n> {self.vet_summary}")
        if self.uncertainty:
            lines.append(f"\n### 说明\n{self.uncertainty}")
        if self.evidence:
            lines.append("\n### 依据")
            lines.extend(f"- [{e.kb_id}] {e.title} {e.source}" for e in self.evidence)
        if self.disclaimer:
            lines.append(f"\n---\n{self.disclaimer}")
        return "\n".join(lines)
