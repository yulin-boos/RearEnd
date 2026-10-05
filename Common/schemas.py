from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from Knowledge.schemas import KnowledgeActivity, KnowledgeDecision


class ApiError(BaseModel):
    code: str
    message: str


class JevCheck(BaseModel):
    model: str
    relevance_probability: float = Field(ge=0, le=1)
    off_topic_probability: float = Field(ge=0, le=1)
    instruction_override_probability: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)
    knowledge_decision: KnowledgeDecision | None = None
    knowledge_error: str | None = None


class ChatChecks(BaseModel):
    question: JevCheck
    reply: JevCheck


class ChatReply(BaseModel):
    recognition_id: str
    model: str
    source: Literal["deepseek", "knowledge"] = "deepseek"
    reply: str
    history_length: int
    elapsed_ms: float
    guard: ChatChecks
    usage: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    knowledge: KnowledgeActivity = Field(default_factory=KnowledgeActivity)


class RecognitionChat(BaseModel):
    recognition_id: str
    configured: bool
    guard_configured: bool = False
    model: str
    source: Literal["deepseek", "knowledge"] = "deepseek"
    status: Literal["ready", "not_configured", "error", "skipped"]
    reply: str | None = None
    error: ApiError | None = None
    elapsed_ms: float | None = None
    truncated: bool = False
    guard: ChatChecks | None = None
    knowledge: KnowledgeActivity | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recognition_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    message: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("message cannot be blank")
        return value


class ChatSessionResponse(BaseModel):
    session_id: str
    kind: Literal["general"] = "general"
    title: str = "智能病虫害问答"


class ClassInfo(BaseModel):
    class_id: int
    class_name: str
    display_name: str
    crop: str | None = None
    condition: str | None = None
    is_healthy: bool | None = None


class Prediction(ClassInfo):
    confidence: float = Field(ge=0, le=1)


class ImageInfo(BaseModel):
    filename: str
    width: int
    height: int


class LeafCheck(BaseModel):
    is_leaf: bool
    model: str
    leaf_similarity: float = Field(ge=-1, le=1)
    competing_similarity: float = Field(ge=-1, le=1)
    competing_category: str
    margin: float = Field(ge=-2, le=2)
    minimum_similarity: float
    minimum_margin: float
    elapsed_ms: float = Field(ge=0)


class RecognitionResponse(BaseModel):
    request_id: str
    task: Literal["classify"] = "classify"
    model: str
    image: ImageInfo
    leaf_check: LeafCheck
    postprocessor: Literal["cpp", "python"]
    confidence_threshold: float
    is_confident: bool
    status: Literal["recognized", "uncertain"]
    top_prediction: Prediction
    predictions: list[Prediction]
    inference_ms: float
    elapsed_ms: float
    result_image_url: str | None = None
    chat: RecognitionChat | None = None
