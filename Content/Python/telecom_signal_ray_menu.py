import os
import runpy

import unreal


MENU_OWNER = "TelecomTwinSignalRayDemo"
MENU_PATH = "LevelEditor.MainMenu.TelecomTwin"
SECTION_NAME = "SignalRayDemo"


def _sig_actors():
    return [
        actor
        for actor in unreal.EditorLevelLibrary.get_all_level_actors()
        if actor.get_actor_label().startswith("SIG_")
    ]


def _set_demo_visible(visible):
    changed = 0
    for actor in _sig_actors():
        actor.set_actor_hidden_in_game(not visible)
        actor.set_is_temporarily_hidden_in_editor(not visible)
        changed += 1
    unreal.log("[SignalRayDemo] Set {} SIG_ actors visible={}".format(changed, visible))


def show_signal_ray_demo():
    _set_demo_visible(True)


def hide_signal_ray_demo():
    _set_demo_visible(False)


def toggle_signal_ray_demo():
    actors = _sig_actors()
    if not actors:
        unreal.log_warning("[SignalRayDemo] No SIG_ actors found. Use Rebuild Signal Rays first.")
        return
    currently_visible = any(not actor.is_temporarily_hidden_in_editor() for actor in actors)
    _set_demo_visible(not currently_visible)


def rebuild_signal_ray_demo():
    project_dir = unreal.Paths.project_dir()
    script_path = os.path.join(project_dir, "Scripts", "SignalRayDemo", "build_signal_ray_demo.py")
    if not os.path.exists(script_path):
        unreal.log_error("[SignalRayDemo] Build script not found: {}".format(script_path))
        return
    unreal.log("[SignalRayDemo] Rebuilding from {}".format(script_path))
    runpy.run_path(script_path, run_name="__main__")


def _entry(label, tooltip, command):
    entry = unreal.ToolMenuEntry(
        name="SignalRayDemo_" + label.replace(" ", ""),
        type=unreal.MultiBlockType.MENU_ENTRY,
        insert_position=unreal.ToolMenuInsert("", unreal.ToolMenuInsertType.DEFAULT),
    )
    entry.set_label(label)
    entry.set_tool_tip(tooltip)
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "", command)
    return entry


def register_menu():
    menus = unreal.ToolMenus.get()
    main_menu = menus.find_menu("LevelEditor.MainMenu")
    if not main_menu:
        unreal.log_warning("[SignalRayDemo] LevelEditor.MainMenu not found; menu was not registered.")
        return

    try:
        menus.unregister_owner_by_name(MENU_OWNER)
    except Exception:
        pass

    main_menu.add_sub_menu(
        MENU_OWNER,
        "TelecomTwin",
        "TelecomTwin",
        "TelecomTwin",
        "TelecomTwin project tools",
    )
    menu = menus.find_menu(MENU_PATH)
    if not menu:
        unreal.log_warning("[SignalRayDemo] TelecomTwin submenu was not created.")
        return

    menu.add_section(SECTION_NAME, "Signal Ray Demo")
    menu.add_menu_entry(
        SECTION_NAME,
        _entry(
            "Show Signal Rays",
            "Show all SIG_ signal ray demo actors.",
            "import telecom_signal_ray_menu; telecom_signal_ray_menu.show_signal_ray_demo()",
        ),
    )
    menu.add_menu_entry(
        SECTION_NAME,
        _entry(
            "Hide Signal Rays",
            "Hide all SIG_ signal ray demo actors without deleting them.",
            "import telecom_signal_ray_menu; telecom_signal_ray_menu.hide_signal_ray_demo()",
        ),
    )
    menu.add_menu_entry(
        SECTION_NAME,
        _entry(
            "Toggle Signal Rays",
            "Toggle visibility for all SIG_ signal ray demo actors.",
            "import telecom_signal_ray_menu; telecom_signal_ray_menu.toggle_signal_ray_demo()",
        ),
    )
    menu.add_menu_entry(
        SECTION_NAME,
        _entry(
            "Rebuild Signal Rays",
            "Rebuild the signal ray demo from the project script.",
            "import telecom_signal_ray_menu; telecom_signal_ray_menu.rebuild_signal_ray_demo()",
        ),
    )
    menus.refresh_all_widgets()
    unreal.log("[SignalRayDemo] TelecomTwin menu registered.")
