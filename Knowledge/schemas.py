from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FOCUS_INSTRUCTIONS = {
    "causes": "整理可能成因、诱发条件与需核实的证据，保留所有不确定性。",
    "symptoms": "整理症状、鉴别要点与观察方法，不把模型候选当作确诊。",
    "management": "整理适用条件和可执行的管理建议，不补充未经提供的药名或剂量。",
    "prevention": "整理预防与种植环境管理要点，明确适用作物和条件。",
    "diagnosis": "整理需要核实的信息与诊断不确定性，不给出未经证实的结论。",
    "none": "",
}


class KnowledgeDecision(BaseModel):
    model: str
    store: bool
    value_probability: float = Field(ge=0, le=1)
    grounded_probability: float = Field(ge=0, le=1)
    private_data_probability: float = Field(ge=0, le=1)
    focus: Literal["causes", "symptoms", "management", "prevention", "diagnosis", "none"]
    focus_confidence: float = Field(ge=0, le=1)


class KnowledgeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=120)
    crop: str = Field(min_length=1, max_length=80)
    condition: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=2, max_length=400)
    answer: str = Field(min_length=5, max_length=3000)
    applicability: str = Field(min_length=2, max_length=600)
    uncertainty: str = Field(min_length=2, max_length=600)
    keywords: list[str] = Field(min_length=1, max_length=12)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values):
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 40 for value in cleaned):
            raise ValueError("Invalid knowledge keyword")
        return list(dict.fromkeys(cleaned))


class KnowledgeEntry(KnowledgeDraft):
    id: str
    created_at: float
    source_type: Literal["ai_summary"] = "ai_summary"
    expert_verified: Literal[False] = False


class KnowledgeSelection(BaseModel):
    references: list[KnowledgeEntry] = Field(default_factory=list)
    direct_entry: KnowledgeEntry | None = None
    direct_probability: float | None = Field(default=None, ge=0, le=1)


class KnowledgeActivity(BaseModel):
    status: Literal["skipped", "stored", "duplicate", "rejected", "error", "reused"] = "skipped"
    entry_id: str | None = None
    decision: KnowledgeDecision | None = None
    source_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retrieval_error_code: str | None = None
    reuse_probability: float | None = Field(default=None, ge=0, le=1)
