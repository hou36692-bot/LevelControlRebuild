"""Import only meshes authored by create_v2_props.py and bake runtime collision/LODs."""

import json
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parent
catalog = json.loads((ROOT / "ue/Plugins/LevelControlRuntime/Resources/catalog.json").read_text())
assets = unreal.AssetToolsHelpers.get_asset_tools()
meshes = (unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
          or unreal.StaticMeshEditorSubsystem())
report = []
for prop in catalog["props"]:
    path = prop["mesh"]
    if unreal.EditorAssetLibrary.does_asset_exist(path) and prop.get("imported"):
        mesh = unreal.load_asset(path)
    else:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", str(ROOT / ".local-assets/lc2-props" /
                                                  (prop["id"].split("/")[1]+".obj")))
        task.set_editor_property("destination_path", "/Game/LC2/Props")
        task.set_editor_property("destination_name", path.rsplit("/", 1)[1])
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("replace_existing_settings", True)
        task.set_editor_property("save", True)
        options = unreal.FbxImportUI()
        options.set_editor_property("import_mesh", True)
        options.set_editor_property("import_as_skeletal", False)
        options.set_editor_property("import_materials", False)
        options.set_editor_property("import_textures", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
        options.static_mesh_import_data.set_editor_property("combine_meshes", True)
        options.static_mesh_import_data.set_editor_property("auto_generate_collision", False)
        task.set_editor_property("options", options)
        assets.import_asset_tasks([task])
        mesh = unreal.load_asset(path)
    assert isinstance(mesh, unreal.StaticMesh), path
    body = mesh.get_editor_property("body_setup")
    body.set_editor_property("collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
    options = unreal.StaticMeshReductionOptions()
    options.set_editor_property("auto_compute_lod_screen_size", False)
    settings = []
    for fraction, screen in [(1.0, 1.0), (0.5, 0.25), (0.25, 0.08)]:
        setting = unreal.StaticMeshReductionSettings()
        setting.set_editor_property("percent_triangles", fraction)
        setting.set_editor_property("screen_size", screen)
        settings.append(setting)
    options.set_editor_property("reduction_settings", settings)
    assert meshes.set_lods(mesh, options) == 3, path
    assert unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    bounds = mesh.get_bounding_box()
    extent = bounds.max - bounds.min
    assert all(0 < x < 200 for x in [extent.x, extent.y, extent.z]), (path, extent)
    center = (bounds.max + bounds.min) * 0.5
    prop["mesh_size_cm"] = [extent.x, extent.y, extent.z]
    prop["mesh_center_cm"] = [center.x, center.y, center.z]
    prop["imported"] = True
    report.append({"id": prop["id"], "lods": meshes.get_lod_count(mesh),
                   "bounds_cm": [extent.x, extent.y, extent.z],
                   "collision": "complex-as-simple", "saved": True})
(ROOT / "tests/reports/v2-prop-import.json").write_text(json.dumps(report, indent=2))
(ROOT / "ue/Plugins/LevelControlRuntime/Resources/catalog.json").write_text(
    json.dumps(catalog, indent=2)+"\n")
unreal.log("LC2_PROPS_READY: " + str(len(report)))
