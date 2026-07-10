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

# Delete old actors
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if label.startswith("BS_") or label.startswith("VENUE_") or label.startswith("Light_"):
        unreal.EditorLevelLibrary.destroy_actor(actor)
unreal.log("Cleaned up")

def geo_to_local(lat, lng, alt):
    dx = (lng - VENUE_LNG) * 111320 * math.cos(math.radians(VENUE_LAT))
    dy = (lat - VENUE_LAT) * 110540
    return dx * 100, dy * 100, alt * 100

for st in STATIONS:
    x, y, z = geo_to_local(st["lat"], st["lng"], st["alt"])
    loc = unreal.Vector(x, y, z)
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, loc)
    if actor:
        actor.set_actor_label("BS_" + st["id"])
        mc = actor.static_mesh_component
        cyl = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
        if cyl:
            mc.set_static_mesh(cyl)
        h = 6.0 if st["type"] == "macro" else 4.0 if st["type"] == "micro" else 2.5
        actor.set_actor_scale3d(unreal.Vector(0.5, 0.5, h))
        dyn = mc.create_dynamic_material_instance(0)
        if dyn:
            dyn.set_vector_parameter_value("Color", unreal.LinearColor(0, 5, 8, 1))
    light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, loc)
    if light:
        light.set_actor_label("Light_" + st["id"])
        lc = light.point_light_component
        lc.set_light_color(unreal.LinearColor(0, 0.8, 1, 1))
        lc.set_editor_property("intensity", 50000.0)
        lc.set_editor_property("attenuation_radius", 5000.0)

unreal.log(f"Placed {len(STATIONS)} stations")

venue = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 500))
if venue:
    venue.set_actor_label("VENUE_MercedesBenz")
    mc = venue.static_mesh_component
    sph = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    if sph:
        mc.set_static_mesh(sph)
    venue.set_actor_scale3d(unreal.Vector(8, 8, 3))
    dyn = mc.create_dynamic_material_instance(0)
    if dyn:
        dyn.set_vector_parameter_value("Color", unreal.LinearColor(10, 2, 0, 1))

vl = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, unreal.Vector(0, 0, 1000))
if vl:
    vl.set_actor_label("Light_Venue")
    vlc = vl.point_light_component
    vlc.set_light_color(unreal.LinearColor(1, 0.3, 0, 1))
    vlc.set_editor_property("intensity", 200000.0)
    vlc.set_editor_property("attenuation_radius", 15000.0)

unreal.log("=== Bright scene done! ===")