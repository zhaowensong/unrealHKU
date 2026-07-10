import unreal
subsystem = unreal.UnrealEditorSubsystem()
subsystem.set_level_viewport_camera_info(unreal.Vector(0, 500, 300000), unreal.Rotator(-35, 0, 90))
unreal.log('Camera fixed with roll!')