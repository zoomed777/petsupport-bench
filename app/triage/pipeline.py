"""健康分诊管线：把路由、红旗、槽位、生成、护栏串成一条链。

两条路径：
    急诊路径（红旗命中）：规则引擎直接产出急诊报告（不等待追问），
        LLM 仅增强文案；ask_user 不阻塞就医建议。
    常规路径：槽位抽取 → 缺失槽位追问（≤3 轮）→ LLM/RAG 生成五区块报告。

LLM 集成点（Hy3 端点就绪后接入，均为可选增强）：
    - router.classify_with_fallback 的 llm_classify
    - 本模块 generate_report 的 llm_generate
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.guardrails.redflag import RedFlagEngine, get_redflag_engine
from app.guardrails.redlines import check_output, get_default_disclaimer
from app.models.triage_schemas import CareReport, EvidenceCard, Intent, TriageLevel
from app.triage import router as intent_router
from app.triage import slots as slot_extractor

KB_PATH = Path(__file__).resolve().parents[2] / "data" / "knowledge_base.jsonl"


def load_kb() -> dict[str, dict]:
    entries = {}
    with open(KB_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                entries[entry["kb_id"]] = entry
    return entries


@dataclass
class PipelineResult:
    report: CareReport | None = None
    ask_questions: list[dict] = field(default_factory=list)   # 需要追问的问题
    route: intent_router.RouteVerdict | None = None
    redflag: dict = field(default_factory=dict)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.ask_questions)


class TriagePipeline:
    def __init__(self, redflag_engine: RedFlagEngine | None = None,
                 llm_classify=None, llm_generate=None) -> None:
        self.engine = redflag_engine or get_redflag_engine()
        self.kb = load_kb()
        self.llm_classify = llm_classify       # callable(message) -> intent str
        self.llm_generate = llm_generate       # callable(context) -> dict(五区块)

    def run(self, message: str, species: str | None = None) -> PipelineResult:
        """主入口。species 可来自宠物档案（已知则跳过追问）。"""
        result = PipelineResult()
        result.route = intent_router.classify_with_fallback(message, self.llm_classify)

        if not result.route.needs_health_pipeline:
            return result  # 纯订单/商品：走原 ReAct 工具链，不产出 CareReport

        # 1. 红旗检测（规则层，最高优先级）
        verdict = self.engine.detect(message, species=species)
        result.redflag = verdict.to_dict()

        if verdict.has_emergency:
            result.report = self._emergency_report(message, verdict, species)
            return result  # 急诊：立即给出报告，不追问

        # 2. 槽位抽取（规则层）
        slot_result = slot_extractor.extract(message)
        required = slot_result.slots.get("_required", [])
        missing = [q for q in slot_result.missing_questions if q["slot"] in required]
        if species:  # 档案已知物种，从缺失列表剔除
            missing = [q for q in missing if q["slot"] != "species"]

        # 3. 关键槽位缺失 → 追问（最多 3 个问题）
        if missing:
            result.ask_questions = missing[:3]
            return result

        # 4. 信息足够 → 生成报告
        result.report = self._generate_report(message, slot_result)
        return result

    # ------------------------------------------------------------------

    def _emergency_report(self, message: str, verdict, species: str | None) -> CareReport:
        """急诊路径：规则直接产出，文案可由 LLM 增强。"""
        flags = [h.keyword for h in verdict.hits if h.level == "emergency"]
        evidence = self._find_evidence(flags, species)

        report = CareReport(
            intent=Intent.mixed if verdict else Intent.health,
            triage_level=TriageLevel.emergency,
            emergency_prompt=verdict.emergency_prompt,
            risk=f"检测到紧急风险信号：{'、'.join(flags)}。这类情况的处置窗口很短，"
                 "症状（如呕吐、精神变化）可能延迟出现，\"目前看起来没事\"不能排除风险。",
            actions=[
                "立即致电急诊/24h宠物医院，说明情况并按指导行动",
                "保留误食物的包装或残留物，就诊时带给兽医",
                "记录事发时间、摄入量与已出现的任何异常",
                "转运时保持安静，减少应激",
            ],
            warnings=[
                "不要自行催吐——除非兽医电话中明确指导",
                "不要喂任何人用药物",
                "不要因\"看起来精神\"而选择在家观察",
            ],
            vet_summary=f"主诉：{message.strip()[:200]}",
            evidence=evidence,
            uncertainty="本判断基于风险信号规则库；最终处置以急诊兽医意见为准。",
            disclaimer=get_default_disclaimer(),
        )

        # LLM 增强（可选）：只允许润色 risk/vet_summary，不允许改变 triage_level
        if self.llm_generate:
            try:
                enhanced = self.llm_generate({"path": "emergency", "report": report.model_dump()})
                for k in ("risk", "vet_summary"):
                    if enhanced.get(k):
                        setattr(report, k, enhanced[k])
            except Exception:  # noqa: BLE001
                pass

        # 护栏终检：急诊报告也不允许踩红线
        self._apply_guardrails(report)
        return report

    def _generate_report(self, message: str, slot_result) -> CareReport:
        """常规路径：模板兜底 + LLM 增强。"""
        symptoms = slot_result.slots.get("symptom", [])
        level = self._estimate_level(symptoms)
        evidence = self._find_evidence(symptoms, slot_result.slots.get("species"))

        report = CareReport(
            triage_level=level,
            risk=f"根据描述的症状（{self._symptom_names(symptoms)}），结合宠物档案综合判断。",
            actions=self._template_actions(symptoms),
            warnings=["不要自行喂人用药物", "观察期间保证充足饮水"],
            vet_summary=f"主诉：{message.strip()[:200]}",
            evidence=evidence,
            uncertainty="如症状持续或加重，请及时就诊。",
            disclaimer=get_default_disclaimer(),
        )

        if self.llm_generate:
            try:
                enhanced = self.llm_generate(
                    {"path": "routine", "slots": slot_result.slots,
                     "report": report.model_dump()}
                )
                if isinstance(enhanced, dict):
                    for k in ("risk", "actions", "warnings", "vet_summary", "uncertainty"):
                        if enhanced.get(k):
                            setattr(report, k, enhanced[k])
            except Exception:  # noqa: BLE001
                pass

        self._apply_guardrails(report)
        return report

    # ------------------------------------------------------------------

    def _apply_guardrails(self, report: CareReport) -> None:
        """输出护栏：红线校验 + 免责声明。红线命中不删除内容（评测需要看到违规），只标记。"""
        text = report.render_markdown()
        verdict = check_output(text)
        report.boundary_violations = verdict.violations
        if not report.disclaimer:
            report.disclaimer = get_default_disclaimer()

    def _estimate_level(self, symptoms: list[str]) -> TriageLevel:
        if any(s in ("dyspnea", "seizure", "bleeding") for s in symptoms):
            return TriageLevel.emergency
        if "vomiting" in symptoms or "urinary" in symptoms:
            return TriageLevel.within_24h
        if "anorexia" in symptoms or "lethargy" in symptoms:
            return TriageLevel.within_24h
        return TriageLevel.home_care

    def _find_evidence(self, keys: list[str], species: str | None) -> list[EvidenceCard]:
        """根据症状/红旗关键词匹配知识库条目。"""
        mapping = {
            "误食巧克力": "KB-TOX-CHOC-001", "误食百合": "KB-TOX-LILY-001",
            "误食木糖醇": "KB-TOXYLITOL-001", "呼吸困难": "KB-RESP-001",
            "尿闭": "KB-URO-001", "误食扑热息痛": "KB-DRUG-001",
            "vomiting": "KB-VOMIT-001", "diarrhea": "KB-STOOL-001",
            "anorexia": "KB-FEED-001",
        }
        cards = []
        for k in keys:
            kb_id = mapping.get(k)
            entry = self.kb.get(kb_id) if kb_id else None
            if entry:
                if entry.get("species") and species and species not in entry["species"]:
                    continue
                cards.append(EvidenceCard(kb_id=kb_id, title=entry.get("title", ""), source=entry.get("source", "")))
        return cards

    @staticmethod
    def _symptom_names(symptoms: list[str]) -> str:
        names = {"vomiting": "呕吐", "diarrhea": "腹泻/软便", "anorexia": "食欲下降",
                 "lethargy": "精神不振", "dyspnea": "呼吸异常", "urinary": "排尿异常",
                 "seizure": "抽搐", "bleeding": "出血", "abdominal": "腹部胀痛"}
        return "、".join(names.get(s, s) for s in symptoms) or "描述的症状"

    @staticmethod
    def _template_actions(symptoms: list[str]) -> list[str]:
        actions = ["记录症状出现的时间、频率与变化"]
        if "vomiting" in symptoms:
            actions += ["记录每次呕吐的时间与内容物（食物/胆汁/血丝）",
                        "禁食观察 4-6 小时但保证饮水"]
        if "diarrhea" in symptoms:
            actions += ["记录排便次数与性状（软便/水样/带血/黏液）"]
        if "anorexia" in symptoms:
            actions += ["尝试加热罐头提升食欲，观察是否进食"]
        actions.append("若 24 小时内无改善或出现加重，预约就诊")
        return actions
