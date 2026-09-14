"""Which structured-output schema shapes one route accepts (#942).

Frozen evidence for ``structured-output-shapes.md``. Every call is live and
costs a few tokens, so run it to re-measure rather than as part of a suite.

    OPENROUTER_API_KEY=... python docs/research/probe_structured_output_shapes.py

Four questions, in the order the bisect asked them:

1. Which of this service's model-facing schemas does the route accept?
2. Which feature of the refused one decides it — a cap on the root array, a cap
   on a nested array, or the row's own fields?
3. At which values of that cap?
4. Is ``anyOf`` implicated, which was the first suspect?
"""

from __future__ import annotations

from typing import Literal

import litellm
from pydantic import BaseModel, ConfigDict, Field, create_model

from analysis_service import model_gate  # noqa: F401  (pins the cost map first)
from analysis_service.assertions import (
    MAX_ASSERTIONS,
    AssertionProposal,
    CatalogProposal,
)
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.system_model import SystemModel

MODEL = "openrouter/google/gemini-3.5-flash-lite"
PROMPT = [{"role": "user", "content": "Reply with an empty object."}]

litellm.suppress_debug_info = True


def verdict(schema):
    """What the route answers when asked for ``schema``."""
    try:
        litellm.completion(
            model=MODEL, messages=PROMPT, response_format=schema, max_tokens=64
        )
    except Exception as error:  # noqa: BLE001 — the message is the measurement
        message = str(error)
        return "INVALID_ARGUMENT" if "INVALID_ARGUMENT" in message else message[:60]
    return "accepted"


def root_capped_object_arrays(schema):
    """Root properties that are an array of objects carrying ``maxItems``."""
    properties = schema.model_json_schema().get("properties", {})
    found = []
    for name, prop in properties.items():
        if prop.get("type") != "array" or "maxItems" not in prop:
            continue
        items = prop.get("items", {})
        if "$ref" in items or items.get("type") == "object":
            found.append(f"{name}(maxItems={prop['maxItems']})")
    return found


def wrapper(row, cap):
    """One root array of ``row``, capped or not."""
    field = Field(default_factory=list)
    if cap is not None:
        field = Field(default_factory=list, max_length=cap)
    return create_model(
        "Wrapper", __config__=ConfigDict(extra="forbid"), assertions=(list[row], field)
    )


def uncapped_row():
    """An assertion row whose own nested lists carry no cap."""
    return create_model(
        "UncappedRow",
        __config__=ConfigDict(extra="forbid"),
        **{
            name: (
                info.annotation,
                Field(default_factory=list) if name in ("scope", "quotes") else info,
            )
            for name, info in AssertionProposal.model_fields.items()
        },
    )


def question_one():
    """Every schema a node asks a model to fill, against the route."""
    print("\n1. the service's own model-facing schemas")
    rows = [("SystemModel", SystemModel), ("CatalogProposal", CatalogProposal)]
    for name in PACKAGES:
        schemas = schemas_for(name)
        rows += [
            (f"{name}.proposals", schemas.proposals),
            (f"{name}.rulings", schemas.rulings),
        ]
    print(f"   {'schema':26} {'root capped object arrays':30} route")
    for label, schema in rows:
        caps = ", ".join(root_capped_object_arrays(schema)) or "-"
        print(f"   {label:26} {caps:30} {verdict(schema)}")


def question_two():
    """Which cap decides it: the root array's, the row's nested ones, neither."""
    print("\n2. where the cap sits")
    for label, row, cap in (
        ("root cap + nested caps", AssertionProposal, MAX_ASSERTIONS),
        ("root cap, no nested caps", uncapped_row(), MAX_ASSERTIONS),
        ("no root cap, nested caps", AssertionProposal, None),
        ("no root cap, no nested caps", uncapped_row(), None),
    ):
        print(f"   {label:28} {verdict(wrapper(row, cap))}")
    strings = create_model(
        "Strings",
        __config__=ConfigDict(extra="forbid"),
        tags=(list[str], Field(default_factory=list, max_length=32)),
    )
    print(f"   {'a capped root array of str':28} {verdict(strings)}")


def question_three():
    """At which values of the root cap."""
    print("\n3. the cap's value")
    for cap in (8, 100, 256, 400, 500):
        print(f"   maxItems={cap:<19} {verdict(wrapper(AssertionProposal, cap))}")


def question_four():
    """``anyOf``, the first suspect, as controls."""
    print("\n4. anyOf, for elimination")

    class NullableEnum(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: Literal["silent", "hedged"] | None = None

    class Inner(BaseModel):
        model_config = ConfigDict(extra="forbid")
        rating: Literal["low", "high"]

    class NullableObject(BaseModel):
        model_config = ConfigDict(extra="forbid")
        severity: Inner | None = None

    print(f"   {'a nullable enum':28} {verdict(NullableEnum)}")
    print(f"   {'a nullable nested object':28} {verdict(NullableObject)}")
    for name in PACKAGES:
        label = f"{name} rulings (many anyOf)"
        print(f"   {label:28} {verdict(schemas_for(name).rulings)}")


if __name__ == "__main__":
    print(f"route: {MODEL}")
    question_one()
    question_two()
    question_three()
    question_four()
