"""Author project-owned procedural materials offline; runtime only loads cooked assets."""

import unreal

LIBRARY = unreal.MaterialEditingLibrary
ASSETS = unreal.AssetToolsHelpers.get_asset_tools()
DIRECTORY = "/Game/LC2/Materials"


def expression(material, kind, **properties):
    node = LIBRARY.create_material_expression(material, kind)
    for key, value in properties.items():
        node.set_editor_property(key, value)
    return node


def wire(source, target, name):
    assert LIBRARY.connect_material_expressions(source, "", target, name)


def custom(material, code, inputs, output=unreal.CustomMaterialOutputType.CMOT_FLOAT1):
    node = expression(material, unreal.MaterialExpressionCustom, code=code, output_type=output)
    declarations = []
    for name in inputs:
        declaration = unreal.CustomInput()
        declaration.set_editor_property("input_name", name)
        declarations.append(declaration)
    node.set_editor_property("inputs", declarations)
    for name, source in inputs.items():
        wire(source, node, name)
    return node


for name, glass in [("M_Surface", False), ("M_Surface_Glass", True)]:
    path = DIRECTORY + "/" + name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.log("LC2 material already exists: " + path)
        continue
    material = ASSETS.create_asset(name, DIRECTORY, unreal.Material, unreal.MaterialFactoryNew())
    assert material
    material.set_editor_property("two_sided", glass)
    if glass:
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        material.set_editor_property("translucency_lighting_mode",
                                     unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    LIBRARY.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    tint = expression(material, unreal.MaterialExpressionVectorParameter,
                      parameter_name="Tint", default_value=unreal.LinearColor(0.6, 0.6, 0.6, 1))
    uv = expression(material, unreal.MaterialExpressionTextureCoordinate)
    values = {}
    for parameter, default in [("roughness", 0.7), ("metallic", 0), ("emissive", 0),
                               ("wetness", 0), ("normal_strength", 0.25), ("pattern", 0)]:
        values[parameter] = expression(material, unreal.MaterialExpressionScalarParameter,
                                       parameter_name=parameter, default_value=default)
    pattern = custom(material, """
if (Pattern < 0.5) return 1.0;
if (Pattern < 1.5) return 0.65 + 0.35 * pow(abs(sin(UV.x*65 + sin(UV.y*6)*2)), 0.3);
if (Pattern < 2.5) return 0.7 + 0.3 * abs(sin(UV.x*160)*sin(UV.y*160));
float2 cell = floor(UV*35);
return 0.72 + 0.28 * frac(sin(dot(cell,float2(12.9898,78.233)))*43758.5453);
""", {"UV": uv, "Pattern": values["pattern"]})
    color = expression(material, unreal.MaterialExpressionMultiply)
    wire(tint, color, "A")
    wire(pattern, color, "B")
    assert LIBRARY.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
    roughness = custom(material, "return max(0.02, Roughness * (1.0 - 0.85 * Wetness));",
                       {"Roughness": values["roughness"], "Wetness": values["wetness"]})
    assert LIBRARY.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
    assert LIBRARY.connect_material_property(values["metallic"], "",
                                             unreal.MaterialProperty.MP_METALLIC)
    emission = expression(material, unreal.MaterialExpressionMultiply)
    wire(tint, emission, "A")
    wire(values["emissive"], emission, "B")
    assert LIBRARY.connect_material_property(emission, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    normal = custom(material,
                    "return normalize(float3(0.15*Strength*sin(UV.x*80),"
                    "0.15*Strength*cos(UV.y*80),1));",
                    {"UV": uv, "Strength": values["normal_strength"]},
                    unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    assert LIBRARY.connect_material_property(normal, "", unreal.MaterialProperty.MP_NORMAL)
    if glass:
        opacity = expression(material, unreal.MaterialExpressionConstant, r=0.28)
        assert LIBRARY.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)
    LIBRARY.layout_material_expressions(material)
    LIBRARY.recompile_material(material)
    assert unreal.EditorAssetLibrary.save_loaded_asset(material)
if not unreal.EditorAssetLibrary.does_asset_exist(DIRECTORY + "/M_Decal"):
    material = ASSETS.create_asset("M_Decal", DIRECTORY, unreal.Material,
                                   unreal.MaterialFactoryNew())
    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    tint = expression(material, unreal.MaterialExpressionVectorParameter,
                      parameter_name="Tint", default_value=unreal.LinearColor(0.05, 0.03, 0.01, 1))
    uv = expression(material, unreal.MaterialExpressionTextureCoordinate)
    pattern = expression(material, unreal.MaterialExpressionScalarParameter,
                         parameter_name="pattern", default_value=0)
    mask = custom(material, """
float2 p = UV * 2 - 1;
if (Pattern < 0.5) return saturate((0.85-length(p))*5)*0.7;
if (Pattern < 1.5) return (abs(p.x)<0.12 || abs(p.y)<0.12) && max(abs(p.x),abs(p.y))<0.8;
return (abs(p.x)<0.15 && p.y>-0.7 && p.y<0.25) ||
       (p.y>=0.0 && p.y<0.8 && abs(p.x)<(0.8-p.y)*0.8);
""", {"UV": uv, "Pattern": pattern})
    assert LIBRARY.connect_material_property(tint, "", unreal.MaterialProperty.MP_BASE_COLOR)
    assert LIBRARY.connect_material_property(mask, "", unreal.MaterialProperty.MP_OPACITY)
    LIBRARY.layout_material_expressions(material)
    LIBRARY.recompile_material(material)
    assert unreal.EditorAssetLibrary.save_loaded_asset(material)
unreal.log("LC2_MATERIALS_READY")
