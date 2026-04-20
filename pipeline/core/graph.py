from __future__ import annotations

from datetime import datetime
from functools import partial

from langgraph.graph import END, StateGraph

from core.llm_router import LLMRouter, LLMTier
from core.nodes.analyze import analyze_node
from core.nodes.codegen import codegen_node
from core.nodes.commit import commit_node
from core.nodes.inject import inject_node
from core.nodes.interpret import interpret_failure_node
from core.nodes.retrieve import retrieve_skills_node
from core.nodes.test_runner import test_node
from core.skills import SkillStore
from models.job import JobState, JobStatus


def build_graph(router: LLMRouter, store: SkillStore, checkpointer=None):
    """
    Build and compile the LangGraph pipeline.
    Dependencies (router, store) are bound to nodes at construction time via partial.
    The compiled graph is thread-safe and reusable across requests.
    """
    workflow = StateGraph(JobState)

    analyze_llm = router.get(LLMTier.HIGH)
    codegen_llm = router.get(LLMTier.MEDIUM)
    interpret_llm = router.get(LLMTier.MEDIUM)
    analyze_label = router.provider_label(LLMTier.HIGH)
    codegen_label = router.provider_label(LLMTier.MEDIUM)

    workflow.add_node("inject", inject_node)
    workflow.add_node("analyze", partial(analyze_node, llm=analyze_llm, provider_label=analyze_label))
    workflow.add_node("retrieve", partial(retrieve_skills_node, store=store))
    workflow.add_node("codegen", partial(codegen_node, llm=codegen_llm, provider_label=codegen_label))
    workflow.add_node("test", test_node)
    workflow.add_node("interpret", partial(interpret_failure_node, llm=interpret_llm, provider_label=codegen_label))
    workflow.add_node("exhaust", _exhaust_node)
    workflow.add_node("commit", commit_node)

    workflow.set_entry_point("inject")
    workflow.add_edge("inject", "analyze")
    workflow.add_edge("analyze", "retrieve")
    workflow.add_edge("retrieve", "codegen")
    workflow.add_edge("codegen", "test")

    workflow.add_conditional_edges(
        "test",
        _route_after_test,
        {
            "commit": "commit",
            "interpret": "interpret",
            "exhaust": "exhaust",
        },
    )

    workflow.add_edge("interpret", "codegen")
    workflow.add_edge("commit", END)
    workflow.add_edge("exhaust", END)

    return workflow.compile(checkpointer=checkpointer)


def _route_after_test(state: JobState) -> str:
    if state.test_results is not None and state.test_results.passed:
        return "commit"
    if state.iteration_count >= state.max_iterations:
        return "exhaust"
    return "interpret"


def _exhaust_node(state: JobState) -> dict:
    return {
        "status": JobStatus.EXHAUSTED,
        "updated_at": datetime.utcnow(),
        "error_logs": state.error_logs + [
            f"Job exhausted after {state.iteration_count} iteration(s). Manual review required."
        ],
    }
