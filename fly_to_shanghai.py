import unreal
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if 'CesiumGeoreference' in actor.get_class().get_name():
        actor.set_editor_property('origin_latitude', 31.2397)
        actor.set_editor_property('origin_longitude', 121.4998)
        actor.set_editor_property('origin_height', 0.0)
        unreal.log('Set origin to Shanghai!')
        break
subsystem = unreal.UnrealEditorSubsystem()
subsystem.set_level_viewport_camera_info(unreal.Vector(0, 0, 500000), unreal.Rotator(-90, 0, 0))
unreal.log('Camera moved!')