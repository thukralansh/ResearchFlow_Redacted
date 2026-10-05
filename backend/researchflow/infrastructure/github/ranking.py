"""Deterministic, explainable ranking over immutable Stage A inventory."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from researchflow.domain.repository_ranking import (
    InspectionLimits,
    RankingWeights,
    RepositoryGuidance,
    tokenize,
)
from researchflow.ports.repositories import RepositoryComponentData, RepositoryTreeEntryData


@dataclass(frozen=True, slots=True)
class RankedRepositoryPath:
    path: str
    language: str | None
    coarse_role: str | None
    size_bytes: int
    score: Decimal
    selected: bool
    selection_reason: str
    matched_terms: tuple[str, ...]
    matched_roles: tuple[str, ...]
    stage_a_references: tuple[str, ...]
    signals: dict[str, str]
    influenced_by: tuple[dict[str, str], ...]

    def diagnostic(self) -> dict[str, object]:
        return {
            "path": self.path,
            "language": self.language,
            "coarse_role": self.coarse_role,
            "size_bytes": self.size_bytes,
            "score": str(self.score),
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "matched_terms": list(self.matched_terms),
            "matched_roles": list(self.matched_roles),
            "stage_a_references": list(self.stage_a_references),
            "signals": self.signals,
            "influenced_by": list(self.influenced_by),
        }


_ROLE_TERMS = {
    "model_definition": ("model", "network", "deeponet", "operator"),
    "loss": ("loss", "objective", "weight"),
    "data_loading": ("data", "dataset", "generator", "loader"),
    "preprocessing": ("preprocess", "convert", "partition", "domain"),
    "training": ("train", "training", "fit", "optim"),
    "evaluation": ("evaluate", "evaluation", "accuracy", "speed", "metric"),
    "configuration": ("config", "parameter", "setting", "script"),
    "test": ("test", "validation", "check"),
}


def rank_repository_paths(
    *,
    entries: tuple[RepositoryTreeEntryData, ...],
    stage_a_components: tuple[RepositoryComponentData, ...],
    guidance: RepositoryGuidance,
    weights: RankingWeights,
    limits: InspectionLimits,
) -> tuple[RankedRepositoryPath, ...]:
    weights.validate()
    limits.validate()
    terms = guidance.terms()
    expected_roles = guidance.expected_roles()
    references = _stage_a_references(stage_a_components, entries)
    raw: list[RankedRepositoryPath] = []
    for entry in entries:
        if (
            entry.entry_type != "blob"
            or entry.excluded_reason is not None
            or entry.size_bytes is None
            or entry.size_bytes > limits.max_file_bytes
        ):
            continue
        path_terms = set(tokenize(entry.path.replace("/", " ").replace(".", " ")))
        matched_terms = tuple(sorted(path_terms.intersection(terms)))
        matched_roles = tuple(
            role for role in expected_roles if path_terms.intersection(_ROLE_TERMS.get(role, ()))
        )
        exact_refs = tuple(sorted(ref for ref in references if ref == entry.path))
        term_signal = Decimal(min(len(matched_terms), 5)) / Decimal(5)
        role_signal = Decimal(min(len(matched_roles), 2)) / Decimal(2)
        reference_signal = Decimal(1 if exact_refs else 0)
        source_signal = Decimal(
            1
            if entry.coarse_role in {"source", "test", "entry_point"}
            and entry.language not in {None, "Markdown", "JSON", "YAML", "TOML"}
            else 0
        )
        score = (
            weights.guidance_term * term_signal
            + weights.expected_role * role_signal
            + weights.stage_a_reference * reference_signal
            + weights.implementation_source * source_signal
        ).quantize(Decimal("0.0001"))
        influenced_items: list[dict[str, str]] = []
        if path_terms.intersection(tokenize(f"{guidance.question_text} {guidance.scope or ''}")):
            influenced_items.append(
                {
                    "source_type": "research_question",
                    "source_id": guidance.research_question_id,
                }
            )
        if path_terms.intersection(tokenize(guidance.objective)):
            influenced_items.append(
                {"source_type": "project_objective", "source_id": guidance.project_id}
            )
        influenced_items.extend(
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
            }
            for item in guidance.accepted_inputs
            if path_terms.intersection(tokenize(item.text))
            or any(role in matched_roles for role in item.expected_roles)
        )
        influenced = tuple(influenced_items)
        if not influenced:
            continue
        raw.append(
            RankedRepositoryPath(
                path=entry.path,
                language=entry.language,
                coarse_role=entry.coarse_role,
                size_bytes=entry.size_bytes,
                score=score,
                selected=False,
                selection_reason="outside_candidate_limit",
                matched_terms=matched_terms,
                matched_roles=matched_roles,
                stage_a_references=exact_refs,
                signals={
                    "guidance_term": str(term_signal),
                    "expected_role": str(role_signal),
                    "stage_a_reference": str(reference_signal),
                    "implementation_source": str(source_signal),
                },
                influenced_by=influenced,
            )
        )
    ordered = sorted(raw, key=lambda item: (-item.score, item.path.casefold(), item.path))
    result: list[RankedRepositoryPath] = []
    used_bytes = 0
    selected_count = 0
    for index, item in enumerate(ordered):
        selected = False
        if index >= limits.candidate_limit:
            reason = "outside_candidate_limit"
        elif selected_count >= limits.inspection_limit:
            reason = "inspection_limit"
        elif used_bytes + item.size_bytes > limits.aggregate_bytes:
            reason = "aggregate_byte_budget"
        else:
            selected = True
            reason = "selected_for_deep_inspection"
            selected_count += 1
            used_bytes += item.size_bytes
        result.append(
            RankedRepositoryPath(
                path=item.path,
                language=item.language,
                coarse_role=item.coarse_role,
                size_bytes=item.size_bytes,
                score=item.score,
                selected=selected,
                selection_reason=reason,
                matched_terms=item.matched_terms,
                matched_roles=item.matched_roles,
                stage_a_references=item.stage_a_references,
                signals=item.signals,
                influenced_by=item.influenced_by,
            )
        )
    return tuple(result)


def _stage_a_references(
    components: tuple[RepositoryComponentData, ...],
    entries: tuple[RepositoryTreeEntryData, ...],
) -> frozenset[str]:
    texts = "\n".join(
        component.content_excerpt
        for component in components
        if component.component_type in {"readme", "documentation"}
    )
    return frozenset(entry.path for entry in entries if entry.path in texts)
