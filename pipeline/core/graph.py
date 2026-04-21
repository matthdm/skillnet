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
from core.nodes.plan import plan_node
from core.nodes.retrieve import retrieve_skills_node
from core.nodes.test_runner import test_node
from core.repo_manager import RepoManager
from core.skills import SkillStore
from models.job import JobState, JobStatus


def build_graph(router: LLMRouter, store: SkillStore, repo_manager: RepoManager, checkpointer=None):
    workflow = StateGraph(JobState)

    analyze_llm = router.get(LLMTier.HIGH)
    plan_llm = router.get(LLMTier.HIGH)
    codegen_llm = router.get(LLMTier.MEDIUM)
    interpret_llm = router.get(LLMTier.MEDIUM)
    analyze_label = router.provider_label(LLMTier.HIGH)
    plan_label = router.provider_label(LLMTier.HIGH)
    codegen_label = router.provider_label(LLMTier.MEDIUM)

    workflow.add_node("inject", inject_node)
    workflow.add_node("analyze", partial(analyze_node, llm=analyze_llm, provider_label=analyze_label))
    workflow.add_node("retrieve", partial(retrieve_skills_node, store=store))
    workflow.add_node("plan", partial(plan_node, llm=plan_llm, provider_label=plan_label))
    workflow.add_node("codegen", partial(codegen_node, llm=codegen_llm, provider_label=codegen_label))
    workflow.add_node("test", test_node)
    workflow.add_node("interpret", partial(interpret_failure_node, llm=interpret_llm, provider_label=codegen_label))
    workflow.add_node("exhaust", _exhaust_node)
    workflow.add_node("commit", partial(commit_node, repo_manager=repo_manager))

    workflow.set_entry_point("inject")
    workflow.add_edge("inject", "analyze")
    workflow.add_edge("analyze", "retrieve")

    workflow.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        {"plan": "plan", "codegen": "codegen", END: END},
    )

    workflow.add_edge("plan", END)  # graph pauses; resumes after human approval

    workflow.add_conditional_edges(
        "codegen",
        _route_after_codegen,
        {"test": "test", END: END},
    )

    workflow.add_conditional_edges(
        "test",
        _route_after_test,
        {"commit": "commit", "interpret": "interpret", "exhaust": "exhaust"},
    )

    workflow.add_conditional_edges(
        "interpret",
        _route_after_interpret,
        {"codegen": "codegen", END: END},
    )
    workflow.add_edge("commit", END)
    workflow.add_edge("exhaust", END)

    return workflow.compile(checkpointer=checkpointer)


def _route_after_retrieve(state: JobState) -> str:
    plan = state.implementation_plan
    if plan is None:
        return "plan"
    if plan.status == "approved":
        return "codegen"
    # PLANNING (pending) or REJECTED — graph ends, worker saves state
    return END


_HALT_STATUSES = {JobStatus.PAUSED, JobStatus.FAILED, JobStatus.EXHAUSTED}


def _route_after_codegen(state: JobState) -> str:
    """Halt the graph if codegen failed or timed out; otherwise proceed to test."""
    if state.status in _HALT_STATUSES:
        return END
    return "test"


def _route_after_interpret(state: JobState) -> str:
    """Halt the graph if interpret failed; otherwise loop back to codegen."""
    if state.status in _HALT_STATUSES:
        return END
    return "codegen"


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
