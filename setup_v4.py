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

geo = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if "Georeference" in actor.get_class().get_name():
        geo = actor
        break

for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    label = actor.get_actor_label()
    if any(label.startswith(p) for p in ["BS_","VENUE_","Light_","Beam_","TEST_"]):
        unreal.EditorLevelLibrary.destroy_actor(actor)

# Figure out scale: 1m in real = how many UE units?
p0 = geo.transform_longitude_latitude_height_position_to_unreal(unreal.Vector(VENUE_LNG, VENUE_LAT, 0))
p1 = geo.transform_longitude_latitude_height_position_to_unreal(unreal.Vector(VENUE_LNG, VENUE_LAT, 1))
SCALE = abs(p1.z - p0.z)  # UE units per meter
unreal.log(f"Scale: 1m = {SCALE:.0f} UE units")

cyl = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
sph = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
# Cylinder default: 100 UE units tall, 100 wide (radius 50)
# To make Xm wide: scale = X * SCALE / 100
# To make Ym tall: scale = Y * SCALE / 100

def m_to_scale(meters):
    return meters * SCALE / 100.0

for st in STATIONS:
    base = geo.transform_longitude_latitude_height_position_to_unreal(
        unreal.Vector(st["lng"], st["lat"], float(st["alt"]))
    )
    top = geo.transform_longitude_latitude_height_position_to_unreal(
        unreal.Vector(st["lng"], st["lat"], float(st["alt"]) + 300.0)
    )
    mid = unreal.Vector(base.x, base.y, (base.z + top.z) / 2.0)
    h_scale = abs(top.z - base.z) / 100.0
    w_scale = m_to_scale(5)  # 5m wide beam

    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, mid)
    if actor:
        actor.set_actor_label("Beam_" + st["id"])
        mc = actor.static_mesh_component
        if cyl:
            mc.set_static_mesh(cyl)
        actor.set_actor_scale3d(unreal.Vector(w_scale, w_scale, h_scale))

    light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, top)
    if light:
        light.set_actor_label("Light_" + st["id"])
        lc = light.point_light_component
        lc.set_light_color(unreal.LinearColor(0.0, 0.8, 1.0, 1.0))
        lc.set_editor_property("intensity", 50000000.0)
        lc.set_editor_property("attenuation_radius", SCALE * 500)

unreal.log(f"Placed {len(STATIONS)} beams (w={w_scale:.0f}, h={h_scale:.0f})")

# Venue: 100m sphere
v_pos = geo.transform_longitude_latitude_height_position_to_unreal(
    unreal.Vector(VENUE_LNG, VENUE_LAT, 50.0)
)
venue = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, v_pos)
if venue:
    venue.set_actor_label("VENUE_Center")
    mc = venue.static_mesh_component
    if sph:
        mc.set_static_mesh(sph)
    s = m_to_scale(100)
    venue.set_actor_scale3d(unreal.Vector(s, s, s * 0.3))

vl = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, v_pos)
if vl:
    vl.set_actor_label("Light_Venue")
    vlc = vl.point_light_component
    vlc.set_light_color(unreal.LinearColor(1.0, 0.2, 0.0, 1.0))
    vlc.set_editor_property("intensity", 200000000.0)
    vlc.set_editor_property("attenuation_radius", SCALE * 2000)

unreal.log("=== v4 done! ===")