import unreal
import math
import json

# ============ CONFIG ============
VENUE_LAT = 31.1907
VENUE_LNG = 121.4742
VENUE_NAME = "Mercedes-Benz Arena"

# Nearby base stations (within 1km)
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

# ============ FIND GEOREFERENCE ============
geo = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if "CesiumGeoreference" in actor.get_class().get_name():
        geo = actor
        break

if not geo:
    unreal.log_error("CesiumGeoreference not found!")
else:
    # Set origin to venue
    geo.set_editor_property("origin_latitude", VENUE_LAT)
    geo.set_editor_property("origin_longitude", VENUE_LNG)
    geo.set_editor_property("origin_height", 0.0)
    unreal.log("Origin set to Mercedes-Benz Arena!")

# ============ HELPER: lat/lng to local XY ============
def geo_to_local(lat, lng, alt):
    dx = (lng - VENUE_LNG) * 111320 * math.cos(math.radians(VENUE_LAT))
    dy = (lat - VENUE_LAT) * 110540
    # UE5 uses cm, Cesium georeference handles conversion
    # With origin at venue, offsets are in meters from origin
    return dx * 100, dy * 100, alt * 100  # convert m to cm

# ============ SPAWN BASE STATIONS ============
station_actors = []
for st in STATIONS:
    x, y, z = geo_to_local(st["lat"], st["lng"], st["alt"])
    
    # Spawn a cylinder for each base station
    loc = unreal.Vector(x, y, z)
    
    # Use a static mesh actor with a cylinder
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.StaticMeshActor, loc
    )
    
    if actor:
        # Set name
        actor.set_actor_label("BS_" + st["id"])
        
        # Set cylinder mesh
        mesh_comp = actor.static_mesh_component
        cylinder = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
        if cylinder:
            mesh_comp.set_static_mesh(cylinder)
        
        # Scale: thin and tall based on station type
        height_scale = 3.0 if st["type"] == "macro" else 2.0 if st["type"] == "micro" else 1.0
        actor.set_actor_scale3d(unreal.Vector(0.3, 0.3, height_scale))
        
        # Create dynamic material for color control later
        mat = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
        if mat:
            mesh_comp.set_material(0, mat)
        
        station_actors.append(actor)
        unreal.log(f"Placed BS_{st['id']} at ({x:.0f}, {y:.0f}, {z:.0f})")

unreal.log(f"=== Placed {len(station_actors)} base stations ===")

# ============ SPAWN VENUE MARKER ============
venue_marker = unreal.EditorLevelLibrary.spawn_actor_from_class(
    unreal.StaticMeshActor, unreal.Vector(0, 0, 200)
)
if venue_marker:
    venue_marker.set_actor_label("VENUE_MercedesBenz")
    mesh_comp = venue_marker.static_mesh_component
    sphere = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    if sphere:
        mesh_comp.set_static_mesh(sphere)
    venue_marker.set_actor_scale3d(unreal.Vector(5, 5, 2))
    unreal.log("Venue marker placed!")

# ============ SET CAMERA ============
subsystem = unreal.UnrealEditorSubsystem()
subsystem.set_level_viewport_camera_info(
    unreal.Vector(0, -50000, 80000),
    unreal.Rotator(-45, 90, 0)
)
unreal.log("=== Scene setup complete! ===")