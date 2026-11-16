import json
from typing import Any

from pydantic import BaseModel

from .entities import Edge, Entity
from .findings import ConfidenceBreakdown, Finding
from .llm import ExtractionResult, QueryPlan, SearchQuery, UsernameVariant, VariantSet
from .observations import Evidence, Observation
from .profiles import GithubProfile, HtbProfile, ThmProfile, WebProfile
from .tasks import Budget, ScanContext, Seed, Target, TaskSpec

MODELS: list[type[BaseModel]] = [
    Evidence,
    Observation,
    Entity,
    Edge,
    ConfidenceBreakdown,
    Finding,
    TaskSpec,
    Target,
    Seed,
    Budget,
    ScanContext,
    GithubProfile,
    ThmProfile,
    HtbProfile,
    WebProfile,
    SearchQuery,
    QueryPlan,
    UsernameVariant,
    VariantSet,
    ExtractionResult,
]


def export_all_schemas() -> dict[str, Any]:
    schemas = {}
    for model in MODELS:
        schemas[model.__name__] = model.model_json_schema()
    return schemas


def main():
    schemas = export_all_schemas()
    print(json.dumps(schemas, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
