"""Small coarse plans and one-module deliveries for a single designer."""

from typing import Literal

from pydantic import Field

from .build_types import Anchor, Environment, Input, Name, Node, Vector


class ModuleBrief(Input):
    id: Name
    label: str = Field(max_length=80)
    size: Vector
    at: Vector | None = None
    yaw: float = 0
    relative_to: Name | None = None
    side: Literal["left", "right", "front", "back", "above", "below"] | None = None
    gap: float = 0
    depends_on: list[Name] = Field(default_factory=list)
    brief: str = Field(default="", max_length=240)


class CoarsePlan(Input):
    name: Name = Field(description="English kebab-case identifier, e.g. limestone-porch.")
    expected_version: int
    anchor: Anchor | None = None
    modules: list[ModuleBrief] = Field(min_length=1, max_length=32)
    environment: Environment | None = None


class ModuleDelivery(Input):
    id: Name
    revision: int = Field(default=1, ge=1)
    expected_version: int | None = None
    nodes: list[Node] = Field(min_length=1, max_length=256)
    definitions: dict[Name, Node] | None = None
