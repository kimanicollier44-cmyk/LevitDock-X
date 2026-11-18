#!/usr/bin/env python3
"""LevitDock-X - educational levitation docking station lab.

by Darion Kellar

Pure Python standard library. Zero dependencies. Zero network.
Education only: this lab computes the planning arithmetic of a
docking station for levitating vehicles - maglev pods, shuttles and
cargo lifters that arrive floating and must be caught, aligned and
berthed without ever touching the guideway. The physics is honest
and labelled: magnetic pressure p = B^2 / (2 mu0) lifts the vehicle,
the lift falls with the square of the air gap, the approach is a
braking-distance problem, the berth is a turnaround-time problem,
and the dock is a power-budget problem. A Dijkstra graph routes
each arrival over the approach lanes, and six interlocks REFUSE a
docking plan - beacon spacing, gap range, lift margin, approach
speed, alignment, power budget - with the violated rule named.
Real maglev systems (Shanghai, JR SCMaglev, Incheon) appear as
LABELLED published facts, never as computed results. This is a
planning and learning simulator: it docks nothing real, guides no
real vehicle, and every real-world construction / operation /
certification action is a HOLD gate - with Gemini Argon 4 as
DECIDER (names-only, tighten-only), Muse as advisor (never
overrides HOLD), and Google Flow plan-only seats.
"""
import hashlib
import heapq
import json
import math
import sys

# ---------------------------------------------------------------- constants
G_EARTH = 9.80665               # standard gravity, m/s^2 (exact, SI)
MU0 = 4.0 * math.pi * 1.0e-7    # vacuum permeability, H/m (exact, SI)
VERSION = "1.0.0"

# Levitation model constants (LABELLED model values)
NOMINAL_GAP_MM = 10.0          # labelled nominal levitation gap
GAP_MIN_MM = 8.0               # labelled capture-gap floor
GAP_MAX_MM = 12.0              # labelled capture-gap ceiling
LIFT_MARGIN_MIN = 1.15         # labelled minimum lift / weight at capture
APPROACH_SPEED_MAX_MS = 3.0    # labelled max speed at the capture point
APPROACH_DECEL_MS2 = 1.2       # labelled docking braking deceleration
LATERAL_MAX_M = 0.25           # labelled capture lateral-offset limit
YAW_MAX_DEG = 2.0              # labelled capture yaw limit
BEACON_SPACING_MAX_M = 500.0   # labelled max approach segment sans beacon
DOCK_SUPPLY_KW = 100.0         # labelled dock power supply limit

# Berth turnaround phases (LABELLED model seconds)
PHASE_DOCK_S = 90.0
PHASE_UNLOAD_S = 300.0
PHASE_CHARGE_S = 600.0
PHASE_LOAD_S = 300.0
PHASE_UNDOCK_S = 90.0

# Power model (LABELLED model values, per vehicle while berthed)
LEV_KW_PER_T = 1.5             # levitation power per tonne
GUIDANCE_KW = 2.5              # guidance electronics, flat per vehicle
COOLING_KW = 4.0               # magnet cooling, flat per vehicle

# ---------------------------------------------------------------- HOLD gates
HOLD_GATES = {
    "real_vehicle_operation": "HOLD - no operating, driving or docking a real levitating vehicle",
    "real_docking_guidance": "HOLD - no guidance for any real docking manoeuvre; this lab plans on paper only",
    "maglev_construction": "HOLD - no construction instructions for real guideways, docks or berths",
    "cryogenic_handling": "HOLD - no handling of cryogenics or superconducting magnets in the real world",
    "high_voltage_work": "HOLD - no high-voltage or traction-power work on real equipment",
    "structural_certification_claim": "HOLD - no certifying any real dock, berth or vehicle safe",
    "live_traffic_direction": "HOLD - no directing live vehicle traffic or docking traffic",
    "commercial_service_claim": "HOLD - this is an educational lab, not a commercial docking service or product",
    "investment_claim": "HOLD - no investment advice about maglev, levitation or transport companies",
    "guaranteed_schedule_claim": "HOLD - no guaranteed real schedule or throughput; every rate here is a labelled model value",
    "weapons_application_claim": "HOLD - no weapons application guidance of any kind",
    "passenger_safety_claim": "HOLD - no safety claims about carrying real passengers",
    "emergency_direction_live": "HOLD - no live emergency or evacuation direction; berth math here is a planning model",
    "spectrum_claim": "HOLD - no claims about real radio spectrum, signalling or control-system approval",
}


def gate(action):
    """Return 'HOLD' if the action is gated, else 'PROCEED' (sandbox sim)."""
    return "HOLD" if action in HOLD_GATES else "PROCEED"


# ================================================================ 1. Levitation lift
# A magnet lifts because the field stores energy: magnetic pressure
#   p = B^2 / (2 mu0)     [Pa]
# and the lift force is pressure times magnet area, F = p * A.
# At B = 1 T the pressure is ~398 kPa - a modest magnet area lifts
# tonnes. The catch is the gap: for an EMS-style lift the force
# falls with the square of the gap, F(g) = F_nom * (g0 / g)^2, so
# drifting 4 mm wide of a 10 mm nominal gap costs almost half the
# lift. Every vehicle below is a LABELLED model, not a real craft.

VEHICLES = {
    "taxi":    {"mass_kg": 4000.0, "field_t": 0.9, "magnet_area_m2": 0.20},
    "pod":     {"mass_kg": 8000.0, "field_t": 1.0, "magnet_area_m2": 0.30},
    "shuttle": {"mass_kg": 12000.0, "field_t": 1.1, "magnet_area_m2": 0.35},
    "cargo":   {"mass_kg": 20000.0, "field_t": 1.2, "magnet_area_m2": 0.50},
}


def _vehicle(name):
    if name not in VEHICLES:
        raise ValueError(f"unknown vehicle: {name}")
    return VEHICLES[name]


def magnetic_pressure_pa(field_t):
    """Magnetic pressure p = B^2 / (2 mu0) in pascals."""
    if field_t <= 0:
        raise ValueError("field must be positive")
    return field_t ** 2 / (2.0 * MU0)


def weight_n(vehicle):
    """Vehicle weight in newtons."""
    return _vehicle(vehicle)["mass_kg"] * G_EARTH


def lift_force_n(vehicle, gap_mm=NOMINAL_GAP_MM):
    """Lift force at a gap: nominal force scaled by (g0 / g)^2."""
    if gap_mm <= 0:
        raise ValueError("gap must be positive")
    v = _vehicle(vehicle)
    f_nom = magnetic_pressure_pa(v["field_t"]) * v["magnet_area_m2"]
    return f_nom * (NOMINAL_GAP_MM / gap_mm) ** 2


def lift_margin(vehicle, gap_mm=NOMINAL_GAP_MM):
    """Lift / weight at a gap - the number the interlock watches."""
    return lift_force_n(vehicle, gap_mm) / weight_n(vehicle)


def lift_table(gap_mm=NOMINAL_GAP_MM):
    rows = []
    for name in ("taxi", "pod", "shuttle", "cargo"):
        rows.append({"vehicle": name,
                     "mass_kg": VEHICLES[name]["mass_kg"],
                     "field_t": VEHICLES[name]["field_t"],
                     "pressure_pa": magnetic_pressure_pa(VEHICLES[name]["field_t"]),
                     "lift_n": lift_force_n(name, gap_mm),
                     "weight_n": weight_n(name),
                     "lift_margin": lift_margin(name, gap_mm)})
    return rows


def gap_for_margin(vehicle, margin):
    """Gap (mm) at which the lift margin falls to `margin`.

    Inverts F(g) = F_nom * (g0/g)^2: g = g0 * sqrt(margin_nom / margin).
    """
    if margin <= 0:
        raise ValueError("margin must be positive")
    return NOMINAL_GAP_MM * math.sqrt(lift_margin(vehicle) / margin)


# ================================================================ 2. Docking approach
# Docking is a braking problem first and an alignment problem
# second. From approach speed v with deceleration a the vehicle
# needs v^2 / (2a) metres to stop - at the 3 m/s capture limit
# with 1.2 m/s^2 braking that is 3.75 m, and at 5 m/s it is 10.42 m,
# which is already past the capture point. The capture envelope is
# a box: gap 8-12 mm, lateral offset <= 0.25 m, yaw <= 2 degrees,
# speed <= 3 m/s. Miss any wall of the box and the dock refuses.

def braking_distance_m(speed_ms, decel_ms2=APPROACH_DECEL_MS2):
    """Stopping distance v^2 / (2a) in metres."""
    if speed_ms < 0:
        raise ValueError("speed must be non-negative")
    if decel_ms2 <= 0:
        raise ValueError("deceleration must be positive")
    return speed_ms ** 2 / (2.0 * decel_ms2)


def capture_envelope_ok(gap_mm, approach_ms, lateral_m, yaw_deg):
    """True only if every wall of the capture box is satisfied."""
    return (GAP_MIN_MM <= gap_mm <= GAP_MAX_MM
            and approach_ms <= APPROACH_SPEED_MAX_MS
            and lateral_m <= LATERAL_MAX_M
            and yaw_deg <= YAW_MAX_DEG)


def approach_report(vehicle="pod", gap_mm=NOMINAL_GAP_MM,
                    approach_ms=2.5, lateral_m=0.10, yaw_deg=0.8):
    return {"vehicle": vehicle, "gap_mm": gap_mm,
            "approach_ms": approach_ms, "lateral_m": lateral_m,
            "yaw_deg": yaw_deg,
            "braking_distance_m": braking_distance_m(approach_ms),
            "lift_margin": lift_margin(vehicle, gap_mm),
            "capture_ok": capture_envelope_ok(gap_mm, approach_ms,
                                              lateral_m, yaw_deg)}


# ================================================================ 3. Real maglev systems (LABELLED published facts)
# These numbers are published facts about finished systems, printed
# for scale only. They are NOT outputs of the models above and the
# lab claims no part in any of them.

REAL_SYSTEMS = [
    {"name": "Shanghai Maglev", "country": "China",
     "length_km": 30.5, "top_speed_kmh": 430, "opened": 2004,
     "note": "commercial Transrapid line, Shanghai Pudong airport run"},
    {"name": "JR SCMaglev L0 Series", "country": "Japan",
     "length_km": 42.8, "top_speed_kmh": 500, "opened": 2013,
     "note": "test-line speed record 603 km/h (2015); Yamanashi test line"},
    {"name": "Incheon Airport Maglev", "country": "South Korea",
     "length_km": 6.1, "top_speed_kmh": 110, "opened": 2016,
     "note": "urban maglev shuttle, Incheon International Airport"},
]


def real_systems():
    return [dict(s) for s in REAL_SYSTEMS]


# ================================================================ 4. Approach lanes (Dijkstra)
# A small deterministic lane graph: one entry holding point, two
# guidance beacons, a junction, two gates and two berths. Every edge
# is a lane segment with a length and a speed limit; traversal time
# is length / speed. The EXPRESS segment (ENTRY -> JUNCTION, 900 m
# at 12 m/s) is the trap: it is the fastest way in and it breaks the
# beacon rule - no segment over 500 m without a guidance beacon.

GRAPH = {
    "ENTRY": [("BEACON_1", 300.0, 8.0), ("JUNCTION", 900.0, 12.0)],
    "BEACON_1": [("ENTRY", 300.0, 8.0), ("BEACON_2", 350.0, 8.0),
                 ("JUNCTION", 400.0, 8.0)],
    "BEACON_2": [("BEACON_1", 350.0, 8.0), ("GATE_A", 300.0, 6.0)],
    "JUNCTION": [("ENTRY", 900.0, 12.0), ("BEACON_1", 400.0, 8.0),
                 ("GATE_A", 450.0, 10.0), ("GATE_B", 500.0, 10.0)],
    "GATE_A": [("BEACON_2", 300.0, 6.0), ("JUNCTION", 450.0, 10.0),
              ("BERTH_1", 200.0, 4.0), ("BERTH_2", 350.0, 4.0)],
    "GATE_B": [("JUNCTION", 500.0, 10.0), ("BERTH_2", 250.0, 4.0)],
    "BERTH_1": [("GATE_A", 200.0, 4.0), ("BERTH_2", 150.0, 3.0)],
    "BERTH_2": [("GATE_A", 350.0, 4.0), ("GATE_B", 250.0, 4.0),
                ("BERTH_1", 150.0, 3.0)],
}


def edge_time_s(length_m, speed_ms):
    """Traversal time for one lane segment (seconds)."""
    if length_m <= 0:
        raise ValueError("segment length must be positive")
    if speed_ms <= 0:
        raise ValueError("speed limit must be positive")
    return length_m / speed_ms


def _edge_lookup(a, b):
    for (n, length, speed) in GRAPH[a]:
        if n == b:
            return length, speed
    raise KeyError(f"no edge {a}-{b}")


def dijkstra(start, goal, max_segment_m=None):
    """Shortest-time route; optionally forbids segments over a length.

    Returns {"path": [...], "time_s": float, "distance_m": float}.
    """
    if start not in GRAPH or goal not in GRAPH:
        raise ValueError("unknown node")
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, start)]
    seen = set()
    while pq:
        t, node = heapq.heappop(pq)
        if node in seen:
            continue
        seen.add(node)
        if node == goal:
            break
        for (nxt, length, speed) in GRAPH[node]:
            if max_segment_m is not None and length > max_segment_m:
                continue
            nt = t + edge_time_s(length, speed)
            if nxt not in dist or nt < dist[nxt]:
                dist[nxt] = nt
                prev[nxt] = node
                heapq.heappush(pq, (nt, nxt))
    if goal not in dist:
        raise ValueError(f"no route from {start} to {goal}")
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    distance = sum(_edge_lookup(a, b)[0] for a, b in zip(path, path[1:]))
    return {"path": path, "time_s": dist[goal], "distance_m": distance}


def route_segments(path):
    """Segment-by-segment breakdown of a route path."""
    segs = []
    for a, b in zip(path, path[1:]):
        length, speed = _edge_lookup(a, b)
        segs.append({"from": a, "to": b, "length_m": length,
                     "speed_limit_ms": speed,
                     "time_s": edge_time_s(length, speed)})
    return segs


# ================================================================ 5. Berths & turnaround
# A berth is a clock, not a parking space. Every docking runs the
# same five phases - dock, unload, charge, load, undock - 1,380 s
# (23 min) end to end, so one berth turns 2.61 vehicles an hour and
# four berths turn 10.43. The dock's power supply is the binding
# constraint long before the berths run out: 100 kW covers four
# vehicles at once with 8 kW to spare, and a fifth cargo lifter
# tips the dock over budget.

def turnaround_s():
    """Total berth turnaround time (seconds)."""
    return (PHASE_DOCK_S + PHASE_UNLOAD_S + PHASE_CHARGE_S
            + PHASE_LOAD_S + PHASE_UNDOCK_S)


def berth_throughput_per_hour(berths=4):
    """Vehicles per hour across `berths` berths at full turnaround."""
    if berths <= 0:
        raise ValueError("berths must be positive")
    return berths * 3600.0 / turnaround_s()


def berth_power_kw(vehicle):
    """Power draw of one berthed vehicle (kW, labelled model)."""
    v = _vehicle(vehicle)
    mass_t = v["mass_kg"] / 1000.0
    return mass_t * LEV_KW_PER_T + GUIDANCE_KW + COOLING_KW


def fleet_power_kw(fleet):
    """Simultaneous draw of a berthed fleet (kW)."""
    return sum(berth_power_kw(v) for v in fleet)


def berth_energy_kwh(vehicle):
    """Energy of one full turnaround at berth power (kWh)."""
    return berth_power_kw(vehicle) * turnaround_s() / 3600.0


def berth_report(berths=4):
    return {"turnaround_s": turnaround_s(),
            "turnaround_min": turnaround_s() / 60.0,
            "per_berth_per_hour": 3600.0 / turnaround_s(),
            "berths": berths,
            "throughput_per_hour": berth_throughput_per_hour(berths),
            "daily_capacity_18h": berth_throughput_per_hour(berths) * 18.0,
            "supply_kw": DOCK_SUPPLY_KW,
            "four_vehicle_draw_kw": fleet_power_kw(
                ["taxi", "pod", "shuttle", "cargo"]),
            "supply_headroom_kw": DOCK_SUPPLY_KW - fleet_power_kw(
                ["taxi", "pod", "shuttle", "cargo"])}


# ================================================================ 6. Docking interlocks
# A docking plan is a route plus a capture envelope plus a fleet.
# Six interlocks, each with a named refusal reason - a plan that
# breaks one is REFUSED, never warned-and-waved-through:
#   BEACON_SPACING  no approach segment over 500 m without a beacon
#   GAP_RANGE       capture gap must sit in [8, 12] mm
#   LIFT_MARGIN     lift / weight at the capture gap must be >= 1.15
#   APPROACH_SPEED  speed at the capture point must be <= 3 m/s
#   ALIGNMENT       lateral offset <= 0.25 m AND yaw <= 2 degrees
#   POWER_BUDGET    simultaneous berthed draw must be <= 100 kW

def check_plan(route, vehicle="pod", gap_mm=NOMINAL_GAP_MM,
               approach_ms=2.5, lateral_m=0.10, yaw_deg=0.8,
               fleet=("taxi", "pod", "shuttle")):
    """Audit a docking plan against the six interlocks.

    Returns {"accepted": bool, "violations": [reasons], "audit": {...}}.
    """
    segs = route_segments(route["path"])
    violations = []
    longest = max(s["length_m"] for s in segs)
    beacon_ok = longest <= BEACON_SPACING_MAX_M
    if not beacon_ok:
        violations.append("BEACON_SPACING")
    gap_ok = GAP_MIN_MM <= gap_mm <= GAP_MAX_MM
    if not gap_ok:
        violations.append("GAP_RANGE")
    margin = lift_margin(vehicle, gap_mm)
    lift_ok = margin >= LIFT_MARGIN_MIN
    if not lift_ok:
        violations.append("LIFT_MARGIN")
    speed_ok = approach_ms <= APPROACH_SPEED_MAX_MS
    if not speed_ok:
        violations.append("APPROACH_SPEED")
    align_ok = lateral_m <= LATERAL_MAX_M and yaw_deg <= YAW_MAX_DEG
    if not align_ok:
        violations.append("ALIGNMENT")
    draw = fleet_power_kw(fleet)
    power_ok = draw <= DOCK_SUPPLY_KW
    if not power_ok:
        violations.append("POWER_BUDGET")
    return {"accepted": not violations, "violations": violations,
            "audit": {"longest_segment_m": longest,
                      "gap_mm": gap_mm, "lift_margin": margin,
                      "approach_ms": approach_ms,
                      "braking_distance_m": braking_distance_m(approach_ms),
                      "lateral_m": lateral_m, "yaw_deg": yaw_deg,
                      "fleet_draw_kw": draw,
                      "supply_kw": DOCK_SUPPLY_KW,
                      "beacon_ok": beacon_ok, "gap_ok": gap_ok,
                      "lift_ok": lift_ok, "speed_ok": speed_ok,
                      "align_ok": align_ok, "power_ok": power_ok}}


def plan_book():
    """The demo plan book: one compliant plan, five refused plans."""
    fast = dijkstra("ENTRY", "BERTH_2")
    compliant = dijkstra("ENTRY", "BERTH_2",
                         max_segment_m=BEACON_SPACING_MAX_M)
    return {
        "compliant_docking": check_plan(
            compliant, vehicle="pod", gap_mm=10.0, approach_ms=2.5,
            lateral_m=0.10, yaw_deg=0.8,
            fleet=("taxi", "pod", "shuttle")),
        "express_lane_fastest": check_plan(
            fast, vehicle="pod", gap_mm=10.0, approach_ms=2.5,
            lateral_m=0.10, yaw_deg=0.8,
            fleet=("taxi", "pod", "shuttle")),
        "wide_gap_capture": check_plan(
            compliant, vehicle="pod", gap_mm=14.0, approach_ms=2.5,
            lateral_m=0.10, yaw_deg=0.8,
            fleet=("taxi", "pod", "shuttle")),
        "fast_approach": check_plan(
            compliant, vehicle="pod", gap_mm=10.0, approach_ms=5.0,
            lateral_m=0.10, yaw_deg=0.8,
            fleet=("taxi", "pod", "shuttle")),
        "misaligned_capture": check_plan(
            compliant, vehicle="pod", gap_mm=10.0, approach_ms=2.5,
            lateral_m=0.60, yaw_deg=4.0,
            fleet=("taxi", "pod", "shuttle")),
        "overloaded_dock": check_plan(
            compliant, vehicle="pod", gap_mm=10.0, approach_ms=2.5,
            lateral_m=0.10, yaw_deg=0.8,
            fleet=("cargo", "cargo", "cargo", "shuttle", "pod")),
    }


# ================================================================ 7. kNN dockability classifier
# Features: (gap_mm / 10, lateral_m x 4, approach_ms / 2) - scaled
# so a drifting gap, a sideways offset and a hot approach all count,
# and no single feature shouts over the others. Labels follow the
# capture box: inside it the dock is ready, near its walls it is
# marginal, outside it the dock refuses. Training rows are stored
# in raw units (gap mm, lateral m, approach m/s).

_CLASSIFIER_TRAIN = [
    # (gap_mm, lateral_m, approach_ms, label)
    (10.0, 0.05, 2.0, "dockable"), (9.0, 0.10, 2.5, "dockable"),
    (11.0, 0.08, 1.8, "dockable"), (10.0, 0.15, 2.2, "dockable"),
    (9.5, 0.05, 2.8, "dockable"), (10.5, 0.12, 2.0, "dockable"),
    (12.0, 0.20, 2.8, "marginal"), (11.5, 0.30, 3.0, "marginal"),
    (12.5, 0.18, 2.5, "marginal"), (11.0, 0.35, 3.2, "marginal"),
    (12.0, 0.28, 3.5, "marginal"), (13.0, 0.22, 2.6, "marginal"),
    (14.0, 0.60, 4.0, "refused"), (15.0, 0.50, 5.0, "refused"),
    (13.5, 0.80, 4.5, "refused"), (16.0, 0.40, 6.0, "refused"),
    (14.5, 0.70, 3.8, "refused"), (12.0, 0.90, 5.5, "refused"),
    (10.0, 0.10, 5.0, "refused"), (11.0, 0.15, 5.5, "refused"),
    (10.0, 0.20, 4.8, "refused"),
]


def _features(gap_mm, lateral_m, approach_ms):
    return (gap_mm / 10.0, lateral_m * 4.0, approach_ms / 2.0)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def classify_docking(gap_mm, lateral_m, approach_ms, k=3):
    """kNN (k=3) dockability classifier: dockable / marginal / refused."""
    if gap_mm <= 0 or lateral_m < 0 or approach_ms < 0:
        raise ValueError("need gap > 0, lateral >= 0, approach >= 0")
    f = _features(gap_mm, lateral_m, approach_ms)
    ranked = sorted(_CLASSIFIER_TRAIN,
                    key=lambda row: _dist(f, _features(*row[:3])))
    votes = {}
    for row in ranked[:k]:
        votes[row[3]] = votes.get(row[3], 0) + 1
    return max(sorted(votes), key=lambda lab: votes[lab])


def docking_report():
    scenarios = {
        "nominal_pod": (10.0, 0.10, 2.5),
        "tight_gap_taxi": (9.0, 0.05, 2.0),
        "edge_gap_shuttle": (12.0, 0.20, 2.8),
        "fast_in_pod": (10.0, 0.10, 4.5),
        "wide_misaligned_cargo": (14.0, 0.60, 4.0),
    }
    return {name: classify_docking(*vals) for name, vals in scenarios.items()}


# ================================================================ 8. Advisory seats
def _trace(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def argon4_decide(topic="levitdock-x-demo"):
    """Gemini Argon 4 DECIDER seat: MAP->MODEL->STRESS->DECIDE.

    Names-only keys, live calls 0, tighten-only: can add HOLDs, never lift one.
    """
    stages = ["MAP", "MODEL", "STRESS", "DECIDE"]
    payload = {"seat": "gemini-argon-4", "topic": topic, "stages": stages}
    return {
        "seat": "Gemini Argon 4 Extended Thinking",
        "role": "DECIDER",
        "pipeline": stages,
        "tiers": [2048, 8192, 32768],
        "key": "names-only (GEMINI_API_KEY name checked, value never read)",
        "live_calls": 0,
        "decision": "PROCEED - sandbox simulation only, all claim gates stay HOLD",
        "can_override_hold": False,
        "trace_sha256_16": _trace(payload),
    }


def claude_advisor(topic="levitdock-x-demo"):
    """Muse advisor seat: advises, can never override a HOLD."""
    return {
        "seat": "Muse",
        "role": "ADVISOR",
        "key": "names-only (MODEL_API_KEY name checked, value never read)",
        "live_calls": 0,
        "advice": "Quote the lift margin with its gap next to it, keep published maglev speeds labelled as published, and let the interlocks refuse out loud: at a dock, the approach you reject is the one that saves the vehicle.",
        "can_override_hold": False,
    }


def google_flow_plan():
    """Google Flow seat: plan-only, nothing rendered, nothing live."""
    return {
        "seat": "Google Flow",
        "role": "PLAN-ONLY",
        "shots": ["a pod floating down the guideway toward the dock",
                  "the capture gap closing to ten millimetres",
                  "berths turning vehicles around in twenty-three minutes",
                  "the express lane with its beacon refusal stamped red",
                  "four vehicles berthed under a hundred kilowatts"],
        "rendered": False,
        "live": 0,
    }


def brains_report():
    return {"decider": argon4_decide(), "advisor": claude_advisor(),
            "flow": google_flow_plan()}


# ================================================================ checks
def run_checks():
    c = []

    def chk(name, ok):
        c.append((name, bool(ok)))

    # 1. levitation lift
    chk("pressure_1T", abs(magnetic_pressure_pa(1.0) - 397887.36) < 1.0)
    chk("pressure_scales_B_squared",
        abs(magnetic_pressure_pa(2.0) - 4 * magnetic_pressure_pa(1.0)) < 1e-6)
    chk("pressure_taxi", abs(magnetic_pressure_pa(0.9) - 322288.75) < 1.0)
    chk("lift_pod_nominal", abs(lift_force_n("pod") - 119366.2) < 1.0)
    chk("lift_cargo_nominal", abs(lift_force_n("cargo") - 286478.9) < 1.0)
    chk("lift_margin_pod", abs(lift_margin("pod") - 1.5215) < 0.001)
    chk("lift_margin_taxi", abs(lift_margin("taxi") - 1.6432) < 0.001)
    chk("lift_margin_shuttle", abs(lift_margin("shuttle") - 1.4319) < 0.001)
    chk("lift_margin_cargo", abs(lift_margin("cargo") - 1.4606) < 0.001)
    chk("lift_falls_with_gap",
        lift_force_n("pod", 14.0) < lift_force_n("pod", 10.0)
        < lift_force_n("pod", 8.0))
    chk("lift_gap15_below_weight", lift_margin("pod", 15.0) < 1.0)
    chk("lift_gap8_above_nominal", lift_margin("pod", 8.0) > 2.0)
    chk("gap_for_margin_115", abs(gap_for_margin("pod", 1.15) - 11.50) < 0.05)
    chk("gap_for_margin_100", abs(gap_for_margin("pod", 1.0) - 12.33) < 0.05)
    chk("gap_for_margin_falls_with_margin",
        gap_for_margin("pod", 1.5) < gap_for_margin("pod", 1.15))
    chk("lift_table_four_vehicles", len(lift_table()) == 4)
    chk("lift_table_all_positive_margins",
        all(row["lift_margin"] > 1.0 for row in lift_table()))
    # 2. docking approach
    chk("braking_3ms", abs(braking_distance_m(3.0) - 3.75) < 1e-9)
    chk("braking_5ms", abs(braking_distance_m(5.0) - 10.4167) < 0.001)
    chk("braking_zero", braking_distance_m(0.0) == 0.0)
    chk("braking_grows_with_speed",
        braking_distance_m(5.0) > braking_distance_m(3.0))
    chk("capture_nominal_ok",
        capture_envelope_ok(10.0, 2.5, 0.10, 0.8) is True)
    chk("capture_wide_gap_fails",
        capture_envelope_ok(14.0, 2.5, 0.10, 0.8) is False)
    chk("capture_fast_fails",
        capture_envelope_ok(10.0, 5.0, 0.10, 0.8) is False)
    chk("capture_misaligned_fails",
        capture_envelope_ok(10.0, 2.5, 0.60, 4.0) is False)
    rep = approach_report()
    chk("approach_report_margin", abs(rep["lift_margin"] - 1.5215) < 0.001)
    chk("approach_report_braking", abs(rep["braking_distance_m"] - 2.6042) < 0.001)
    chk("approach_report_capture", rep["capture_ok"] is True)
    # 3. labelled references
    refs = real_systems()
    chk("reference_count_3", len(refs) == 3)
    chk("reference_jr_fastest_top_speed",
        max(refs, key=lambda s: s["top_speed_kmh"])["name"] == "JR SCMaglev L0 Series")
    chk("reference_shanghai_430",
        [s for s in refs if s["name"] == "Shanghai Maglev"][0]["top_speed_kmh"] == 430)
    chk("reference_jr_500",
        [s for s in refs if s["name"] == "JR SCMaglev L0 Series"][0]["top_speed_kmh"] == 500)
    chk("reference_lengths",
        [s["length_km"] for s in refs] == [30.5, 42.8, 6.1])
    # 4. approach lanes
    chk("edge_time_express", abs(edge_time_s(900.0, 12.0) - 75.0) < 1e-9)
    fast = dijkstra("ENTRY", "BERTH_2")
    chk("fastest_uses_express", fast["path"][:2] == ["ENTRY", "JUNCTION"])
    chk("fastest_time", abs(fast["time_s"] - 187.5) < 1e-9)
    chk("fastest_distance", abs(fast["distance_m"] - 1650.0) < 1e-9)
    comp = dijkstra("ENTRY", "BERTH_2", max_segment_m=BEACON_SPACING_MAX_M)
    chk("compliant_avoids_express",
        all(s["length_m"] <= BEACON_SPACING_MAX_M
            for s in route_segments(comp["path"])))
    chk("compliant_time", abs(comp["time_s"] - 200.0) < 1e-9)
    chk("compliant_distance", abs(comp["distance_m"] - 1450.0) < 1e-9)
    chk("compliant_slower_than_fastest", comp["time_s"] > fast["time_s"])
    chk("route_segments_cover_distance",
        abs(sum(s["length_m"] for s in route_segments(comp["path"]))
            - comp["distance_m"]) < 1e-9)
    # 5. berths & power
    chk("turnaround_1380s", turnaround_s() == 1380.0)
    chk("turnaround_23min", abs(turnaround_s() / 60.0 - 23.0) < 1e-9)
    chk("per_berth_throughput", abs(3600.0 / turnaround_s() - 2.6087) < 0.001)
    chk("four_berth_throughput", abs(berth_throughput_per_hour(4) - 10.4348) < 0.001)
    chk("power_taxi", abs(berth_power_kw("taxi") - 12.5) < 1e-9)
    chk("power_pod", abs(berth_power_kw("pod") - 18.5) < 1e-9)
    chk("power_shuttle", abs(berth_power_kw("shuttle") - 24.5) < 1e-9)
    chk("power_cargo", abs(berth_power_kw("cargo") - 36.5) < 1e-9)
    chk("fleet_four_draw", abs(fleet_power_kw(["taxi", "pod", "shuttle", "cargo"]) - 92.0) < 1e-9)
    chk("fleet_four_within_supply",
        fleet_power_kw(["taxi", "pod", "shuttle", "cargo"]) <= DOCK_SUPPLY_KW)
    chk("fleet_five_cargo_over_supply",
        fleet_power_kw(["cargo", "cargo", "cargo", "shuttle", "pod"]) > DOCK_SUPPLY_KW)
    chk("berth_energy_pod", abs(berth_energy_kwh("pod") - 7.0917) < 0.001)
    chk("berth_energy_scales_with_power",
        berth_energy_kwh("cargo") > berth_energy_kwh("pod"))
    # 6. interlocks
    book = plan_book()
    chk("plan_compliant_accepted", book["compliant_docking"]["accepted"] is True)
    chk("plan_express_refused_beacon",
        book["express_lane_fastest"]["violations"] == ["BEACON_SPACING"])
    chk("plan_wide_gap_refused",
        book["wide_gap_capture"]["violations"] == ["GAP_RANGE", "LIFT_MARGIN"])
    chk("plan_fast_refused_speed",
        book["fast_approach"]["violations"] == ["APPROACH_SPEED"])
    chk("plan_misaligned_refused",
        book["misaligned_capture"]["violations"] == ["ALIGNMENT"])
    chk("plan_overloaded_refused_power",
        book["overloaded_dock"]["violations"] == ["POWER_BUDGET"])
    chk("plan_book_six", len(book) == 6)
    tight = check_plan(comp, vehicle="pod", gap_mm=11.0, approach_ms=2.5,
                       lateral_m=0.10, yaw_deg=0.8,
                       fleet=("taxi", "pod", "shuttle"))
    chk("plan_gap11_accepted", tight["accepted"] is True)
    wide = check_plan(comp, vehicle="pod", gap_mm=12.0, approach_ms=2.5,
                      lateral_m=0.10, yaw_deg=0.8,
                      fleet=("taxi", "pod", "shuttle"))
    chk("plan_gap12_refused_lift",
        wide["violations"] == ["LIFT_MARGIN"])
    # 7. classifier
    drep = docking_report()
    chk("classifier_nominal", drep["nominal_pod"] == "dockable")
    chk("classifier_tight", drep["tight_gap_taxi"] == "dockable")
    chk("classifier_edge_marginal", drep["edge_gap_shuttle"] == "marginal")
    chk("classifier_fast_refused", drep["fast_in_pod"] == "refused")
    chk("classifier_wide_refused", drep["wide_misaligned_cargo"] == "refused")
    # gates + seats
    chk("hold_gates_14", len(HOLD_GATES) == 14 and all(v.startswith("HOLD") for v in HOLD_GATES.values()))
    chk("gate_real_operation_hold", gate("real_vehicle_operation") == "HOLD")
    chk("gate_docking_hold", gate("real_docking_guidance") == "HOLD")
    chk("gate_certification_hold", gate("structural_certification_claim") == "HOLD")
    chk("gate_cryo_hold", gate("cryogenic_handling") == "HOLD")
    chk("gate_sandbox_proceed", gate("run_simulation") == "PROCEED")
    b = brains_report()
    chk("argon4_no_override", b["decider"]["can_override_hold"] is False)
    chk("claude_no_override", b["advisor"]["can_override_hold"] is False)
    chk("flow_plan_only", b["flow"]["rendered"] is False and b["flow"]["live"] == 0)
    return c


# ================================================================ CLI / demo
def demo():
    print("=" * 68)
    print("LevitDock-X - educational levitation docking station lab")
    print("by Darion Kellar | MIT | pure stdlib | zero network | EDUCATION ONLY")
    print("=" * 68)
    print("\n[1] Levitation lift - magnetic pressure p = B^2 / (2 mu0), gap 10 mm")
    for row in lift_table():
        print(f"    {row['vehicle']:8s}: mass {row['mass_kg']:7.0f} kg | "
              f"B {row['field_t']:.1f} T -> {row['pressure_pa']/1000:6.1f} kPa | "
              f"lift {row['lift_n']:9.0f} N vs weight {row['weight_n']:8.0f} N | "
              f"margin {row['lift_margin']:.2f}")
    print("    -> at 1 T the pressure is ~398 kPa: a 0.3 m^2 magnet lifts")
    print("       an 8-tonne pod 1.52x over. At a 15 mm gap that margin is")
    print("       0.68 - the vehicle falls. The gap IS the lift.")
    print("\n[2] Docking approach - braking distance + capture box")
    for v in (2.5, 3.0, 5.0):
        print(f"    approach {v:.1f} m/s -> braking {braking_distance_m(v):5.2f} m")
    print(f"    capture box: gap {GAP_MIN_MM:.0f}-{GAP_MAX_MM:.0f} mm, "
          f"speed <= {APPROACH_SPEED_MAX_MS:.1f} m/s, lateral <= {LATERAL_MAX_M:.2f} m, "
          f"yaw <= {YAW_MAX_DEG:.0f} deg, lift margin >= {LIFT_MARGIN_MIN}")
    print("    -> at 5 m/s the stopping distance is 10.42 m: already past")
    print("       the capture point. Slow down before the box, not in it.")
    print("\n[3] Real maglev systems - LABELLED published facts (not computed here)")
    for s in real_systems():
        print(f"    {s['name']:24s} {s['length_km']:5.1f} km, {s['top_speed_kmh']:3d} km/h "
              f"({s['country']}, {s['opened']}) - {s['note']}")
    print("\n[4] Approach lanes - Dijkstra over the lane graph")
    fast = dijkstra("ENTRY", "BERTH_2")
    comp = dijkstra("ENTRY", "BERTH_2", max_segment_m=BEACON_SPACING_MAX_M)
    print(f"    fastest   : {' -> '.join(fast['path'])} = {fast['time_s']:.1f} s "
          f"({fast['distance_m']:,.0f} m)")
    print(f"    compliant : {' -> '.join(comp['path'])} = {comp['time_s']:.1f} s "
          f"({comp['distance_m']:,.0f} m)")
    print("    -> the fastest route rides the 900 m express lane. Watch [6].")
    print("\n[5] Berths & power - 23-minute turnaround, 100 kW dock supply")
    rep = berth_report()
    print(f"    turnaround {rep['turnaround_s']:.0f} s ({rep['turnaround_min']:.0f} min) | "
          f"{rep['per_berth_per_hour']:.2f} vehicles/h per berth")
    print(f"    {rep['berths']} berths -> {rep['throughput_per_hour']:.2f} vehicles/h, "
          f"{rep['daily_capacity_18h']:.0f} per 18-hour day")
    print(f"    four vehicles draw {rep['four_vehicle_draw_kw']:.1f} kW of "
          f"{rep['supply_kw']:.0f} kW - {rep['supply_headroom_kw']:.1f} kW headroom")
    print("    -> the dock runs out of power before it runs out of berths.")
    print("\n[6] Docking interlocks - plans are REFUSED, with the reason named")
    for name, verdict in plan_book().items():
        state = "ACCEPTED" if verdict["accepted"] else \
            "REFUSED (" + ", ".join(verdict["violations"]) + ")"
        print(f"    {name:22s} -> {state}")
    print("    -> beacons every 500 m, gap 8-12 mm, lift margin 1.15,")
    print("       3 m/s at capture, aligned within 0.25 m and 2 deg,")
    print("       and the fleet under 100 kW. No exceptions.")
    print("\n[7] Dockability classifier (TinyML kNN)")
    for name, label in docking_report().items():
        print(f"    {name:22s} -> {label}")
    print("\n[8] Advisory seats")
    print(f"    decider: {argon4_decide()['decision']} trace={argon4_decide()['trace_sha256_16']}")
    print(f"    advisor can_override_hold = {claude_advisor()['can_override_hold']}")
    print(f"    flow rendered = {google_flow_plan()['rendered']} live = {google_flow_plan()['live']}")
    print(f"\n[9] HOLD gates: {len(HOLD_GATES)}")
    for k in HOLD_GATES:
        print(f"    HOLD  {k}")
    checks = run_checks()
    passed = sum(1 for _, ok in checks if ok)
    print(f"\nCHECKS {passed}/{len(checks)}")
    for name, ok in checks:
        if not ok:
            print(f"    FAIL {name}")
    verdict = "PROCEED" if passed == len(checks) else "HOLD"
    print(f"VERDICT {verdict} (sandbox simulation only) | HOLD gates {len(HOLD_GATES)}")
    return 0 if passed == len(checks) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "demo":
        return demo()
    cmd = argv[0]
    if cmd == "checks":
        checks = run_checks()
        for name, ok in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        print(f"CHECKS {sum(1 for _, ok in checks if ok)}/{len(checks)}")
        return 0 if all(ok for _, ok in checks) else 1
    if cmd == "lift":
        print(json.dumps(lift_table(), indent=2, sort_keys=True))
        return 0
    if cmd == "docking":
        print(json.dumps(approach_report(), indent=2, sort_keys=True))
        return 0
    if cmd == "history":
        print(json.dumps(real_systems(), indent=2, sort_keys=True))
        return 0
    if cmd == "routing":
        out = {"fastest": dijkstra("ENTRY", "BERTH_2"),
               "compliant": dijkstra("ENTRY", "BERTH_2",
                                     max_segment_m=BEACON_SPACING_MAX_M)}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "berths":
        print(json.dumps(berth_report(), indent=2, sort_keys=True))
        return 0
    if cmd == "power":
        rows = [{"vehicle": v, "power_kw": berth_power_kw(v),
                 "energy_kwh": berth_energy_kwh(v)} for v in VEHICLES]
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if cmd == "plans":
        print(json.dumps(plan_book(), indent=2, sort_keys=True))
        return 0
    if cmd == "classify":
        print(json.dumps(docking_report(), indent=2, sort_keys=True))
        return 0
    if cmd == "brains":
        print(json.dumps(brains_report(), indent=2, sort_keys=True))
        return 0
    if cmd == "holds":
        for k, v in HOLD_GATES.items():
            print(f"{k}: {v}")
        return 0
    if cmd == "hold-demo":
        action = argv[1] if len(argv) > 1 else "real_docking_guidance"
        print(f"{action}: {HOLD_GATES.get(action, 'PROCEED - not gated')}")
        return 2 if gate(action) == "HOLD" else 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
