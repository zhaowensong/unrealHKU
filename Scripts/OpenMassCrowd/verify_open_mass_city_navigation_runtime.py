"""Verify the Cesium-grounded, branching OpenMassCrowd navigation demo in PIE.

Execute this file through ``run_unreal_python_via_mcp.py`` while TelecomTwin is
open.  The verifier waits for a PIE world, observes it without starting or
stopping PIE, and writes both timestamped evidence and the stable path
``Saved/Reports/open_mass_city_navigation_runtime_latest.json``.

The checks deliberately use public runtime evidence only: the spawner's
Blueprint getters, the active representation actors, sampled movement, and
line traces against loaded ``CesiumGltfPrimitiveComponent`` geometry.  Slate
post-tick deltas are reported as an editor-frame performance proxy; they are
advisory because they include editor and verifier overhead.
"""

from __future__ import annotations

import builtins
import ctypes
import json
import math
import statistics
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import unreal


TARGET_POPULATION = 30
EXPECTED_NETWORK_NODES = 9
MINIMUM_DIRECTED_LANES = 16
MAXIMUM_DIRECTED_LANES = 24

POLL_INTERVAL_SECONDS = 0.25
POSITION_SAMPLE_INTERVAL_SECONDS = 0.5
MIN_SAMPLE_SECONDS = 20.0
MAX_SAMPLE_SECONDS = 60.0
WAIT_TIMEOUT_SECONDS = 1800.0

MIN_ACTOR_PATH_CM = 150.0
STATIONARY_STEP_CM = 10.0
POSSIBLE_STUCK_SECONDS = 5.0
TRACK_REBIND_MAX_DISTANCE_CM = 250.0

MIN_COVERAGE_X_CM = 1000.0
MIN_COVERAGE_Y_CM = 300.0
MIN_DIRECTION_BINS = 4
MIN_AXIS_ACTIVE_ACTORS = 10
AXIS_ACTIVE_DISTANCE_CM = 150.0
HEADING_SEGMENT_MIN_CM = 15.0
TURN_THRESHOLD_DEGREES = 45.0
COARSE_CELL_SIZE_CM = 200.0

AGENT_RADIUS_CM = 30.0
SEVERE_OVERLAP_DISTANCE_CM = 20.0

EXPECTED_ROOT_GROUND_OFFSET_CM = 2.0
ROOT_GROUND_TOLERANCE_CM = 8.0
# Ground correction is capped at 20 Hz while entities move. The instantaneous
# envelope records the expected between-correction excursion without replacing
# the stricter 8 cm correction-point check.
MAX_INSTANTANEOUS_ROOT_ERROR_CM = 35.0
GROUND_VALIDATION_SAMPLE_COUNT = 3
GROUND_VALIDATION_INTERVAL_SECONDS = 0.25
MIN_WALKABLE_NORMAL_Z = 0.72
TRACE_HEIGHT_CM = 1600.0
TRACE_DEPTH_CM = 2600.0
# Exact XY only: the verifier must never certify an unsupported actor position
# by borrowing the height of a neighbouring collision triangle.
CESIUM_PROBE_OFFSETS_CM = ((0.0, 0.0),)

MAX_REASONABLE_REPLANS = 3
ADVISORY_MIN_MEDIAN_FPS = 15.0

CALLBACK_KEY = "_hk_open_mass_city_navigation_verify_handle"
STATE_KEY = "_hk_open_mass_city_navigation_verify_state"
REPORT_BASENAME = "open_mass_city_navigation_runtime"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def object_path(value):
    if value is None:
        return None
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def class_name(value):
    if value is None:
        return None
    try:
        return value.get_class().get_name()
    except Exception:
        return type(value).__name__


def actor_name(actor):
    try:
        return actor.get_name()
    except Exception:
        return object_path(actor) or "<unknown-actor>"


def actor_label(actor):
    try:
        return actor.get_actor_label()
    except Exception:
        return actor_name(actor)


def vector_list(value):
    return [round(float(value.x), 3), round(float(value.y), 3), round(float(value.z), 3)]


def hit_property(hit, property_name, default=None):
    try:
        return getattr(hit, property_name)
    except Exception:
        try:
            return hit.get_editor_property(property_name)
        except Exception:
            return default


def distance_squared(first, second):
    return (
        (float(second.x) - float(first.x)) ** 2
        + (float(second.y) - float(first.y)) ** 2
        + (float(second.z) - float(first.z)) ** 2
    )


def distance_xy(first, second):
    return math.hypot(second[0] - first[0], second[1] - first[1])


def append_error(state, stage, error):
    errors = state.setdefault("errors", [])
    if len(errors) >= 100:
        return
    text = repr(error)
    if any(item["stage"] == stage and item["error"] == text for item in errors):
        return
    errors.append(
        {
            "stage": stage,
            "error": text,
            "traceback": traceback.format_exc().replace(chr(10), " | "),
        }
    )


def get_pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    if world is not None:
        return world
    fallback = getattr(unreal.EditorLevelLibrary, "get_game_world", None)
    return fallback() if fallback is not None else None


def actors_of_class(world, actor_class):
    if world is None or actor_class is None:
        return []
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, actor_class))


def all_world_actors(world):
    return actors_of_class(world, unreal.Actor)


def actor_hidden(actor):
    try:
        return bool(actor.get_editor_property("hidden"))
    except Exception:
        try:
            return bool(actor.is_hidden())
        except Exception:
            return False


def call_int_getter(actor, python_name, state):
    try:
        return int(getattr(actor, python_name)())
    except Exception as error:
        append_error(state, "spawner." + python_name, error)
        return None


def collect_runtime_snapshot(world, state):
    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    proxy_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    spawners = actors_of_class(world, spawner_class)
    proxies = actors_of_class(world, proxy_class)
    active_proxies = [actor for actor in proxies if not actor_hidden(actor)]

    metrics = {
        "requested_population": None,
        "spawned_entities": None,
        "network_nodes": None,
        "directed_lanes": None,
        "route_assignments": None,
        "completed_trips": None,
        "route_replans": None,
        "ground_projection_failures": None,
        "ground_center_recoveries": None,
        "ground_rollbacks": None,
        "ground_unrecoverable": None,
        "current_unsupported_visuals": None,
        "high_res_representations": None,
        "low_res_representations": None,
    }
    if len(spawners) == 1:
        spawner = spawners[0]
        try:
            metrics["requested_population"] = int(
                spawner.get_editor_property("population_count")
            )
        except Exception as error:
            append_error(state, "spawner.population_count", error)
        getter_names = {
            "spawned_entities": "get_spawned_entity_count",
            "network_nodes": "get_runtime_network_node_count",
            "directed_lanes": "get_runtime_lane_count",
            "route_assignments": "get_route_assignment_count",
            "completed_trips": "get_completed_trip_count",
            "route_replans": "get_route_replan_count",
            "ground_projection_failures": "get_ground_projection_failure_count",
            "ground_center_recoveries": "get_ground_center_recovery_count",
            "ground_rollbacks": "get_ground_rollback_count",
            "ground_unrecoverable": "get_ground_unrecoverable_count",
            "current_unsupported_visuals": "get_current_unsupported_visual_count",
            "high_res_representations": "get_current_high_res_representation_count",
            "low_res_representations": "get_current_low_res_representation_count",
        }
        for key, getter_name in getter_names.items():
            metrics[key] = call_int_getter(spawner, getter_name, state)

    position_records = []
    for proxy in sorted(active_proxies, key=actor_name):
        try:
            mass_identity = int(proxy.get_mass_appearance_seed())
        except Exception as error:
            append_error(state, "proxy.get_mass_appearance_seed", error)
            mass_identity = -1
        position_records.append(
            {
                "actor_name": actor_name(proxy),
                "actor_class": class_name(proxy),
                "mass_identity": mass_identity,
                "location": vector_list(proxy.get_actor_location()),
            }
        )
    positions = {
        str(item["mass_identity"]): item["location"]
        for item in position_records
        if item["mass_identity"] >= 0
    }

    return {
        "world": object_path(world),
        "world_name": world.get_name(),
        "spawner_count": len(spawners),
        "spawner_labels": [actor_label(actor) for actor in spawners],
        "proxy_actor_count": len(proxies),
        "active_proxy_actor_count": len(active_proxies),
        "stable_mass_identity_count": len(positions),
        "representation_actor_classes": sorted(
            {item["actor_class"] for item in position_records if item["actor_class"]}
        ),
        "positions": positions,
        "position_records": position_records,
        **metrics,
    }


def snapshot_ready(snapshot):
    high = snapshot.get("high_res_representations")
    low = snapshot.get("low_res_representations")
    represented = high is not None and low is not None and high + low == TARGET_POPULATION
    directed_lanes = snapshot.get("directed_lanes")
    certified_lane_count = (
        directed_lanes is not None
        and MINIMUM_DIRECTED_LANES <= directed_lanes <= MAXIMUM_DIRECTED_LANES
        and directed_lanes % 2 == 0
    )
    return (
        snapshot.get("spawner_count") == 1
        and snapshot.get("requested_population") == TARGET_POPULATION
        and snapshot.get("spawned_entities") == TARGET_POPULATION
        and snapshot.get("proxy_actor_count") == TARGET_POPULATION
        and snapshot.get("active_proxy_actor_count") == TARGET_POPULATION
        and snapshot.get("stable_mass_identity_count") == TARGET_POPULATION
        and snapshot.get("network_nodes") == EXPECTED_NETWORK_NODES
        and certified_lane_count
        and (snapshot.get("route_assignments") or 0) >= TARGET_POPULATION
        and represented
    )


def new_track(track_index, actor, sample_time):
    return {
        "track_id": "mass_{}".format(actor["mass_identity"]),
        "mass_identity": actor["mass_identity"],
        "current_actor_name": actor["actor_name"],
        "actor_names": [actor["actor_name"]],
        "actor_classes": [actor["actor_class"]],
        "samples": [
            {
                "time_seconds": round(sample_time, 3),
                "location": actor["location"],
            }
        ],
    }


def append_track_sample(track, actor, sample_time):
    track["mass_identity"] = actor["mass_identity"]
    track["current_actor_name"] = actor["actor_name"]
    if actor["actor_name"] not in track["actor_names"]:
        track["actor_names"].append(actor["actor_name"])
    if actor["actor_class"] not in track["actor_classes"]:
        track["actor_classes"].append(actor["actor_class"])
    track["samples"].append(
        {
            "time_seconds": round(sample_time, 3),
            "location": actor["location"],
        }
    )


def update_tracks(state, snapshot, sample_time):
    actors = [dict(item) for item in snapshot["position_records"]]
    tracks = state.setdefault("tracks", [])
    if not tracks:
        for index, actor in enumerate(actors):
            tracks.append(new_track(index, actor, sample_time))
        return

    unmatched_actor_indices = set(range(len(actors)))
    unmatched_track_indices = set(range(len(tracks)))
    actor_index_by_identity = {
        actor["mass_identity"]: index
        for index, actor in enumerate(actors)
        if actor["mass_identity"] >= 0
    }

    # A Mass appearance seed is stable across High/Low actor replacement. Actor
    # names are deliberately retained only as audit detail, never as identity.
    for track_index, track in enumerate(tracks):
        actor_index = actor_index_by_identity.get(track["mass_identity"])
        if actor_index is None or actor_index not in unmatched_actor_indices:
            continue
        append_track_sample(track, actors[actor_index], sample_time)
        unmatched_track_indices.discard(track_index)
        unmatched_actor_indices.discard(actor_index)

    candidates = []
    for track_index in unmatched_track_indices:
        last_position = tracks[track_index]["samples"][-1]["location"]
        for actor_index in unmatched_actor_indices:
            candidates.append(
                (
                    distance_xy(last_position, actors[actor_index]["location"]),
                    track_index,
                    actor_index,
                )
            )
    for distance, track_index, actor_index in sorted(candidates):
        if track_index not in unmatched_track_indices:
            continue
        if actor_index not in unmatched_actor_indices:
            continue
        if distance > TRACK_REBIND_MAX_DISTANCE_CM:
            continue
        append_track_sample(tracks[track_index], actors[actor_index], sample_time)
        unmatched_track_indices.discard(track_index)
        unmatched_actor_indices.discard(actor_index)

    # This should not occur with a fixed 30-entity population, but retaining
    # unmatched representations makes an identity/pooling anomaly explicit.
    for actor_index in sorted(unmatched_actor_indices):
        tracks.append(new_track(len(tracks), actors[actor_index], sample_time))
    if unmatched_track_indices:
        state["unmatched_track_events"] = state.get("unmatched_track_events", 0) + len(
            unmatched_track_indices
        )


def pairwise_frame_metrics(snapshot):
    positions = snapshot["positions"]
    names = sorted(positions)
    minimum = None
    body_overlaps = []
    severe_overlaps = []
    for first_index in range(len(names)):
        for second_index in range(first_index + 1, len(names)):
            first_name = names[first_index]
            second_name = names[second_index]
            distance = distance_xy(positions[first_name], positions[second_name])
            minimum = distance if minimum is None else min(minimum, distance)
            pair = [first_name, second_name, round(distance, 3)]
            if distance < AGENT_RADIUS_CM * 2.0:
                body_overlaps.append(pair)
            if distance < SEVERE_OVERLAP_DISTANCE_CM:
                severe_overlaps.append(pair)
    return {
        "minimum_distance_cm": round(minimum, 3) if minimum is not None else None,
        "agent_body_overlap_pair_count": len(body_overlaps),
        "severe_overlap_pair_count": len(severe_overlaps),
        "agent_body_overlap_samples": body_overlaps[:10],
        "severe_overlap_samples": severe_overlaps[:10],
    }


def record_position_sample(state, snapshot, game_elapsed):
    update_tracks(state, snapshot, game_elapsed)
    pairwise = pairwise_frame_metrics(snapshot)
    state.setdefault("sample_summaries", []).append(
        {
            "time_seconds": round(game_elapsed, 3),
            "actor_count": len(snapshot["positions"]),
            "route_assignments": snapshot.get("route_assignments"),
            "completed_trips": snapshot.get("completed_trips"),
            "route_replans": snapshot.get("route_replans"),
            "high_res_representations": snapshot.get("high_res_representations"),
            "low_res_representations": snapshot.get("low_res_representations"),
            **pairwise,
        }
    )
    state.setdefault("representation_observations", []).append(
        [
            snapshot.get("high_res_representations"),
            snapshot.get("low_res_representations"),
        ]
    )


def circular_heading_delta(first, second):
    delta = abs(second - first) % 360.0
    return min(delta, 360.0 - delta)


def movement_and_branching_report(state):
    actor_reports = []
    direction_bins = set()
    all_points = []
    coarse_cells = set()

    for track in state.get("tracks", []):
        samples = track["samples"]
        points = [sample["location"] for sample in samples]
        all_points.extend(points)
        for point in points:
            coarse_cells.add(
                (
                    int(math.floor(point[0] / COARSE_CELL_SIZE_CM)),
                    int(math.floor(point[1] / COARSE_CELL_SIZE_CM)),
                )
            )

        path_length = 0.0
        cumulative_x = 0.0
        cumulative_y = 0.0
        headings = []
        longest_stationary = 0.0
        stationary_run = 0.0
        for first, second in zip(samples, samples[1:]):
            first_location = first["location"]
            second_location = second["location"]
            delta_x = second_location[0] - first_location[0]
            delta_y = second_location[1] - first_location[1]
            segment = math.hypot(delta_x, delta_y)
            delta_time = max(0.0, second["time_seconds"] - first["time_seconds"])
            path_length += segment
            cumulative_x += abs(delta_x)
            cumulative_y += abs(delta_y)
            if segment < STATIONARY_STEP_CM:
                stationary_run += delta_time
                longest_stationary = max(longest_stationary, stationary_run)
            else:
                stationary_run = 0.0
            if segment >= HEADING_SEGMENT_MIN_CM:
                heading = math.degrees(math.atan2(delta_y, delta_x)) % 360.0
                headings.append(heading)
                direction_bins.add(int((heading + 22.5) // 45.0) % 8)

        meaningful_turns = sum(
            circular_heading_delta(first, second) >= TURN_THRESHOLD_DEGREES
            for first, second in zip(headings, headings[1:])
        )
        net_distance = distance_xy(points[0], points[-1]) if len(points) >= 2 else 0.0
        possible_stuck = (
            path_length < MIN_ACTOR_PATH_CM
            or longest_stationary >= POSSIBLE_STUCK_SECONDS
        )
        actor_reports.append(
            {
                "track_id": track["track_id"],
                "mass_identity": track["mass_identity"],
                "actor_names": track["actor_names"],
                "actor_classes": track["actor_classes"],
                "sample_count": len(samples),
                "start_location": points[0] if points else None,
                "end_location": points[-1] if points else None,
                "cumulative_path_cm": round(path_length, 3),
                "net_displacement_cm": round(net_distance, 3),
                "cumulative_abs_x_cm": round(cumulative_x, 3),
                "cumulative_abs_y_cm": round(cumulative_y, 3),
                "meaningful_turn_count": meaningful_turns,
                "longest_stationary_interval_seconds": round(longest_stationary, 3),
                "possible_stuck": possible_stuck,
            }
        )

    path_lengths = [item["cumulative_path_cm"] for item in actor_reports]
    moved = [item for item in actor_reports if item["cumulative_path_cm"] >= MIN_ACTOR_PATH_CM]
    stuck = [item["track_id"] for item in actor_reports if item["possible_stuck"]]
    x_active = [
        item for item in actor_reports if item["cumulative_abs_x_cm"] >= AXIS_ACTIVE_DISTANCE_CM
    ]
    y_active = [
        item for item in actor_reports if item["cumulative_abs_y_cm"] >= AXIS_ACTIVE_DISTANCE_CM
    ]
    turners = [item for item in actor_reports if item["meaningful_turn_count"] > 0]
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    return {
        "tracked_actor_count": len(actor_reports),
        "moved_actor_count": len(moved),
        "possible_stuck_actor_count": len(stuck),
        "possible_stuck_track_ids": stuck,
        "minimum_cumulative_path_cm": round(min(path_lengths), 3) if path_lengths else None,
        "median_cumulative_path_cm": round(float(statistics.median(path_lengths)), 3)
        if path_lengths
        else None,
        "maximum_cumulative_path_cm": round(max(path_lengths), 3) if path_lengths else None,
        "coverage_x_cm": round(max(xs) - min(xs), 3) if xs else None,
        "coverage_y_cm": round(max(ys) - min(ys), 3) if ys else None,
        "coarse_200cm_cell_count": len(coarse_cells),
        "direction_bin_count": len(direction_bins),
        "direction_bins_45deg": sorted(direction_bins),
        "x_active_actor_count": len(x_active),
        "y_active_actor_count": len(y_active),
        "meaningful_turn_actor_count": len(turners),
        "unmatched_track_events": state.get("unmatched_track_events", 0),
        "per_actor": actor_reports,
    }


def collision_report(state):
    summaries = state.get("sample_summaries", [])
    minimums = [
        item["minimum_distance_cm"]
        for item in summaries
        if item.get("minimum_distance_cm") is not None
    ]
    body_pair_samples = sum(item["agent_body_overlap_pair_count"] for item in summaries)
    severe_pair_samples = sum(item["severe_overlap_pair_count"] for item in summaries)
    body_frames = sum(item["agent_body_overlap_pair_count"] > 0 for item in summaries)
    severe_frames = sum(item["severe_overlap_pair_count"] > 0 for item in summaries)
    unique_body_pairs = set()
    unique_severe_pairs = set()
    body_examples = []
    severe_examples = []
    for item in summaries:
        for first, second, distance in item["agent_body_overlap_samples"]:
            unique_body_pairs.add(tuple(sorted((first, second))))
            if len(body_examples) < 20:
                body_examples.append(
                    {
                        "time_seconds": item["time_seconds"],
                        "actors": [first, second],
                        "distance_cm": distance,
                    }
                )
        for first, second, distance in item["severe_overlap_samples"]:
            unique_severe_pairs.add(tuple(sorted((first, second))))
            if len(severe_examples) < 20:
                severe_examples.append(
                    {
                        "time_seconds": item["time_seconds"],
                        "actors": [first, second],
                        "distance_cm": distance,
                    }
                )
    return {
        "sample_frame_count": len(summaries),
        "minimum_pairwise_xy_distance_cm": round(min(minimums), 3)
        if minimums
        else None,
        "agent_radius_cm": AGENT_RADIUS_CM,
        "body_overlap_threshold_cm": AGENT_RADIUS_CM * 2.0,
        "body_overlap_pair_samples": body_pair_samples,
        "body_overlap_frame_count": body_frames,
        "unique_body_overlap_pair_count": len(unique_body_pairs),
        "body_overlap_examples": body_examples,
        "severe_overlap_threshold_cm": SEVERE_OVERLAP_DISTANCE_CM,
        "severe_overlap_pair_samples": severe_pair_samples,
        "severe_overlap_frame_count": severe_frames,
        "unique_severe_overlap_pair_count": len(unique_severe_pairs),
        "severe_overlap_examples": severe_examples,
    }


def collect_cesium_components(world):
    tileset_class = getattr(unreal, "Cesium3DTileset", None)
    if tileset_class is not None:
        tilesets = actors_of_class(world, tileset_class)
    else:
        tilesets = [actor for actor in all_world_actors(world) if class_name(actor) == "Cesium3DTileset"]
    components = []
    for actor in tilesets:
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
                continue
            try:
                accepted = component.is_visible() and component.is_query_collision_enabled()
            except Exception:
                accepted = False
            if accepted:
                components.append(component)
    return components


def trace_world_first_blocker(world, start, end):
    hit = unreal.SystemLibrary.line_trace_single(
        world,
        start,
        end,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    if not hit_property(hit, "blocking_hit", False):
        return None

    point = hit_property(hit, "impact_point")
    if point is None:
        point = hit_property(hit, "location")
    normal = hit_property(hit, "impact_normal")
    if normal is None:
        normal = hit_property(hit, "normal")
    component = hit_property(hit, "component")
    if component is None:
        component = hit_property(hit, "hit_component")
    if point is None or normal is None:
        return None
    return point, normal, component


def trace_cesium_ground(world, components, location):
    for offset_x, offset_y in CESIUM_PROBE_OFFSETS_CM:
        start = unreal.Vector(
            location[0] + offset_x,
            location[1] + offset_y,
            location[2] + TRACE_HEIGHT_CM,
        )
        end = unreal.Vector(
            location[0] + offset_x,
            location[1] + offset_y,
            location[2] - TRACE_DEPTH_CM,
        )
        nearest = None
        nearest_distance = None

        # Match the runtime projection policy: the response-channel result is
        # one raw candidate even when its blocker is not a Cesium component.
        world_hit = trace_world_first_blocker(world, start, end)
        if world_hit is not None:
            point, normal, component = world_hit
            nearest = (point, normal, component, [offset_x, offset_y])
            nearest_distance = distance_squared(start, point)

        # Direct component traces cover streamed Cesium primitives that do not
        # participate in the response channel. Compare every raw result with
        # the world candidate before applying any ownership/slope checks.
        for component in components:
            hit = component.line_trace_component(start, end, True, False, False)
            if not hit:
                continue
            point, normal, _bone_name, _hit_result = hit
            distance = distance_squared(start, point)
            if nearest is None or distance < nearest_distance:
                nearest = (point, normal, component, [offset_x, offset_y])
                nearest_distance = distance

        # Qualify only the global raw first blocker. In particular, a nearer
        # non-Cesium world blocker must reject a Cesium triangle behind it.
        nearest_component = nearest[2] if nearest is not None else None
        nearest_is_cesium = (
            nearest_component is not None
            and class_name(nearest_component) == "CesiumGltfPrimitiveComponent"
        )
        if (
            nearest is not None
            and nearest_is_cesium
            and nearest[1].z >= MIN_WALKABLE_NORMAL_Z
        ):
            return nearest
        if nearest is not None:
            return None
    return None


def grounding_report(world, snapshot, state):
    components = collect_cesium_components(world)
    measurements = []
    for record in snapshot.get("position_records", [])[:TARGET_POPULATION]:
        try:
            hit = trace_cesium_ground(world, components, record["location"])
        except Exception as error:
            append_error(state, "cesium_ground_trace", error)
            hit = None
        if hit is None:
            measurements.append(
                {
                    "actor_name": record["actor_name"],
                    "mass_identity": record["mass_identity"],
                    "trace_hit": False,
                    "root_location": record["location"],
                }
            )
            continue
        point, normal, component, probe_offset = hit
        root_offset = record["location"][2] - float(point.z)
        root_error = root_offset - EXPECTED_ROOT_GROUND_OFFSET_CM
        measurements.append(
            {
                "actor_name": record["actor_name"],
                "mass_identity": record["mass_identity"],
                "trace_hit": True,
                "root_location": record["location"],
                "ground_location": vector_list(point),
                "probe_offset_xy_cm": probe_offset,
                "normal_z": round(float(normal.z), 5),
                "component_class": component.get_class().get_name(),
                "root_ground_offset_cm": round(root_offset, 3),
                "root_ground_error_cm": round(root_error, 3),
            }
        )
    hits = [item for item in measurements if item["trace_hit"]]
    grounded = [
        item
        for item in hits
        if abs(item["root_ground_error_cm"]) <= ROOT_GROUND_TOLERANCE_CM
    ]
    errors = [abs(item["root_ground_error_cm"]) for item in hits]
    return {
        "probe_mode": "exact_xy_only",
        "cesium_component_count": len(components),
        "requested_actor_count": min(TARGET_POPULATION, len(snapshot.get("position_records", []))),
        "trace_hit_count": len(hits),
        "root_within_tolerance_count": len(grounded),
        "target_root_ground_offset_cm": EXPECTED_ROOT_GROUND_OFFSET_CM,
        "root_tolerance_cm": ROOT_GROUND_TOLERANCE_CM,
        "root_max_abs_error_cm": round(max(errors), 3) if errors else None,
        "measurements": measurements,
    }


def aggregate_grounding_reports(reports):
    if not reports:
        return {}
    by_actor = {}
    instantaneous_errors = []
    for sample_index, report in enumerate(reports):
        for measurement in report.get("measurements", []):
            actor = str(measurement["mass_identity"])
            record = by_actor.setdefault(
                actor,
                {
                    "mass_identity": measurement["mass_identity"],
                    "actor_names": [],
                    "sample_count": 0,
                    "trace_hit_count": 0,
                    "minimum_abs_root_error_cm": None,
                    "maximum_abs_root_error_cm": None,
                    "best_measurement": None,
                },
            )
            if measurement["actor_name"] not in record["actor_names"]:
                record["actor_names"].append(measurement["actor_name"])
            record["sample_count"] += 1
            if not measurement.get("trace_hit"):
                continue
            record["trace_hit_count"] += 1
            error = abs(float(measurement["root_ground_error_cm"]))
            instantaneous_errors.append(error)
            if (
                record["minimum_abs_root_error_cm"] is None
                or error < record["minimum_abs_root_error_cm"]
            ):
                record["minimum_abs_root_error_cm"] = round(error, 3)
                record["best_measurement"] = {"sample_index": sample_index, **measurement}
            if (
                record["maximum_abs_root_error_cm"] is None
                or error > record["maximum_abs_root_error_cm"]
            ):
                record["maximum_abs_root_error_cm"] = round(error, 3)

    actor_records = sorted(by_actor.values(), key=lambda item: item["mass_identity"])
    traced_actors = [item for item in actor_records if item["trace_hit_count"] > 0]
    traced_every_sample = [
        item
        for item in actor_records
        if item["trace_hit_count"] == len(reports)
    ]
    correction_observed = [
        item
        for item in actor_records
        if item["minimum_abs_root_error_cm"] is not None
        and item["minimum_abs_root_error_cm"] <= ROOT_GROUND_TOLERANCE_CM
    ]
    within_dynamic_envelope = [
        item
        for item in actor_records
        if item["maximum_abs_root_error_cm"] is not None
        and item["maximum_abs_root_error_cm"] <= MAX_INSTANTANEOUS_ROOT_ERROR_CM
    ]
    return {
        "probe_mode": "exact_xy_only",
        "first_blocker_policy": "global_nearest_raw_hit_then_walkability",
        "validation_sample_count": len(reports),
        "cesium_component_count": max(
            (item.get("cesium_component_count", 0) for item in reports), default=0
        ),
        "requested_actor_count": TARGET_POPULATION,
        "unique_actor_count": len(actor_records),
        "trace_hit_actor_count": len(traced_actors),
        "trace_hit_every_sample_actor_count": len(traced_every_sample),
        "root_correction_observed_actor_count": len(correction_observed),
        "root_within_instantaneous_envelope_actor_count": len(within_dynamic_envelope),
        "target_root_ground_offset_cm": EXPECTED_ROOT_GROUND_OFFSET_CM,
        "root_correction_tolerance_cm": ROOT_GROUND_TOLERANCE_CM,
        "maximum_instantaneous_root_error_allowed_cm": MAX_INSTANTANEOUS_ROOT_ERROR_CM,
        "instantaneous_root_error_max_cm": round(max(instantaneous_errors), 3)
        if instantaneous_errors
        else None,
        "per_actor": actor_records,
        "samples": reports,
    }


def process_memory_snapshot():
    try:
        from ctypes import wintypes

        size_type = ctypes.c_size_t

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", size_type),
                ("WorkingSetSize", size_type),
                ("QuotaPeakPagedPoolUsage", size_type),
                ("QuotaPagedPoolUsage", size_type),
                ("QuotaPeakNonPagedPoolUsage", size_type),
                ("QuotaNonPagedPoolUsage", size_type),
                ("PagefileUsage", size_type),
                ("PeakPagefileUsage", size_type),
                ("PrivateUsage", size_type),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        process = get_current_process()
        ok = get_process_memory_info(
            process, ctypes.byref(counters), counters.cb
        )
        if not ok:
            raise OSError("GetProcessMemoryInfo returned false")
        mib = 1024.0 * 1024.0
        return {
            "available": True,
            "working_set_mib": round(counters.WorkingSetSize / mib, 3),
            "peak_working_set_mib": round(counters.PeakWorkingSetSize / mib, 3),
            "private_usage_mib": round(counters.PrivateUsage / mib, 3),
        }
    except Exception as error:
        return {"available": False, "error": repr(error)}


def percentile(values, percentile_value):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def performance_report(state, final_game_seconds):
    frame_ms = [
        delta * 1000.0
        for delta in state.get("frame_delta_samples", [])
        if 0.0 < delta < 1.0
    ]
    median_ms = float(statistics.median(frame_ms)) if frame_ms else None
    p95_ms = percentile(frame_ms, 0.95)
    sample_started_monotonic = state.get("sample_started_monotonic") or time.monotonic()
    wall_seconds = max(0.0, time.monotonic() - sample_started_monotonic)
    game_seconds = max(
        0.0,
        final_game_seconds - state.get("sample_started_game_seconds", final_game_seconds),
    )
    memory_start = state.get("memory_start", {"available": False})
    memory_end = process_memory_snapshot()
    memory_delta = None
    if memory_start.get("available") and memory_end.get("available"):
        memory_delta = round(
            memory_end["working_set_mib"] - memory_start["working_set_mib"], 3
        )
    return {
        "measurement_kind": "Slate post-tick editor-frame proxy; includes editor/verifier overhead",
        "frame_sample_count": len(frame_ms),
        "median_frame_ms": round(median_ms, 3) if median_ms is not None else None,
        "p95_frame_ms": round(p95_ms, 3) if p95_ms is not None else None,
        "maximum_frame_ms": round(max(frame_ms), 3) if frame_ms else None,
        "median_fps_proxy": round(1000.0 / median_ms, 3)
        if median_ms is not None and median_ms > 0.0
        else None,
        "frames_over_50ms": sum(value > 50.0 for value in frame_ms),
        "sample_game_seconds": round(game_seconds, 3),
        "sample_wall_seconds": round(wall_seconds, 3),
        "game_to_wall_time_ratio": round(game_seconds / wall_seconds, 4)
        if wall_seconds > 0.0
        else None,
        "process_memory_start": memory_start,
        "process_memory_end": memory_end,
        "working_set_delta_mib": memory_delta,
    }


def add_check(checks, name, passed, actual, expected, required=True):
    checks[name] = {
        "passed": bool(passed),
        "required": bool(required),
        "actual": actual,
        "expected": expected,
    }


def serializable_snapshot(snapshot):
    if snapshot is None:
        return None
    return dict(snapshot)


def report_paths():
    saved_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    )
    reports_dir = saved_dir / "Reports"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        reports_dir / (REPORT_BASENAME + "_" + timestamp + ".json"),
        reports_dir / (REPORT_BASENAME + "_latest.json"),
    )


def write_report(report):
    timestamped_path, latest_path = report_paths()
    timestamped_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)
    timestamped_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    evidence_path = (
        Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
        / "Docs"
        / "Evidence"
        / "OpenMassCrowd"
        / (REPORT_BASENAME + "_latest.json")
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(payload, encoding="utf-8")
    return timestamped_path, latest_path


def unregister_callback(state):
    handle = state.get("handle")
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
        state["handle"] = None
    if getattr(builtins, CALLBACK_KEY, None) == handle:
        delattr(builtins, CALLBACK_KEY)
    if getattr(builtins, STATE_KEY, None) is state:
        delattr(builtins, STATE_KEY)


def finalize(state, reason, world=None):
    if state.get("finished"):
        return
    state["finished"] = True
    unregister_callback(state)

    final_snapshot = state.get("latest_snapshot")
    try:
        if world is not None:
            final_snapshot = collect_runtime_snapshot(world, state)
    except Exception as error:
        append_error(state, "collect_final_snapshot", error)

    final_game_seconds = None
    if world is not None:
        try:
            final_game_seconds = float(unreal.GameplayStatics.get_time_seconds(world))
        except Exception as error:
            append_error(state, "final_game_seconds", error)
    if final_game_seconds is None:
        final_game_seconds = state.get("sample_started_game_seconds", 0.0)

    movement = movement_and_branching_report(state)
    collisions = collision_report(state)
    performance = performance_report(state, final_game_seconds)
    try:
        grounding_samples = list(state.get("grounding_samples", []))
        if not grounding_samples and world is not None:
            grounding_samples.append(grounding_report(world, final_snapshot or {}, state))
        grounding = aggregate_grounding_reports(grounding_samples)
    except Exception as error:
        append_error(state, "grounding_report", error)
        grounding = {}

    initial_metrics = state.get("initial_navigation_metrics", {})
    checks = {}
    world_name = (final_snapshot or {}).get("world_name", "")
    add_check(
        checks,
        "pie_world_is_shanghai",
        world_name.lower() == "shanghai" or world_name.lower().endswith("_shanghai"),
        world_name or None,
        "shanghai (PIE prefix allowed)",
    )
    add_check(
        checks,
        "single_open_mass_spawner",
        (final_snapshot or {}).get("spawner_count") == 1,
        (final_snapshot or {}).get("spawner_count"),
        1,
    )
    add_check(
        checks,
        "exactly_30_mass_entities_and_proxies",
        (final_snapshot or {}).get("requested_population") == TARGET_POPULATION
        and (final_snapshot or {}).get("spawned_entities") == TARGET_POPULATION
        and (final_snapshot or {}).get("proxy_actor_count") == TARGET_POPULATION
        and (final_snapshot or {}).get("active_proxy_actor_count") == TARGET_POPULATION
        and (final_snapshot or {}).get("stable_mass_identity_count")
        == TARGET_POPULATION,
        {
            "requested": (final_snapshot or {}).get("requested_population"),
            "entities": (final_snapshot or {}).get("spawned_entities"),
            "proxies_total": (final_snapshot or {}).get("proxy_actor_count"),
            "proxies_active": (final_snapshot or {}).get("active_proxy_actor_count"),
            "stable_mass_identities": (final_snapshot or {}).get(
                "stable_mass_identity_count"
            ),
        },
        {"exact_each": TARGET_POPULATION},
    )
    add_check(
        checks,
        "runtime_navigation_grid_is_9_nodes_and_connected_collision_pruned_lanes",
        (final_snapshot or {}).get("network_nodes") == EXPECTED_NETWORK_NODES
        and (final_snapshot or {}).get("directed_lanes") is not None
        and MINIMUM_DIRECTED_LANES
        <= (final_snapshot or {}).get("directed_lanes")
        <= MAXIMUM_DIRECTED_LANES
        and (final_snapshot or {}).get("directed_lanes") % 2 == 0,
        {
            "nodes": (final_snapshot or {}).get("network_nodes"),
            "directed_lanes": (final_snapshot or {}).get("directed_lanes"),
        },
        {
            "nodes": EXPECTED_NETWORK_NODES,
            "directed_lanes_minimum": MINIMUM_DIRECTED_LANES,
            "directed_lanes_maximum": MAXIMUM_DIRECTED_LANES,
            "directed_lanes_even": True,
        },
    )
    add_check(
        checks,
        "route_assignments_cover_population",
        ((final_snapshot or {}).get("route_assignments") or 0) >= TARGET_POPULATION,
        {
            "initial": initial_metrics.get("route_assignments"),
            "final": (final_snapshot or {}).get("route_assignments"),
        },
        {"minimum_final": TARGET_POPULATION},
    )
    add_check(
        checks,
        "at_least_one_completed_a_to_b_trip",
        ((final_snapshot or {}).get("completed_trips") or 0) >= 1,
        {
            "initial": initial_metrics.get("completed_trips"),
            "final": (final_snapshot or {}).get("completed_trips"),
            "delta_during_sample": max(
                0,
                ((final_snapshot or {}).get("completed_trips") or 0)
                - (initial_metrics.get("completed_trips") or 0),
            ),
        },
        {"minimum_final": 1},
    )
    final_replans = (final_snapshot or {}).get("route_replans")
    add_check(
        checks,
        "route_replans_are_reasonable",
        final_replans is not None and final_replans <= MAX_REASONABLE_REPLANS,
        {
            "initial": initial_metrics.get("route_replans"),
            "final": final_replans,
        },
        {"maximum": MAX_REASONABLE_REPLANS, "ideal": 0},
    )
    add_check(
        checks,
        "route_replans_ideal_zero",
        final_replans == 0,
        final_replans,
        0,
        required=False,
    )
    final_ground_failures = (final_snapshot or {}).get("ground_projection_failures")
    final_ground_recoveries = (final_snapshot or {}).get("ground_center_recoveries")
    final_ground_rollbacks = (final_snapshot or {}).get("ground_rollbacks")
    final_ground_unrecoverable = (final_snapshot or {}).get("ground_unrecoverable")
    final_unsupported_visuals = (final_snapshot or {}).get(
        "current_unsupported_visuals"
    )
    add_check(
        checks,
        "unsupported_ground_steps_are_recovered_before_render",
        final_ground_failures is not None
        and final_ground_recoveries is not None
        and final_ground_rollbacks is not None
        and final_ground_unrecoverable == 0
        and final_unsupported_visuals == 0
        and final_ground_failures
        == final_ground_recoveries + final_ground_rollbacks + final_ground_unrecoverable,
        {
            "projection_failures": final_ground_failures,
            "lane_center_recoveries": final_ground_recoveries,
            "last_valid_rollbacks": final_ground_rollbacks,
            "unrecoverable": final_ground_unrecoverable,
            "currently_unsupported_visuals": final_unsupported_visuals,
        },
        {
            "every_failure_is_recovered_or_accounted": True,
            "unrecoverable": 0,
            "currently_unsupported_visuals": 0,
        },
    )
    add_check(
        checks,
        "all_30_representation_actors_move",
        movement.get("tracked_actor_count") == TARGET_POPULATION
        and movement.get("moved_actor_count") == TARGET_POPULATION,
        {
            "tracked": movement.get("tracked_actor_count"),
            "moved": movement.get("moved_actor_count"),
            "minimum_cumulative_path_cm": movement.get("minimum_cumulative_path_cm"),
        },
        {"exact_moved": TARGET_POPULATION, "minimum_path_per_actor_cm": MIN_ACTOR_PATH_CM},
    )
    add_check(
        checks,
        "no_possible_stuck_actor",
        movement.get("tracked_actor_count") == TARGET_POPULATION
        and movement.get("possible_stuck_actor_count") == 0,
        {
            "possible_stuck_count": movement.get("possible_stuck_actor_count"),
            "track_ids": movement.get("possible_stuck_track_ids"),
        },
        {
            "count": 0,
            "stationary_interval_under_seconds": POSSIBLE_STUCK_SECONDS,
            "minimum_path_cm": MIN_ACTOR_PATH_CM,
        },
    )
    add_check(
        checks,
        "broad_xy_coverage_and_route_branching",
        (movement.get("coverage_x_cm") or 0.0) >= MIN_COVERAGE_X_CM
        and (movement.get("coverage_y_cm") or 0.0) >= MIN_COVERAGE_Y_CM
        and movement.get("direction_bin_count", 0) >= MIN_DIRECTION_BINS
        and movement.get("x_active_actor_count", 0) >= MIN_AXIS_ACTIVE_ACTORS
        and movement.get("y_active_actor_count", 0) >= MIN_AXIS_ACTIVE_ACTORS,
        {
            "coverage_x_cm": movement.get("coverage_x_cm"),
            "coverage_y_cm": movement.get("coverage_y_cm"),
            "coarse_200cm_cells": movement.get("coarse_200cm_cell_count"),
            "direction_bins": movement.get("direction_bins_45deg"),
            "x_active_actors": movement.get("x_active_actor_count"),
            "y_active_actors": movement.get("y_active_actor_count"),
            "turning_actors": movement.get("meaningful_turn_actor_count"),
        },
        {
            "minimum_coverage_x_cm": MIN_COVERAGE_X_CM,
            "minimum_coverage_y_cm": MIN_COVERAGE_Y_CM,
            "minimum_direction_bins": MIN_DIRECTION_BINS,
            "minimum_active_actors_per_axis": MIN_AXIS_ACTIVE_ACTORS,
        },
    )
    add_check(
        checks,
        "no_severe_center_overlap",
        collisions.get("sample_frame_count", 0) > 0
        and collisions.get("severe_overlap_pair_samples", 0) == 0,
        {
            "minimum_pairwise_xy_distance_cm": collisions.get(
                "minimum_pairwise_xy_distance_cm"
            ),
            "severe_overlap_pair_samples": collisions.get("severe_overlap_pair_samples"),
            "body_overlap_pair_samples": collisions.get("body_overlap_pair_samples"),
        },
        {
            "severe_pair_samples": 0,
            "severe_threshold_cm": SEVERE_OVERLAP_DISTANCE_CM,
            "body_overlap_threshold_cm_diagnostic": AGENT_RADIUS_CM * 2.0,
        },
    )
    add_check(
        checks,
        "all_30_roots_trace_to_loaded_cesium_collision",
        grounding.get("trace_hit_actor_count") == TARGET_POPULATION
        and grounding.get("trace_hit_every_sample_actor_count") == TARGET_POPULATION
        and grounding.get("root_correction_observed_actor_count") == TARGET_POPULATION
        and grounding.get("root_within_instantaneous_envelope_actor_count")
        == TARGET_POPULATION,
        {
            "cesium_components": grounding.get("cesium_component_count"),
            "validation_samples": grounding.get("validation_sample_count"),
            "actors_with_trace_hit": grounding.get("trace_hit_actor_count"),
            "actors_traced_every_sample": grounding.get(
                "trace_hit_every_sample_actor_count"
            ),
            "actors_with_correction_point_observed": grounding.get(
                "root_correction_observed_actor_count"
            ),
            "actors_within_dynamic_envelope": grounding.get(
                "root_within_instantaneous_envelope_actor_count"
            ),
            "maximum_instantaneous_abs_error_cm": grounding.get(
                "instantaneous_root_error_max_cm"
            ),
        },
        {
            "exact_actor_count": TARGET_POPULATION,
            "validation_sample_count": GROUND_VALIDATION_SAMPLE_COUNT,
            "component_class": "CesiumGltfPrimitiveComponent",
            "target_root_offset_cm": EXPECTED_ROOT_GROUND_OFFSET_CM,
            "correction_point_tolerance_cm": ROOT_GROUND_TOLERANCE_CM,
            "instantaneous_error_envelope_cm": MAX_INSTANTANEOUS_ROOT_ERROR_CM,
        },
    )
    high = (final_snapshot or {}).get("high_res_representations")
    low = (final_snapshot or {}).get("low_res_representations")
    observed_tiers = sorted(
        {tuple(item) for item in state.get("representation_observations", [])}
    )
    add_check(
        checks,
        "representation_getters_account_for_population",
        high is not None and low is not None and high + low == TARGET_POPULATION,
        {
            "final_high": high,
            "final_low": low,
            "observed_high_low_pairs": [list(item) for item in observed_tiers],
        },
        {"high_plus_low": TARGET_POPULATION},
    )
    median_fps = performance.get("median_fps_proxy")
    add_check(
        checks,
        "editor_frame_proxy_median_fps_advisory",
        median_fps is not None and median_fps >= ADVISORY_MIN_MEDIAN_FPS,
        {
            "median_fps_proxy": median_fps,
            "p95_frame_ms": performance.get("p95_frame_ms"),
            "frames_over_50ms": performance.get("frames_over_50ms"),
        },
        {"minimum_median_fps_proxy": ADVISORY_MIN_MEDIAN_FPS},
        required=False,
    )

    required_failures = [
        name for name, value in checks.items() if value["required"] and not value["passed"]
    ]
    advisory_failures = [
        name for name, value in checks.items() if not value["required"] and not value["passed"]
    ]
    overall_passed = (
        reason == "completed"
        and not required_failures
        and not state.get("errors", [])
    )
    report = {
        "schema_version": 1,
        "generated_utc": utc_now(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "project_dir": unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
        "completion_reason": reason,
        "overall_passed": overall_passed,
        "required_failed_checks": required_failures,
        "advisory_failed_checks": advisory_failures,
        "checks": checks,
        "sampling": {
            "minimum_seconds": MIN_SAMPLE_SECONDS,
            "maximum_seconds": MAX_SAMPLE_SECONDS,
            "position_interval_seconds": POSITION_SAMPLE_INTERVAL_SECONDS,
            "sample_count": len(state.get("sample_summaries", [])),
            "sample_summaries": state.get("sample_summaries", []),
        },
        "navigation_metrics_at_sample_start": initial_metrics,
        "runtime_snapshot_final": serializable_snapshot(final_snapshot),
        "movement_and_branching": movement,
        "collision_and_spacing": collisions,
        "cesium_grounding": grounding,
        "representations": {
            "observed_high_low_pairs": [list(item) for item in observed_tiers],
            "final_high": high,
            "final_low": low,
        },
        "performance": performance,
        "diagnostics": {
            "error_count": len(state.get("errors", [])),
            "errors": state.get("errors", []),
            "status_updates": state.get("status_updates", []),
        },
    }

    try:
        timestamped_path, latest_path = write_report(report)
        marker = {
            "overall_passed": overall_passed,
            "completion_reason": reason,
            "required_failed_checks": required_failures,
            "advisory_failed_checks": advisory_failures,
            "error_count": len(state.get("errors", [])),
            "report": str(timestamped_path),
            "latest": str(latest_path),
        }
        message = "OPEN_MASS_CITY_NAVIGATION_RUNTIME_VERIFY=" + json.dumps(
            marker, ensure_ascii=False, sort_keys=True
        )
        if overall_passed:
            unreal.log_warning(message)
        else:
            unreal.log_error(message)
    except Exception as error:
        unreal.log_error("OPEN_MASS_CITY_NAVIGATION_REPORT_WRITE_FAILED=" + repr(error))


def add_status_update(state, status, snapshot=None):
    update = {
        "utc": utc_now(),
        "elapsed_wall_seconds": round(time.monotonic() - state["started_monotonic"], 3),
        "status": status,
    }
    if snapshot is not None:
        for key in (
            "spawner_count",
            "spawned_entities",
            "proxy_actor_count",
            "active_proxy_actor_count",
            "network_nodes",
            "directed_lanes",
            "route_assignments",
            "completed_trips",
            "route_replans",
            "high_res_representations",
            "low_res_representations",
        ):
            update[key] = snapshot.get(key)
    updates = state.setdefault("status_updates", [])
    comparison = {key: value for key, value in update.items() if key not in {"utc", "elapsed_wall_seconds"}}
    if updates:
        previous = {
            key: value
            for key, value in updates[-1].items()
            if key not in {"utc", "elapsed_wall_seconds"}
        }
        if previous == comparison:
            return
    updates.append(update)
    unreal.log_warning(
        "OPEN_MASS_CITY_NAVIGATION_RUNTIME_STATUS="
        + json.dumps(update, ensure_ascii=False, sort_keys=True)
    )


def tick_verifier(delta_seconds):
    state = getattr(builtins, STATE_KEY, None)
    if state is None or state.get("finished"):
        return
    now = time.monotonic()
    if state.get("sample_started_game_seconds") is not None and 0.0 < delta_seconds < 1.0:
        state.setdefault("frame_delta_samples", []).append(float(delta_seconds))
    if now - state["last_poll_monotonic"] < POLL_INTERVAL_SECONDS:
        return
    state["last_poll_monotonic"] = now

    world = None
    try:
        world = get_pie_world()
        if world is None:
            if state.get("pie_seen"):
                add_status_update(state, "pie_ended_before_verification")
                finalize(state, "pie_ended_early", None)
                return
            add_status_update(state, "waiting_for_pie_world")
        else:
            state["pie_seen"] = True
            snapshot = collect_runtime_snapshot(world, state)
            state["latest_snapshot"] = snapshot
            if not snapshot_ready(snapshot):
                add_status_update(state, "waiting_for_navigation_population", snapshot)
            else:
                game_seconds = float(unreal.GameplayStatics.get_time_seconds(world))
                if state.get("sample_started_game_seconds") is None:
                    state["sample_started_game_seconds"] = game_seconds
                    state["sample_started_monotonic"] = now
                    state["last_position_sample_game_seconds"] = game_seconds
                    state["memory_start"] = process_memory_snapshot()
                    state["initial_navigation_metrics"] = {
                        key: snapshot.get(key)
                        for key in (
                            "route_assignments",
                            "completed_trips",
                            "route_replans",
                            "high_res_representations",
                            "low_res_representations",
                        )
                    }
                    record_position_sample(state, snapshot, 0.0)
                    add_status_update(state, "sampling_city_navigation", snapshot)
                else:
                    game_elapsed = max(0.0, game_seconds - state["sample_started_game_seconds"])
                    if (
                        game_seconds - state["last_position_sample_game_seconds"]
                        >= POSITION_SAMPLE_INTERVAL_SECONDS
                    ):
                        state["last_position_sample_game_seconds"] = game_seconds
                        record_position_sample(state, snapshot, game_elapsed)

                    completed = snapshot.get("completed_trips") or 0
                    if game_elapsed >= MIN_SAMPLE_SECONDS and completed >= 1:
                        last_ground_sample = state.get("last_grounding_sample_game_seconds")
                        if (
                            len(state.get("grounding_samples", []))
                            < GROUND_VALIDATION_SAMPLE_COUNT
                            and (
                                last_ground_sample is None
                                or game_seconds - last_ground_sample
                                >= GROUND_VALIDATION_INTERVAL_SECONDS
                            )
                        ):
                            state.setdefault("grounding_samples", []).append(
                                grounding_report(world, snapshot, state)
                            )
                            state["last_grounding_sample_game_seconds"] = game_seconds
                            add_status_update(
                                state,
                                "sampling_cesium_ground_correction_{}/{}".format(
                                    len(state["grounding_samples"]),
                                    GROUND_VALIDATION_SAMPLE_COUNT,
                                ),
                                snapshot,
                            )
                        if (
                            len(state.get("grounding_samples", []))
                            >= GROUND_VALIDATION_SAMPLE_COUNT
                        ):
                            # Capture a final endpoint even if the last regular
                            # sample occurred just before the completion threshold.
                            if not state.get("sample_summaries") or (
                                game_elapsed - state["sample_summaries"][-1]["time_seconds"]
                                > 0.05
                            ):
                                record_position_sample(state, snapshot, game_elapsed)
                            add_status_update(state, "finalizing_navigation_evidence", snapshot)
                            finalize(state, "completed", world)
                            return
                    if game_elapsed >= MAX_SAMPLE_SECONDS:
                        record_position_sample(state, snapshot, game_elapsed)
                        add_status_update(state, "sample_timeout_without_completed_trip", snapshot)
                        finalize(state, "sample_timeout", world)
                        return

        if now - state["started_monotonic"] >= WAIT_TIMEOUT_SECONDS:
            add_status_update(state, "verification_timeout")
            finalize(state, "timeout", world)
    except Exception as error:
        append_error(state, "tick_verifier", error)
        finalize(state, "callback_exception", world)


def main():
    old_handle = getattr(builtins, CALLBACK_KEY, None)
    if old_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(old_handle)
        except Exception:
            pass

    now = time.monotonic()
    state = {
        "started_monotonic": now,
        "last_poll_monotonic": 0.0,
        "sample_started_game_seconds": None,
        "sample_started_monotonic": None,
        "last_position_sample_game_seconds": None,
        "latest_snapshot": None,
        "initial_navigation_metrics": {},
        "tracks": [],
        "sample_summaries": [],
        "grounding_samples": [],
        "last_grounding_sample_game_seconds": None,
        "representation_observations": [],
        "frame_delta_samples": [],
        "pie_seen": False,
        "finished": False,
        "errors": [],
        "status_updates": [],
        "handle": None,
    }
    state["handle"] = unreal.register_slate_post_tick_callback(tick_verifier)
    setattr(builtins, CALLBACK_KEY, state["handle"])
    setattr(builtins, STATE_KEY, state)
    unreal.log_warning(
        "OPEN_MASS_CITY_NAVIGATION_RUNTIME_VERIFY_STARTED="
        + json.dumps(
            {
                "target_population": TARGET_POPULATION,
                "expected_network_nodes": EXPECTED_NETWORK_NODES,
                "minimum_directed_lanes": MINIMUM_DIRECTED_LANES,
                "maximum_directed_lanes": MAXIMUM_DIRECTED_LANES,
                "minimum_sample_seconds": MIN_SAMPLE_SECONDS,
                "maximum_sample_seconds": MAX_SAMPLE_SECONDS,
                "cesium_ground_validation_samples": GROUND_VALIDATION_SAMPLE_COUNT,
                "wait_timeout_seconds": WAIT_TIMEOUT_SECONDS,
                "does_not_start_or_stop_pie": True,
            },
            sort_keys=True,
        )
    )


main()
