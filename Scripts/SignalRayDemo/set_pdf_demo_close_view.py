import unreal


camera_location = unreal.Vector(-18000.0, -52000.0, 23000.0)
target = unreal.Vector(0.0, 0.0, 7000.0)
rotation = unreal.MathLibrary.find_look_at_rotation(camera_location, target)
unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
    camera_location,
    rotation,
)
