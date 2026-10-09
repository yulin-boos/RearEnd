from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from Common.schemas import ChatChecks
from Knowledge.schemas import KnowledgeActivity

InformationField = Literal["crop", "symptoms", "onset", "spread", "affected_parts",
                           "watering", "fertilizing", "environment", "pests"]
InformationText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class GuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    recognition_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int | None = Field(default=None, ge=0, strict=True)
    answers: dict[InformationField, InformationText] = Field(default_factory=dict, max_length=9)
    message: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def bounded_answers(self):
        if self.answers and self.revision is None:
            raise ValueError("提交回答时需要提供 revision")
        if sum(map(len, self.answers.values())) + len(self.message or "") > 4000:
            raise ValueError("本轮补充信息总长度不能超过 4000 字符")
        return self


class GuidanceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: InformationField
    question: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=240)
    options: list[ShortText] = Field(default_factory=list, max_length=5)


class ReportedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    field: InformationField
    quote: InformationText


class GuidanceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    analysis: str = Field(min_length=1, max_length=2000)
    advice: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(max_length=6)
    questions: list[GuidanceQuestion] = Field(max_length=3)
    observations: list[ReportedObservation] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def unique_fields(self):
        for fields in ([item.id for item in self.questions], [item.field for item in self.observations]):
            if len(fields) != len(set(fields)):
                raise ValueError("引导问题或观察字段重复")
        return self


class CropCandidate(BaseModel):
    crop: str
    confidence: float = Field(ge=0, le=1)


class GuidanceConfidence(BaseModel):
    top_confidence: float | None = Field(default=None, ge=0, le=1)
    threshold: float | None = Field(default=None, ge=0, le=1)
    is_confident: bool | None = None
    candidate_gap: float | None = Field(default=None, ge=0, le=1)
    crop_candidates: list[CropCandidate] = Field(default_factory=list)
    uncertainty_reasons: list[Literal["no_image_result", "low_confidence", "close_candidates", "different_crops"]] = Field(default_factory=list)


class GuidanceResponse(BaseModel):
    recognition_id: str
    revision: int
    stale: bool = False
    status: Literal["needs_information", "advice_ready"]
    analysis: str
    advice: list[str]
    questions: list[GuidanceQuestion]
    answered_information: dict[InformationField, InformationText]
    confidence: GuidanceConfidence
    model: str
    history_length: int
    elapsed_ms: float
    usage: dict[str, int] = Field(default_factory=dict)
    guard: ChatChecks
    knowledge: KnowledgeActivity = Field(default_factory=KnowledgeActivity)
