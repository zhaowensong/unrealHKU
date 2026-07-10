import unreal

VENUE_LAT = 31.1907
VENUE_LNG = 121.4742

STATIONS = [
    {"id": "F0102", "lat": 31.189896, "lng": 121.476913, "alt": 17, "type": "micro"},
    {"id": "F2C11", "lat": 31.188229, "lng": 121.477448, "alt": 16, "type": "micro"},
    {"id": "F0111", "lat": 31.187998, "lng": 121.477455, "alt": 44, "type": "micro"},
    {"id": "F2EB3", "lat": 31.187250, "lng": 121.478760, "alt": 22, "type": "macro"},
    {"id": "F0103", "lat": 31.194567, "lng": 121.469498, "alt": 32, "type": "macro"},
    {"id": "F2F81", "lat": 31.185930, "lng": 121.478546, "alt": 88, "type": "macro"},
    {"id": "F0113", "lat": 31.184826, "lng": 121.471008, "alt": 8, "type": "micro"},
    {"id": "F0D83", "lat": 31.192776, "lng": 121.467834, "alt": 25, "type": "micro"},
    {"id": "F3D42", "lat": 31.197618, "lng": 121.473343, "alt": 7, "type": "macro"},
    {"id": "F1121", "lat": 31.197313, "lng": 121.470390, "alt": 46, "type": "micro"},
]

# Get georeference
geo = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if "Georeference" in actor.get_class().get_name():
        geo = actor
        break

# Clean old actors
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if any(label.startswith(p) for p in ["BS_","VENUE_","Light_","Beam_","TEST_"]):
        unreal.EditorLevelLibrary.destroy_actor(actor)
unreal.log("Cleaned")

# Get ground level Z at origin
ground = geo.transform_longitude_latitude_height_position_to_unreal(unreal.Vector(VENUE_LNG, VENUE_LAT, 0))
unreal.log(f"Ground Z = {ground.z:.0f}")

cyl = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
sph = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")

# Place base stations using Cesium transform
for st in STATIONS:
    pos = geo.transform_longitude_latitude_height_position_to_unreal(
        unreal.Vector(st["lng"], st["lat"], st["alt"])
    )
    # Beam from station up 500m
    beam_top = geo.transform_longitude_latitude_height_position_to_unreal(
        unreal.Vector(st["lng"], st["lat"], st["alt"] + 500)
    )
    beam_center_z = (pos.z + beam_top.z) / 2
    beam_height = abs(beam_top.z - pos.z) / 100  # scale units

    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(pos.x, pos.y, beam_center_z)
    )
    if actor:
        actor.set_actor_label("Beam_" + st["id"])
        mc = actor.static_mesh_component
        if cyl:
            mc.set_static_mesh(cyl)
        actor.set_actor_scale3d(unreal.Vector(2, 2, beam_height))
        unreal.log(f"Beam_{st['id']} at ({pos.x:.0f},{pos.y:.0f},{pos.z:.0f}) h={beam_height:.0f}")

    # Bright cyan point light
    light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, pos)
    if light:
        light.set_actor_label("Light_" + st["id"])
        lc = light.point_light_component
        lc.set_light_color(unreal.LinearColor(0.0, 0.8, 1.0, 1.0))
        lc.set_editor_property("intensity", 2000000.0)
        lc.set_editor_property("attenuation_radius", 100000.0)

# Venue marker
v_pos = geo.transform_longitude_latitude_height_position_to_unreal(
    unreal.Vector(VENUE_LNG, VENUE_LAT, 100)
)
venue = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, v_pos)
if venue:
    venue.set_actor_label("VENUE_Center")
    mc = venue.static_mesh_component
    if sph:
        mc.set_static_mesh(sph)
    venue.set_actor_scale3d(unreal.Vector(80, 80, 30))

# Huge venue light
vl = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, v_pos)
if vl:
    vl.set_actor_label("Light_Venue")
    vlc = vl.point_light_component
    vlc.set_light_color(unreal.LinearColor(1.0, 0.2, 0.0, 1.0))
    vlc.set_editor_property("intensity", 10000000.0)
    vlc.set_editor_property("attenuation_radius", 200000.0)

unreal.log("=== Done with Cesium coords! ===")