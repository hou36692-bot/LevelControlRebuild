"""Model-facing schemas keep construction inputs explicit and compact."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64)]
Vector = Annotated[list[float], Field(min_length=3, max_length=3)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value):
        def check(item, key=""):
            if isinstance(item, bool) and key != "collision":
                raise ValueError("Boolean values cannot be used as numbers")
            if isinstance(item, dict):
                for name, child in item.items():
                    check(child, name)
            elif isinstance(item, list):
                for child in item:
                    check(child, key)
        check(value)
        return value


class Surface(Input):
    preset: str | None = None
    tint: Vector | None = None
    roughness: float | None = None
    metallic: float | None = None
    emissive: float | None = None
    normal_strength: float | None = None
    wetness: float | None = None


class Repeat(Input):
    count: int | list[int]
    step: Vector | None = None
    span: Vector | None = None
    members: list[Name] | None = None


class Semantic(Input):
    name: str | None = None
    aliases: list[str] | None = None
    role: str | None = None
    area: str | None = None
    pattern: str | None = None
    ring: str | None = None
    side: str | None = None
    provenance: Literal["design", "manual", "inferred"] = "design"
    relations: list[dict[str, str]] | None = None


class Node(Input):
    id: Name | None = None
    key: Name | None = Field(
        default=None,
        description="Immutable author key; retain across rename/reparent.")
    semantic: Semantic | None = None
    asset: str | None = Field(default=None, description=(
        "Cooked asset ID. blockout/box=rectangular cuboid, blockout/column=cylinder, "
        "blockout/floor=rectangular floor, blockout/wall=rectangular wall; prop/* from catalog."))
    kind: Literal["floor", "wall", "doorway", "colonnade", "stairs", "fountain"] | None = None
    parts: list[Node] | None = None
    use: Name | None = None
    at: Vector | None = Field(default=None, description="Local center in meters, default [0,0,0].")
    size: Vector | None = None
    yaw: float | None = None
    surface: Surface | str | None = Field(default=None, description="Preset ID or surface fields.")
    collision: StrictBool | None = None
    repeat: Repeat | None = None
    params: dict | None = None


class Anchor(Input):
    front_m: float | None = None
    at: Vector | None = None
    yaw: float | None = None


class EnvironmentValues(Input):
    time_h: float | None = None
    sun_azimuth_deg: float | None = None
    sun_elevation_deg: float | None = None
    sun_intensity: float | None = None
    sun_temperature_k: float | None = None
    sky_intensity: float | None = None
    atmosphere_density: float | None = None
    fog_density: float | None = None
    fog_height_cm: float | None = None
    fog_falloff: float | None = None
    fog_color: Vector | None = None
    cloud_coverage: float | None = None
    exposure_ev: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    temperature_k: float | None = None


class Environment(Input):
    preset: str | None = None
    values: EnvironmentValues | None = None


class Presentation(Input):
    mode: Literal["auto", "off", "on"] = "auto"
    camera: Literal["player", "overview"] = "player"
    music: Literal["none", "architect-pulse"] = "none"


class BuildPlan(Input):
    name: Name = "build"
    scene: Name | None = Field(default=None, description="Only when updating a saved scene.")
    expected_version: int | None = None
    anchor: Anchor | None = None
    definitions: dict[Name, Node] | None = None
    nodes: list[Node]
    environment: Environment | None = None
    intent: Literal["new_structure", "extension", "edit", "placement"] = Field(
        default="edit", description="Always set: whole new building/area=new_structure; "
        "architectural addition (tower/wing/corridor)=extension; adjustments=edit; "
        "individual or repeated props=placement.")
    presentation: Presentation | None = None
    override_policy: Literal["preserve", "discard"] = "preserve"
    constraints: list[dict] | None = None
