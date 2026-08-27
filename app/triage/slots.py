"""槽位抽取：从用户消息中抽取健康分诊所需的关键信息。

规则层（本模块）：正则/词表抽取物种、年龄、症状、持续时间、误食等。
LLM 层（pipeline 中可选）：复杂表述的槽位补充。

场景必填槽位表（供 D3 评分与追问生成）：
    呕吐类   → frequency, duration, appetite, mental_state, possible_ingestion
    误食类   → species, weight_kg, ingested_item, ingested_amount, time_since, symptoms
    泌尿类   → species, urination, abdomen, duration
    通用健康 → species, age, symptom, duration, appetite, mental_state
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.triage_schemas import PetProfile

# 物种
_SPECIES = {"猫": "cat", "猫咪": "cat", "小猫": "cat", "英短": "cat", "布偶": "cat",
            "狗": "dog", "犬": "dog", "金毛": "dog", "泰迪": "dog", "柯基": "dog", "幼犬": "dog"}

# 年龄段
_AGE = {"幼猫": "juvenile", "小猫": "juvenile", "幼犬": "juvenile", "小狗": "juvenile",
        "老年": "senior", "老了": "senior", "成猫": "adult", "成犬": "adult"}

# 症状关键词 → 症状槽位值
_SYMPTOMS = {
    "vomiting": ["呕吐", "吐了", "吐了两次", "干呕"],
    "diarrhea": ["拉稀", "腹泻", "软便", "便溏"],
    "anorexia": ["不吃", "不进食", "食欲下降", "挑食", "不碰"],
    "lethargy": ["没精神", "精神萎靡", "蔫", "精神不好", "不爱动"],
    "dyspnea": ["喘", "呼吸困难", "张口呼吸", "呼吸急促"],
    "urinary": ["尿不出来", "尿闭", "排尿困难", "蹲猫砂"],
    "seizure": ["抽搐", "痉挛", "抖动"],
    "bleeding": ["流血", "出血", "便血"],
    "abdominal": ["肚子胀", "腹胀", "肚子痛"],
}

# 误食检测
_INGESTION = re.compile(r"(吃了|误食|吞了|偷吃了|舔了|啃了)(半块|一块|一些|若干|[一二三四五六七八九十\d]+\s*(克|g|片|颗|块|根))?\s*(巧克力|百合|木糖醇|洋葱|葡萄|葡萄干|药|袜子|玩具|线|老鼠药|消毒液|布洛芬|扑热息痛|蒙脱石散)?")

# 持续时间
_DURATION = re.compile(r"((?:[一二三四五六七八九十\d]+)\s*(?:天|小时|周|日)|今天|昨天|前天|上周|刚才|早上)")
_FREQUENCY = re.compile(r"((?:[一二三四五六七八九十\d]+)\s*次)")

# 体重
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:公斤|千克|kg|斤)")

SLOT_LABELS = {
    "species": "宠物是猫还是狗", "age": "年龄段（幼年/成年/老年）",
    "weight_kg": "体重（公斤）", "symptom": "主要症状", "duration": "症状持续多久了",
    "frequency": "发作频率（如一天几次）", "appetite": "最近食欲如何",
    "mental_state": "精神状态怎么样", "possible_ingestion": "有没有可能误食异物或毒物",
    "ingested_item": "误食了什么", "ingested_amount": "吃了多少",
    "time_since": "吃了多久了", "current_symptoms": "现在有什么表现",
    "urination": "排尿情况", "abdomen": "腹部状态",
}


@dataclass
class SlotResult:
    slots: dict = field(default_factory=dict)
    pet: PetProfile = field(default_factory=PetProfile)

    @property
    def missing_questions(self) -> list[dict]:
        """缺失槽位 → 追问问题（ask_user 素材）。返回所有未抽取到的槽位。"""
        return [
            {"slot": k, "question": f"请问{v}？"}
            for k, v in SLOT_LABELS.items()
            if k not in self.slots
        ]


_REQUIRED_SLOTS: dict[str, list[str]] = {
    # 症状场景 → 必填槽位
    "vomiting": ["species", "age", "symptom", "frequency", "duration", "appetite", "mental_state", "possible_ingestion"],
    "diarrhea": ["species", "age", "symptom", "duration", "frequency", "appetite", "mental_state"],
    "anorexia": ["species", "age", "symptom", "duration", "mental_state"],
    "dyspnea": ["species", "symptom", "duration"],
    "urinary": ["species", "symptom", "duration", "urination", "abdomen"],
    "default": ["species", "age", "symptom", "duration", "appetite", "mental_state"],
}


def required_slots_for(symptoms: list[str]) -> list[str]:
    """根据检出的症状确定该场景的必填槽位集合。"""
    for s in ["dyspnea", "urinary", "vomiting", "diarrhea", "anorexia"]:
        if s in symptoms:
            return _REQUIRED_SLOTS[s]
    return _REQUIRED_SLOTS["default"]


def extract(message: str) -> SlotResult:
    """规则层槽位抽取。"""
    result = SlotResult()

    for term, sp in _SPECIES.items():
        if term in message:
            result.slots["species"] = sp
            result.pet.species = sp
            break
    for term, age in _AGE.items():
        if term in message:
            result.slots["age"] = age
            result.pet.age = age
            break

    symptoms = []
    for sym, terms in _SYMPTOMS.items():
        if any(t in message for t in terms):
            symptoms.append(sym)
    if symptoms:
        result.slots["symptom"] = symptoms

    if m := _INGESTION.search(message):
        result.slots["possible_ingestion"] = True
        item = m.group(3)
        if item:
            result.slots["ingested_item"] = item
            if m.group(2):
                result.slots["ingested_amount"] = m.group(2)

    if m := _DURATION.search(message):
        result.slots["duration"] = m.group(0)
    if m := _FREQUENCY.search(message):
        result.slots["frequency"] = m.group(0)
    if m := _WEIGHT.search(message):
        result.slots["weight_kg"] = float(m.group(1))
        result.pet.weight_kg = float(m.group(1))

    for key, terms in {
        "appetite": ["不吃", "食欲", "挑食", "能吃"],
        "mental_state": ["没精神", "蔫", "精神", "活蹦乱跳", "能玩"],
        "urination": ["尿", "猫砂"],
        "abdomen": ["肚子", "腹胀"],
    }.items():
        if any(t in message for t in terms):
            result.slots[key] = True

    # 记录该场景的必填槽位（供追问与 D3 评分）
    result.slots["_required"] = required_slots_for(symptoms)
    return result
