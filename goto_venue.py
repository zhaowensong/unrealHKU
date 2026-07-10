import unreal

VENUE_LAT = 31.1907
VENUE_LNG = 121.4742

geo = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if "Georeference" in actor.get_class().get_name():
        geo = actor
        break

# Camera at 300m above venue, offset 200m south
cam_pos = geo.transform_longitude_latitude_height_position_to_unreal(
    unreal.Vector(VENUE_LNG, VENUE_LAT - 0.002, 300.0)
)
# Look target = venue at ground
look_at = geo.transform_longitude_latitude_height_position_to_unreal(
    unreal.Vector(VENUE_LNG, VENUE_LAT, 0.0)
)

subsystem = unreal.UnrealEditorSubsystem()
# Calculate look direction
import math
dx = look_at.x - cam_pos.x
dy = look_at.y - cam_pos.y
dz = look_at.z - cam_pos.z
dist_h = math.sqrt(dx*dx + dy*dy)
pitch = math.degrees(math.atan2(dz, dist_h))
yaw = math.degrees(math.atan2(dy, dx))

subsystem.set_level_viewport_camera_info(cam_pos, unreal.Rotator(pitch, yaw, 0))
unreal.log(f"Camera at ({cam_pos.x:.0f},{cam_pos.y:.0f},{cam_pos.z:.0f})")
unreal.log(f"Pitch={pitch:.1f} Yaw={yaw:.1f}")
unreal.log("Camera moved to venue!")