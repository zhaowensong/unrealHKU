"""Deterministic multi-source RF propagation against stable AABB collision proxies."""

from __future__ import annotations

import math
from datetime import datetime, timezone


EPSILON_CM = 5.0


def add(a, b):
    return tuple(a[i] + b[i] for i in range(3))


def sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def mul(a, scalar):
    return tuple(value * scalar for value in a)


def dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def length(vector):
    return math.sqrt(dot(vector, vector))


def normalize(vector):
    magnitude = length(vector)
    if magnitude <= 1.0e-9:
        return (0.0, 0.0, 1.0)
    return mul(vector, 1.0 / magnitude)


def reflect(direction, normal):
    return normalize(sub(direction, mul(normal, 2.0 * dot(direction, normal))))


def vector_object(vector):
    return {"x": round(vector[0], 3), "y": round(vector[1], 3), "z": round(vector[2], 3)}


def ray_aabb_intersection(origin, direction, proxy, maximum_distance_cm):
    center = tuple(proxy["center_cm"])
    extent = tuple(proxy["extent_cm"])
    minimum = sub(center, extent)
    maximum = add(center, extent)
    near_t = EPSILON_CM
    far_t = maximum_distance_cm
    hit_normal = None

    for axis in range(3):
        component = direction[axis]
        if abs(component) < 1.0e-9:
            if origin[axis] < minimum[axis] or origin[axis] > maximum[axis]:
                return None
            continue

        first = (minimum[axis] - origin[axis]) / component
        second = (maximum[axis] - origin[axis]) / component
        first_normal = [0.0, 0.0, 0.0]
        second_normal = [0.0, 0.0, 0.0]
        first_normal[axis] = -1.0
        second_normal[axis] = 1.0
        if first > second:
            first, second = second, first
            first_normal, second_normal = second_normal, first_normal

        if first > near_t:
            near_t = first
            hit_normal = tuple(first_normal)
        far_t = min(far_t, second)
        if near_t > far_t:
            return None

    if hit_normal is None or near_t < EPSILON_CM or near_t > maximum_distance_cm:
        return None
    return near_t, hit_normal


def nearest_proxy_hit(origin, direction, proxies, maximum_distance_cm):
    nearest = None
    for proxy in proxies:
        hit = ray_aabb_intersection(origin, direction, proxy, maximum_distance_cm)
        if hit and (nearest is None or hit[0] < nearest[0]):
            nearest = (hit[0], hit[1], proxy)
    return nearest


def received_power_dbm(settings, total_distance_cm, reflection_count):
    distance_km = max(total_distance_cm / 100000.0, 0.001)
    frequency_mhz = float(settings["frequency_mhz"])
    free_space_loss = 32.44 + 20.0 * math.log10(distance_km) + 20.0 * math.log10(frequency_mhz)
    reflection_loss = reflection_count * float(settings["reflection_loss_db"])
    atmospheric_loss = distance_km * float(settings["atmospheric_loss_db_per_km"])
    return float(settings["transmit_power_dbm"]) - free_space_loss - reflection_loss - atmospheric_loss


def normalize_power(settings, power_dbm):
    low = float(settings["minimum_received_power_dbm"])
    high = float(settings["maximum_received_power_dbm"])
    return max(0.0, min(1.0, (power_dbm - low) / max(high - low, 1.0e-6)))


def initial_directions(source, proxies, ray_count, source_index):
    directions = []
    for index in range(min(ray_count, len(proxies))):
        proxy = proxies[(index * 5 + source_index * 3) % len(proxies)]
        center = tuple(proxy["center_cm"])
        target = (center[0], center[1], min(center[2] + proxy["extent_cm"][2] * 0.15, 12000.0))
        directions.append(normalize(sub(target, source)))

    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    while len(directions) < ray_count:
        index = len(directions)
        yaw = (index + source_index * 0.37) * golden_angle
        pitch = math.radians(((index * 7 + source_index * 3) % 19) - 9)
        directions.append(normalize((math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch))))
    return directions


def simulate(settings, transmitters, proxies):
    segments = []
    maximum_path_cm = float(settings["max_path_distance_m"]) * 100.0
    maximum_reflections = int(settings["max_reflections"])
    rays_per_transmitter = int(settings["rays_per_transmitter"])

    for source_index, transmitter in enumerate(transmitters):
        source = tuple(transmitter["position_cm"])
        for ray_index, direction in enumerate(initial_directions(source, proxies, rays_per_transmitter, source_index)):
            current = source
            travelled_cm = 0.0
            for bounce_index in range(maximum_reflections + 1):
                remaining_cm = maximum_path_cm - travelled_cm
                if remaining_cm <= EPSILON_CM:
                    break
                hit = nearest_proxy_hit(current, direction, proxies, remaining_cm)
                if hit:
                    segment_distance, normal, proxy = hit
                    end = add(current, mul(direction, segment_distance))
                    reflection_hit = True
                    proxy_id = proxy["id"]
                else:
                    segment_distance = remaining_cm
                    end = add(current, mul(direction, segment_distance))
                    reflection_hit = False
                    proxy_id = None

                travelled_cm += segment_distance
                power = received_power_dbm(settings, travelled_cm, bounce_index)
                segments.append(
                    {
                        "start_tuple": current,
                        "end_tuple": end,
                        "start": vector_object(current),
                        "end": vector_object(end),
                        "received_power_dbm": round(power, 3),
                        "normalized_strength": round(normalize_power(settings, power), 6),
                        "bounce_index": bounce_index,
                        "source_id": transmitter["id"],
                        "ray_id": "{}-{:03d}".format(transmitter["id"], ray_index),
                        "reflection_hit": reflection_hit,
                        "hit_proxy_id": proxy_id,
                    }
                )
                if not reflection_hit:
                    break
                direction = reflect(direction, normal)
                current = add(end, mul(direction, EPSILON_CM * 2.0))

    penetrations = validate_zero_penetration(segments, proxies)
    frame_segments = [
        {key: value for key, value in segment.items() if key not in ("start_tuple", "end_tuple", "ray_id")}
        for segment in segments
    ]
    frame = {
        "schemaVersion": "telecom-twin.signal-frame/1.0",
        "frameId": settings["frame_id"],
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "transmitters": [
            {
                "id": transmitter["id"],
                "position": vector_object(transmitter["position_cm"]),
                "frequencyMHz": float(settings["frequency_mhz"]),
                "transmitPowerDbm": float(settings["transmit_power_dbm"]),
            }
            for transmitter in transmitters
        ],
        "segments": [
            {
                "start": item["start"],
                "end": item["end"],
                "receivedPowerDbm": item["received_power_dbm"],
                "normalizedStrength": item["normalized_strength"],
                "bounceIndex": item["bounce_index"],
                "sourceId": item["source_id"],
                "bReflectionHit": item["reflection_hit"],
                "hitProxyId": item["hit_proxy_id"],
            }
            for item in frame_segments
        ],
        "metrics": {
            "transmitter_count": len(transmitters),
            "ray_count": len(transmitters) * rays_per_transmitter,
            "segment_count": len(segments),
            "reflection_segment_count": sum(1 for item in segments if item["reflection_hit"]),
            "zero_penetration_violations": len(penetrations),
        },
    }
    return frame, segments, penetrations


def validate_zero_penetration(segments, proxies, tolerance_cm=2.0):
    violations = []
    for index, segment in enumerate(segments):
        start = segment["start_tuple"]
        end = segment["end_tuple"]
        delta = sub(end, start)
        distance = length(delta)
        if distance <= EPSILON_CM:
            continue
        hit = nearest_proxy_hit(start, normalize(delta), proxies, distance + tolerance_cm)
        if not hit:
            continue
        expected_end = add(start, mul(normalize(delta), hit[0]))
        if segment["hit_proxy_id"] != hit[2]["id"] or length(sub(end, expected_end)) > tolerance_cm:
            violations.append({"segment_index": index, "expected_proxy": hit[2]["id"]})
    return violations
