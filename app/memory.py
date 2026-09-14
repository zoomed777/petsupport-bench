"""Bounded, source-backed local conversation memory (no model-written facts)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import re
from uuid import uuid4

from app.triage.slots import extract, detect_symptoms

PROFILE_KEYS = {'species', 'age', 'weight_kg'}
MAX_CONTEXT_UNITS = 10000
MAX_MESSAGE_UNITS = 6000


def units(text: str) -> int:
    """Conservative UTF-8 byte proxy, NOT the provider's tokenizer count."""
    return len(text.encode('utf-8'))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Pet:
    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ''
    species: str | None = None
    profile: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    rounds: int = 0
    risks: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    folded_turns: int = 0
    event_id: str = field(default_factory=lambda: uuid4().hex[:12])

    def reset_event(self):
        self.facts, self.sources, self.recent, self.pending, self.risks = {}, {}, [], [], []
        self.rounds = 0
        self.folded_turns = 0
        self.event_id = uuid4().hex[:12]

    def put(self, key, value, quote):
        if key.startswith('_') or value is None or value is False:
            return
        target = self.profile if key in PROFILE_KEYS else self.facts
        old = target.get(key)
        old_value = old.get('value') if key in PROFILE_KEYS and isinstance(old, dict) else old
        if old_value is not None and old_value != value:
            self.changes.append({'field': key, 'old': old_value, 'new': value, 'quote': quote[:180], 'at': now()})
            self.changes = self.changes[-30:]
        if key in PROFILE_KEYS:
            target[key] = {'value': value, 'quote': quote[:180], 'updated_at': now()}
            if key == 'species':
                self.species = value
        else:
            target[key] = value
            self.sources[key] = quote[:180]

    def known_profile(self):
        result = {}
        for key, entry in self.profile.items():
            if key not in PROFILE_KEYS:
                continue
            # Age and weight are not timeless. Stale values require reconfirmation.
            try:
                days = (datetime.now(timezone.utc) - datetime.fromisoformat(entry['updated_at'])).days
            except (ValueError, KeyError, TypeError):
                days = 999
            if key == 'species' or days <= 30:
                result[key] = entry['value']
        return result

    def absorb(self, text):
        parsed = extract(text).slots
        correction = bool(re.search(r'说错|更正|不是|改成|实际|应该是', text))
        # Parse only the corrected side; superseded text is never replayed as fact.
        effective = re.split(r'不是[^，,。；;]*[，,](?:而?是)?', text)[-1] if '不是' in text else text
        if correction:
            parsed = extract(effective).slots
            negative = re.search(r'不是([^，,。；;]+)[，,](?:而?是)?',text)
            if negative and detect_symptoms(negative.group(1)):
                parsed['symptom'] = detect_symptoms(effective) or [effective[:80]]
        for key, value in parsed.items():
            if key not in {'species', 'age', 'weight_kg'} and not key.startswith('_'):
                if key in {'appetite','mental_state','urination','abdomen'}:
                    patterns = {
                        'appetite':r'食欲(?:还可以|不佳|不好|下降|正常|很好|差|好)|胃口(?:不好|正常|不佳)|没胃口|不吃不喝|不吃|挑食|能吃',
                        'mental_state':r'精神(?:还可以|还好|萎靡|不好|正常|很好|一般|差|好)|没精神|蔫|活蹦乱跳|能玩|不爱动',
                        'urination':r'尿不出来|尿闭|排尿困难|排尿正常|尿量(?:正常|少|多)',
                        'abdomen':r'肚子(?:胀|痛|硬)|腹胀|腹部(?:正常|疼痛|肿胀)',
                    }
                    matches = re.findall(patterns[key],effective)
                    if not matches:
                        continue
                    value = matches[-1]
                if key == 'symptom' and not correction:
                    value = list(dict.fromkeys(self.facts.get('symptom', []) + value))
                self.put(key, value, effective)
        ages = re.findall(r'(?:[一二两三四五六七八九十\d]+(?:\.\d+)?\s*岁|\d+\s*个月(?:大)?|成年|幼年|老年)', effective)
        if ages:
            self.put('age', ages[-1], effective)
        weights = re.findall(r'(\d+(?:\.\d+)?)\s*(公斤|千克|kg|斤)', effective)
        if weights:
            value, unit = weights[-1]
            weight = float(value) / (2 if unit == '斤' else 1)
            if 0 < weight <= 150:
                self.put('weight_kg', weight, effective)
        for key, pattern in {
            'frequency': r'[一二两三四五六七八九十\d]+\s*次',
            'duration': r'[一二两三四五六七八九十\d]+\s*(?:天|小时|周)|今天|昨天|前天|刚才|早上',
        }.items():
            matches = re.findall(pattern, effective)
            if matches:
                self.put(key, matches[-1], effective)
        for key, pattern in {'age':r'年龄.*(?:不知道|不清楚)', 'weight_kg':r'体重.*(?:不知道|不清楚)'}.items():
            if re.search(pattern, effective):
                self.profile.pop(key, None)
        self.recent.append(text)
        while len(self.recent) > 6 or sum(units(t) for t in self.recent) > 8000:
            self.recent.pop(0)
            self.folded_turns += 1
        return effective

    def summary(self):
        # Extractive structured summary: no generated narrative, no retired values.
        return {'pet': self.name, 'profile': self.known_profile(), 'event': self.facts,
                'unresolved_risks': self.risks, 'pending': self.pending}

    def context(self, latest):
        names = {'cat': '猫', 'dog': '狗'}
        symptoms = {'vomiting':'呕吐','diarrhea':'拉稀','anorexia':'食欲下降','lethargy':'精神不好',
                    'dyspnea':'呼吸困难','urinary':'排尿困难','seizure':'抽搐','bleeding':'流血','abdominal':'肚子胀'}
        known = self.known_profile()
        fact_text = '\n'.join(f'{k}：{json.dumps(v,ensure_ascii=False)}' for k,v in self.facts.items())
        risk_text = '；'.join(self.risks)
        prefix = f'当前宠物：{self.name}（{names.get(self.species,"物种待确认")}）。\n'
        prefix += '已确认档案：'+json.dumps(known,ensure_ascii=False)+'\n'
        prefix += '本次事件：'+ '、'.join(symptoms.get(s,s) for s in self.facts.get('symptom',[]))+'\n'+fact_text+'\n'
        if risk_text:
            prefix += '尚未排除的风险：'+risk_text+'\n'
        context = prefix + '用户本轮描述：'+latest
        if units(context) > MAX_CONTEXT_UNITS:
            raise ValueError('ContextBudgetExceeded')
        return context


@dataclass
class Memory:
    pets: dict[str, Pet] = field(default_factory=dict)
    active_id: str | None = None
    pending_message: str = ''
    pending_species: str | None = None
    topic: str = 'health'

    @property
    def active(self):
        return self.pets.get(self.active_id)

    def select(self, text):
        """Return (pet, clarification, text); never guess ambiguous ownership."""
        mentioned = {sp for term,sp in [('猫','cat'),('狗','dog')] if re.search(term+r'(?!粮|砂|窝|笼|绳|罐|条|爬)',text)}
        matches = [p for p in self.pets.values() if p.name and p.name in text]
        correction = re.search(r'不是(猫|狗)[，,\s]*(?:而?是)(猫|狗)', text)
        if correction and self.active:
            pet = self.active
            sp = {'猫':'cat','狗':'dog'}[correction.group(2)]
            pet.put('species', sp, correction.group(0))
            return pet, '', text[correction.end():]
        if len(mentioned) > 1 or len(matches) > 1:
            self.pending_message = ''
            return None, '这条消息涉及多只宠物。请先说清一只宠物的名字和它的情况，我会分别记录，不把信息混在一起。', text
        named = re.search(r'(?:名字叫|名叫|叫)([\u4e00-\u9fffA-Za-z0-9]{1,10})(?=[，。！？、\s,.!?]|$)',text)
        name = named.group(1) if named else None
        # A symptom such as “一直叫” is not a pet name.
        if name in {'了','起来','不停'}:
            name = None
        sp = next(iter(mentioned), None)
        another = bool(re.search(r'另一只|第二只|另外一只|换一只', text))
        pet = matches[0] if matches else None
        if named and not pet:
            pet = next((p for p in self.pets.values() if p.name == name), None)
            if pet is None and self.active and self.active.name.startswith('未命名') and not another and (not sp or sp == self.active.species):
                pet = self.active
                pet.name = name
        if not pet and not name and not another:
            candidates = [p for p in self.pets.values() if p.species == sp] if sp else []
            if len(candidates) > 1:
                self.pending_message, self.pending_species = text, sp
                return None, '你说的是哪一只？'+ '、'.join(p.name for p in candidates)+'。请告诉我名字，我再继续。', text
            pet = candidates[0] if candidates else (self.active if not sp else None)
        if pet is None:
            if len(self.pets) >= 20:
                return None, '当前已保存20只宠物，请先在记忆管理中整理档案。', text
            sp = sp or self.pending_species
            pet = Pet(name=name or f'未命名{ {"cat":"猫","dog":"狗"}.get(sp,"宠物")}{len(self.pets)+1}', species=sp)
            self.pets[pet.id] = pet
        if sp and pet.species and sp != pet.species:
            return None, f'{pet.name}此前记录为另一物种。你是在更正档案，还是说另一只宠物？请明确说明，例如“不是猫，是狗”。', text
        if sp:
            pet.put('species', sp, text)
        elif pet.species and 'species' not in pet.profile:
            pet.put('species', pet.species, text)
        self.active_id = pet.id
        if self.pending_message:
            text = self.pending_message+'\n'+text
            self.pending_message, self.pending_species = '', None
        if re.search(r'新情况|新的问题|新事件|上次.*好了|重新开始健康咨询',text):
            pet.reset_event()
        return pet, '', text

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = set(Pet.__dataclass_fields__)
        pets = {k:Pet(**{f:v for f,v in p.items() if f in allowed}) for k,p in data.get('pets',{}).items()}
        return cls(pets=pets, active_id=data.get('active_id'), pending_message=data.get('pending_message',''),
                   pending_species=data.get('pending_species'),topic=data.get('topic','health'))
