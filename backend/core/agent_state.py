from typing import Any, Dict, List, Literal, TypedDict


class RecommendationItem(TypedDict):
    program: str
    score: float
    reasons: List[str]


class AgentState(TypedDict, total=False):
    session_id: str
    lang: str
    user_message: str
    use_llm: bool
    allow_web_search: bool
    next_node: Literal["general", "interview", "knowledge", "career", "scoring", "recommendation", "calculator", "lookup"]
    answer: str
    route: str
    profile: Dict[str, Any]
    facts: List[Dict[str, Any]]
    task: str
    planner_notes: str
    session: Dict[str, Any]
    retrieved_context: List[str]
    lookup_query: str
    lookup_queries: List[str]
    lookup_iteration: int
    needs_lookup: bool
    recommendations: List[RecommendationItem]
    scoring_table: List[Dict[str, Any]]
    profile_complete: bool
    reply_to: Dict[str, Any] | None
    recent_raw_messages: str
    relevant_memory_chunks: List[Dict[str, Any]]
    memory_context: Dict[str, Any]
    memory_prompt: str
    turn_semantics: Dict[str, Any]
