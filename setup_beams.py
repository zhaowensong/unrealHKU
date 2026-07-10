import unreal
import math

VENUE_LAT = 31.1907
VENUE_LNG = 121.4742

STATIONS = [
    {"id": "F0102", "lat": 31.189896, "lng": 121.476913, "alt": 17, "type": "micro", "cap": 100},
    {"id": "F2C11", "lat": 31.188229, "lng": 121.477448, "alt": 16, "type": "micro", "cap": 500},
    {"id": "F0111", "lat": 31.187998, "lng": 121.477455, "alt": 44, "type": "micro", "cap": 500},
    {"id": "F2EB3", "lat": 31.187250, "lng": 121.478760, "alt": 22, "type": "macro", "cap": 500},
    {"id": "F0103", "lat": 31.194567, "lng": 121.469498, "alt": 32, "type": "macro", "cap": 100},
    {"id": "F2F81", "lat": 31.185930, "lng": 121.478546, "alt": 88, "type": "macro", "cap": 50},
    {"id": "F0113", "lat": 31.184826, "lng": 121.471008, "alt": 8, "type": "micro", "cap": 500},
    {"id": "F0D83", "lat": 31.192776, "lng": 121.467834, "alt": 25, "type": "micro", "cap": 200},
    {"id": "F32D2", "lat": 31.195181, "lng": 121.469147, "alt": 16, "type": "micro", "cap": 100},
    {"id": "F32D3", "lat": 31.193785, "lng": 121.468033, "alt": 13, "type": "micro", "cap": 200},
    {"id": "F3D42", "lat": 31.197618, "lng": 121.473343, "alt": 7, "type": "macro", "cap": 100},
    {"id": "F32D1", "lat": 31.195131, "lng": 121.468468, "alt": 4, "type": "micro", "cap": 100},
    {"id": "F2F43", "lat": 31.197309, "lng": 121.477921, "alt": 77, "type": "micro", "cap": 200},
    {"id": "F1121", "lat": 31.197313, "lng": 121.470390, "alt": 46, "type": "micro", "cap": 100},
    {"id": "F1621", "lat": 31.193024, "lng": 121.466789, "alt": 11, "type": "pico", "cap": 50},
]

# Clean old
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if label.startswith("BS_") or label.startswith("VENUE_") or label.startswith("Light_") or label.startswith("Beam_"):
        unreal.EditorLevelLibrary.destroy_actor(actor)
unreal.log("Cleaned")

def geo_to_local(lat, lng, alt):
    dx = (lng - VENUE_LNG) * 111320 * math.cos(math.radians(VENUE_LAT))
    dy = (lat - VENUE_LAT) * 110540
    return dx * 100, dy * 100, alt * 100

# Giant sky beams for base stations
for st in STATIONS:
    x, y, z = geo_to_local(st["lat"], st["lng"], st["alt"])
    # Beam: very thin, very tall cylinder shooting up from ground
    beam_z = 30000  # beam center at 300m height
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(x, y, beam_z))
    if actor:
        actor.set_actor_label("Beam_" + st["id"])
        mc = actor.static_mesh_component
        cyl = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
        if cyl:
            mc.set_static_mesh(cyl)
        # Thin beam, 600m tall (visible from space view)
        h = 400.0 if st["type"] == "macro" else 250.0 if st["type"] == "micro" else 150.0
        actor.set_actor_scale3d(unreal.Vector(1.5, 1.5, h))
        dyn = mc.create_dynamic_material_instance(0)
        if dyn:
            dyn.set_vector_parameter_value("Color", unreal.LinearColor(0, 8, 12, 1))
    # Spot light shooting up
    light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, unreal.Vector(x, y, z + 500))
    if light:
        light.set_actor_label("Light_" + st["id"])
        lc = light.point_light_component
        lc.set_light_color(unreal.LinearColor(0, 0.8, 1, 1))
        lc.set_editor_property("intensity", 500000.0)
        lc.set_editor_property("attenuation_radius", 30000.0)

unreal.log(f"Placed {len(STATIONS)} sky beams")

# Venue: huge red beam
venue = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 50000))
if venue:
    venue.set_actor_label("VENUE_MercedesBenz")
    mc = venue.static_mesh_component
    cyl = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
    if cyl:
        mc.set_static_mesh(cyl)
    venue.set_actor_scale3d(unreal.Vector(4, 4, 600))
    dyn = mc.create_dynamic_material_instance(0)
    if dyn:
        dyn.set_vector_parameter_value("Color", unreal.LinearColor(15, 2, 0, 1))

# Venue ring on ground
ring = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 100))
if ring:
    ring.set_actor_label("VENUE_Ring")
    mc = ring.static_mesh_component
    sph = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    if sph:
        mc.set_static_mesh(sph)
    ring.set_actor_scale3d(unreal.Vector(30, 30, 1))
    dyn = mc.create_dynamic_material_instance(0)
    if dyn:
        dyn.set_vector_parameter_value("Color", unreal.LinearColor(12, 3, 0, 1))

vl = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, unreal.Vector(0, 0, 5000))
if vl:
    vl.set_actor_label("Light_Venue")
    vlc = vl.point_light_component
    vlc.set_light_color(unreal.LinearColor(1, 0.2, 0, 1))
    vlc.set_editor_property("intensity", 2000000.0)
    vlc.set_editor_property("attenuation_radius", 80000.0)

unreal.log("=== Sky beams scene done! ===")