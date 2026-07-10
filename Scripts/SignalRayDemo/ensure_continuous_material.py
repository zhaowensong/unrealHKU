import json
import os
import traceback

import unreal


MATERIAL_PATH = "/Game/SignalRayDemo/Materials/M_SignalRayContinuous"


def expression(material, expression_class, x, y):
    return unreal.MaterialEditingLibrary.create_material_expression(material, expression_class, x, y)


def main():
    existing = unreal.EditorAssetLibrary.load_asset(MATERIAL_PATH)
    disk_path = os.path.join(
        os.path.abspath(unreal.Paths.project_content_dir()),
        "SignalRayDemo",
        "Materials",
        "M_SignalRayContinuous.uasset",
    )
    if existing and os.path.isfile(disk_path):
        print(json.dumps({"material_path": MATERIAL_PATH, "created": False}))
        return
    if existing:
        unreal.EditorAssetLibrary.delete_asset(MATERIAL_PATH)

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = asset_tools.create_asset(
        "M_SignalRayContinuous",
        "/Game/SignalRayDemo/Materials",
        unreal.Material,
        unreal.MaterialFactoryNew(),
    )
    if material is None:
        raise RuntimeError("Could not create {}".format(MATERIAL_PATH))

    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    mel = unreal.MaterialEditingLibrary

    strength = expression(material, unreal.MaterialExpressionPerInstanceCustomData, -900, -50)
    strength.set_editor_property("data_index", 0)

    two = expression(material, unreal.MaterialExpressionConstant, -900, 120)
    two.set_editor_property("r", 2.0)
    scaled_strength = expression(material, unreal.MaterialExpressionMultiply, -700, 0)
    mel.connect_material_expressions(strength, "", scaled_strength, "A")
    mel.connect_material_expressions(two, "", scaled_strength, "B")

    low_alpha = expression(material, unreal.MaterialExpressionSaturate, -500, -120)
    mel.connect_material_expressions(scaled_strength, "", low_alpha, "Input")
    one = expression(material, unreal.MaterialExpressionConstant, -700, 220)
    one.set_editor_property("r", 1.0)
    subtract = expression(material, unreal.MaterialExpressionSubtract, -500, 140)
    mel.connect_material_expressions(scaled_strength, "", subtract, "A")
    mel.connect_material_expressions(one, "", subtract, "B")
    high_alpha = expression(material, unreal.MaterialExpressionSaturate, -320, 140)
    mel.connect_material_expressions(subtract, "", high_alpha, "Input")

    red = expression(material, unreal.MaterialExpressionConstant3Vector, -500, -430)
    red.set_editor_property("constant", unreal.LinearColor(1.0, 0.02, 0.0, 1.0))
    yellow = expression(material, unreal.MaterialExpressionConstant3Vector, -500, -330)
    yellow.set_editor_property("constant", unreal.LinearColor(1.0, 0.78, 0.0, 1.0))
    green = expression(material, unreal.MaterialExpressionConstant3Vector, -160, -330)
    green.set_editor_property("constant", unreal.LinearColor(0.0, 1.0, 0.08, 1.0))

    low_color = expression(material, unreal.MaterialExpressionLinearInterpolate, -250, -260)
    mel.connect_material_expressions(red, "", low_color, "A")
    mel.connect_material_expressions(yellow, "", low_color, "B")
    mel.connect_material_expressions(low_alpha, "", low_color, "Alpha")
    color = expression(material, unreal.MaterialExpressionLinearInterpolate, 20, -180)
    mel.connect_material_expressions(low_color, "", color, "A")
    mel.connect_material_expressions(green, "", color, "B")
    mel.connect_material_expressions(high_alpha, "", color, "Alpha")

    three = expression(material, unreal.MaterialExpressionConstant, -150, 100)
    three.set_editor_property("r", 3.0)
    glow_range = expression(material, unreal.MaterialExpressionMultiply, 20, 80)
    mel.connect_material_expressions(strength, "", glow_range, "A")
    mel.connect_material_expressions(three, "", glow_range, "B")
    base_glow = expression(material, unreal.MaterialExpressionConstant, 20, 200)
    base_glow.set_editor_property("r", 2.0)
    glow = expression(material, unreal.MaterialExpressionAdd, 220, 80)
    mel.connect_material_expressions(base_glow, "", glow, "A")
    mel.connect_material_expressions(glow_range, "", glow, "B")
    emissive = expression(material, unreal.MaterialExpressionMultiply, 420, -100)
    mel.connect_material_expressions(color, "", emissive, "A")
    mel.connect_material_expressions(glow, "", emissive, "B")
    mel.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    opacity = expression(material, unreal.MaterialExpressionConstant, 420, 180)
    opacity.set_editor_property("r", 0.42)
    mel.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)
    mel.layout_material_expressions(material)
    mel.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(MATERIAL_PATH)
    print(json.dumps({"material_path": MATERIAL_PATH, "created": True}))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
