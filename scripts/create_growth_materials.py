"""Create preview-only height-clipped copies without changing production surfaces."""

import unreal

library = unreal.MaterialEditingLibrary
directory = "/Game/LC2/Materials/"
sources = {
    "M_Growth_Surface": directory + "M_Surface",
    "M_Growth_Glass": directory + "M_Surface_Glass",
    "M_Growth_Decal": directory + "M_Decal",
    "M_Growth_Default": "/Engine/BasicShapes/BasicShapeMaterial",
}
for name, source in sources.items():
    path = directory + name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.log("Growth material already exists: " + path)
        continue
    material = unreal.EditorAssetLibrary.duplicate_asset(source, path)
    assert isinstance(material, unreal.Material), source
    world = library.create_material_expression(material, unreal.MaterialExpressionWorldPosition)
    height = library.create_material_expression(material, unreal.MaterialExpressionScalarParameter)
    height.set_editor_property("parameter_name", "LCRevealHeightCM")
    height.set_editor_property("default_value", 100000000.0)
    mask = library.create_material_expression(material, unreal.MaterialExpressionCustom)
    mask.set_editor_property("code", "return step(World.z, Height);")
    mask.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
    inputs = []
    for key in ["World", "Height"]:
        entry = unreal.CustomInput()
        entry.set_editor_property("input_name", key)
        inputs.append(entry)
    mask.set_editor_property("inputs", inputs)
    assert library.connect_material_expressions(world, "", mask, "World")
    assert library.connect_material_expressions(height, "", mask, "Height")
    if name in {"M_Growth_Glass", "M_Growth_Decal"}:
        original = library.get_material_property_input_node(
            material, unreal.MaterialProperty.MP_OPACITY)
        assert original
        multiply = library.create_material_expression(material, unreal.MaterialExpressionMultiply)
        assert library.connect_material_expressions(original, "", multiply, "A")
        assert library.connect_material_expressions(mask, "", multiply, "B")
        assert library.connect_material_property(multiply, "", unreal.MaterialProperty.MP_OPACITY)
    else:
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
        assert library.connect_material_property(mask, "", unreal.MaterialProperty.MP_OPACITY_MASK)
    if name != "M_Growth_Decal":
        library.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    library.layout_material_expressions(material)
    library.recompile_material(material)
    assert unreal.EditorAssetLibrary.save_loaded_asset(material)
unreal.log("LC_GROWTH_MATERIALS_READY")
