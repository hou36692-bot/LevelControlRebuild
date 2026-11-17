"""Author the sandbox once in the editor; runtime has no editor dependency."""

import unreal

MAP = "/Game/Maps/LC_Empty"
if unreal.EditorAssetLibrary.does_asset_exist(MAP):
    raise RuntimeError(f"Map already exists; refusing to overwrite {MAP}")
level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert level.new_level(MAP)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
floor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, -50))
floor.set_actor_label("SandboxFloor")
floor.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
floor.set_actor_scale3d(unreal.Vector(100, 100, 1))
actors.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(0, 0, 100))
sun = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 800), unreal.Rotator(-45, -35, 0))
sun.light_component.set_editor_property("intensity", 3.0)
sun.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sky = actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500))
sky.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sky.light_component.set_editor_property("intensity", 0.7)
actors.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
sun.light_component.set_editor_property("atmosphere_sun_light", True)
assert level.save_current_level()
unreal.log("LC_STEP1_MAP_CREATED")
