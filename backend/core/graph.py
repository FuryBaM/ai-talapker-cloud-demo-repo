from langgraph.graph import END, START, StateGraph

from core.agent_state import AgentState
from core.agents import (
    calculator_agent,
    career_agent,
    extract_memory,
    general_agent,
    interview_agent,
    knowledge_agent,
    lookup_agent,
    plan_turn,
    recommendation_agent,
    scoring_agent,
)


def _route_from_state(state: AgentState) -> str:
    return state["next_node"]


def _after_interview(state: AgentState) -> str:
    return "scoring" if state.get("profile_complete") else END


def _after_calculator(state: AgentState) -> str:
    return "lookup" if state.get("needs_lookup") else END


def _after_knowledge(state: AgentState) -> str:
    return "lookup" if state.get("needs_lookup") else END


def _after_lookup(state: AgentState) -> str:
    return "lookup" if state.get("needs_lookup") else END


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("memory", extract_memory)
    graph.add_node("planner", plan_turn)
    graph.add_node("general", general_agent)
    graph.add_node("interview", interview_agent)
    graph.add_node("knowledge", knowledge_agent)
    graph.add_node("career", career_agent)
    graph.add_node("scoring", scoring_agent)
    graph.add_node("recommendation", recommendation_agent)
    graph.add_node("calculator", calculator_agent)
    graph.add_node("lookup", lookup_agent)

    graph.add_edge(START, "memory")
    graph.add_edge("memory", "planner")
    graph.add_conditional_edges(
        "planner",
        _route_from_state,
        {
            "general": "general",
            "interview": "interview",
            "knowledge": "knowledge",
            "career": "career",
            "scoring": "scoring",
            "recommendation": "recommendation",
            "calculator": "calculator",
            "lookup": "lookup",
        },
    )
    graph.add_conditional_edges("interview", _after_interview, {"scoring": "scoring", END: END})
    graph.add_conditional_edges("calculator", _after_calculator, {"lookup": "lookup", END: END})
    graph.add_conditional_edges("knowledge", _after_knowledge, {"lookup": "lookup", END: END})
    graph.add_conditional_edges("lookup", _after_lookup, {"lookup": "lookup", END: END})
    graph.add_edge("general", END)
    graph.add_edge("scoring", "recommendation")
    graph.add_edge("career", END)
    graph.add_edge("recommendation", END)
    return graph.compile()


AGENT_GRAPH = build_agent_graph()
