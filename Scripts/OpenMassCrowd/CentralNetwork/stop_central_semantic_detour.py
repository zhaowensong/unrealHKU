"""Stop the bounded Central semantic-detour editor callback."""

import builtins
import unreal


handle = getattr(builtins, "_hk_central_semantic_detour_callback", None)
if handle is not None:
    unreal.unregister_slate_post_tick_callback(handle)
setattr(builtins, "_hk_central_semantic_detour_callback", None)
unreal.log_warning("OPEN_MASS_CROWD_CENTRAL_SEMANTIC_DETOUR_STOPPED_FOR_PATCH")
