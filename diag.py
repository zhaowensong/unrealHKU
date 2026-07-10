import unreal

# Find Cesium georeference
geo = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    cn = actor.get_class().get_name()
    if "Georeference" in cn:
        geo = actor
        unreal.log(f"Found: {cn} - {actor.get_name()}")
        # List all methods
        for m in dir(actor):
            if "transform" in m.lower() or "longi" in m.lower() or "unreal" in m.lower():
                unreal.log(f"  Method: {m}")
        break

if geo:
    # Try Cesium coordinate transform
    try:
        pos = geo.transform_longitude_latitude_height_position_to_unreal(unreal.Vector(121.4742, 31.1907, 50))
        unreal.log(f"Venue at UE coords: {pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}")
    except Exception as e:
        unreal.log_error(f"Transform failed: {e}")
    # Check origin
    lat = geo.get_editor_property("origin_latitude")
    lng = geo.get_editor_property("origin_longitude")
    unreal.log(f"Origin: lat={lat}, lng={lng}")

# Check material parameters
mat = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
if mat:
    unreal.log(f"Material class: {mat.get_class().get_name()}")

# Test spawn at origin
test = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 5000))
if test:
    test.set_actor_label("TEST_ORIGIN")
    mc = test.static_mesh_component
    sph = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    if sph:
        mc.set_static_mesh(sph)
    test.set_actor_scale3d(unreal.Vector(50, 50, 50))
    unreal.log(f"Test sphere at origin (0,0,5000), scale 50")