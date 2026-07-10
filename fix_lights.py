import unreal

# Delete the dark venue sphere, keep beams and lights
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if label == "VENUE_Center":
        unreal.EditorLevelLibrary.destroy_actor(actor)
        unreal.log("Removed dark venue sphere")

# Check how many lights and beams exist
lights = 0
beams = 0
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if label.startswith("Light_"):
        lights += 1
    if label.startswith("Beam_"):
        beams += 1
unreal.log(f"Scene has {beams} beams, {lights} lights")

# Make all lights MUCH brighter and visible in editor
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if label.startswith("Light_") and label != "Light_Venue":
        lc = actor.point_light_component
        lc.set_editor_property("intensity", 500000000.0)
        lc.set_editor_property("attenuation_radius", 500000.0)
        lc.set_editor_property("cast_shadows", False)
    if label == "Light_Venue":
        lc = actor.point_light_component
        lc.set_editor_property("intensity", 5000000000.0)
        lc.set_editor_property("attenuation_radius", 1000000.0)
        lc.set_editor_property("cast_shadows", False)

unreal.log("Lights boosted!")