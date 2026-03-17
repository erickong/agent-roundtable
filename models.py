"""Data structures for the Roundtable Meeting System V1."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class MeetingInput:
    """User's meeting input: topic, goal, constraints, background."""
    topic: str
    goal: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    background: Optional[str] = None


@dataclass
class AgentMessage:
    """A single agent's speech in a round."""
    round_index: int
    agent_name: str
    content: Dict[str, Any]
    raw_text: str = ""
    search_info: Optional[List[Dict[str, Any]]] = None  # [{"query": str, "result_count": int, ...}]


@dataclass
class RoundScore:
    """Per-round score for an agent."""
    round_index: int
    agent_name: str
    novelty_score: int
    critique_score: int
    comment: str


@dataclass
class FinalScore:
    """Final contribution score for an agent."""
    agent_name: str
    contribution_score: int
    summary: str


@dataclass
class RoundSummary:
    """Moderator's summary after each round."""
    round_index: int
    new_valuable_ideas: List[str]
    strong_critiques: List[str]
    points_worth_preserving: List[str]
    scores: List[RoundScore]
    next_step: str
    should_continue: bool = True
    raw_text: str = ""


@dataclass
class FinalReport:
    """Final roundtable report produced by the moderator."""
    problem_definition: str
    main_consensus: List[str]
    main_disagreements: List[str]
    recommended_solution: str
    why_this_solution: str
    preserved_minority_opinions: List[str]
    agent_contributions: Dict[str, str]
    final_scores: List[FinalScore]
    raw_markdown: str = ""
