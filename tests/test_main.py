"""Tests for LevitDock-X: every number checked against the model."""
import math

import pytest

import main as m


# ---- levitation lift (magnetic pressure p = B^2 / 2 mu0)
def test_pressure_1T():
    assert abs(m.magnetic_pressure_pa(1.0) - 397887.36) < 1.0


def test_pressure_scales_B_squared():
    assert abs(m.magnetic_pressure_pa(2.0)
               - 4 * m.magnetic_pressure_pa(1.0)) < 1e-6


def test_lift_margins():
    assert abs(m.lift_margin("taxi") - 1.6432) < 0.001
    assert abs(m.lift_margin("pod") - 1.5215) < 0.001
    assert abs(m.lift_margin("shuttle") - 1.4319) < 0.001
    assert abs(m.lift_margin("cargo") - 1.4606) < 0.001


def test_lift_falls_with_gap():
    assert (m.lift_force_n("pod", 14.0) < m.lift_force_n("pod", 10.0)
            < m.lift_force_n("pod", 8.0))


def test_lift_gap15_below_weight():
    assert m.lift_margin("pod", 15.0) < 1.0


def test_gap_for_margin():
    assert abs(m.gap_for_margin("pod", 1.15) - 11.50) < 0.05
    assert abs(m.gap_for_margin("pod", 1.0) - 12.33) < 0.05


def test_lift_table():
    rows = m.lift_table()
    assert len(rows) == 4
    assert all(row["lift_margin"] > 1.0 for row in rows)


def test_unknown_vehicle():
    with pytest.raises(ValueError):
        m.lift_force_n("hoverbus")


# ---- docking approach (braking distance + capture box)
def test_braking_distances():
    assert abs(m.braking_distance_m(3.0) - 3.75) < 1e-9
    assert abs(m.braking_distance_m(5.0) - 10.4167) < 0.001
    assert m.braking_distance_m(0.0) == 0.0


def test_capture_envelope():
    assert m.capture_envelope_ok(10.0, 2.5, 0.10, 0.8) is True
    assert m.capture_envelope_ok(14.0, 2.5, 0.10, 0.8) is False
    assert m.capture_envelope_ok(10.0, 5.0, 0.10, 0.8) is False
    assert m.capture_envelope_ok(10.0, 2.5, 0.60, 4.0) is False


def test_approach_report():
    rep = m.approach_report()
    assert abs(rep["lift_margin"] - 1.5215) < 0.001
    assert abs(rep["braking_distance_m"] - 2.6042) < 0.001
    assert rep["capture_ok"] is True


# ---- labelled references
def test_real_systems():
    refs = m.real_systems()
    assert len(refs) == 3
    assert [s["length_km"] for s in refs] == [30.5, 42.8, 6.1]
    shanghai = [s for s in refs if s["name"] == "Shanghai Maglev"][0]
    assert shanghai["top_speed_kmh"] == 430 and shanghai["opened"] == 2004
    jr = [s for s in refs if s["name"] == "JR SCMaglev L0 Series"][0]
    assert jr["top_speed_kmh"] == 500


# ---- approach lanes (Dijkstra)
def test_fastest_route():
    fast = m.dijkstra("ENTRY", "BERTH_2")
    assert fast["path"][:2] == ["ENTRY", "JUNCTION"]
    assert abs(fast["time_s"] - 187.5) < 1e-9
    assert abs(fast["distance_m"] - 1650.0) < 1e-9


def test_compliant_route():
    comp = m.dijkstra("ENTRY", "BERTH_2",
                      max_segment_m=m.BEACON_SPACING_MAX_M)
    assert abs(comp["time_s"] - 200.0) < 1e-9
    assert abs(comp["distance_m"] - 1450.0) < 1e-9
    assert all(s["length_m"] <= m.BEACON_SPACING_MAX_M
               for s in m.route_segments(comp["path"]))


def test_compliant_slower_than_fastest():
    fast = m.dijkstra("ENTRY", "BERTH_2")
    comp = m.dijkstra("ENTRY", "BERTH_2",
                      max_segment_m=m.BEACON_SPACING_MAX_M)
    assert comp["time_s"] > fast["time_s"]


def test_unknown_node():
    with pytest.raises(ValueError):
        m.dijkstra("ENTRY", "NOWHERE")


# ---- berths & power
def test_turnaround():
    assert m.turnaround_s() == 1380.0
    assert abs(m.berth_throughput_per_hour(4) - 10.4348) < 0.001


def test_berth_power():
    assert abs(m.berth_power_kw("taxi") - 12.5) < 1e-9
    assert abs(m.berth_power_kw("pod") - 18.5) < 1e-9
    assert abs(m.berth_power_kw("shuttle") - 24.5) < 1e-9
    assert abs(m.berth_power_kw("cargo") - 36.5) < 1e-9


def test_fleet_power_budget():
    assert abs(m.fleet_power_kw(["taxi", "pod", "shuttle", "cargo"])
               - 92.0) < 1e-9
    assert m.fleet_power_kw(["taxi", "pod", "shuttle", "cargo"]) \
        <= m.DOCK_SUPPLY_KW
    assert m.fleet_power_kw(["cargo", "cargo", "cargo", "shuttle", "pod"]) \
        > m.DOCK_SUPPLY_KW


def test_berth_energy():
    assert abs(m.berth_energy_kwh("pod") - 7.0917) < 0.001
    assert m.berth_energy_kwh("cargo") > m.berth_energy_kwh("pod")


# ---- interlocks
def test_plan_book():
    book = m.plan_book()
    assert len(book) == 6
    assert book["compliant_docking"]["accepted"] is True
    assert book["express_lane_fastest"]["violations"] == ["BEACON_SPACING"]
    assert book["wide_gap_capture"]["violations"] == ["GAP_RANGE", "LIFT_MARGIN"]
    assert book["fast_approach"]["violations"] == ["APPROACH_SPEED"]
    assert book["misaligned_capture"]["violations"] == ["ALIGNMENT"]
    assert book["overloaded_dock"]["violations"] == ["POWER_BUDGET"]


def test_gap12_refused_on_lift_only():
    comp = m.dijkstra("ENTRY", "BERTH_2",
                      max_segment_m=m.BEACON_SPACING_MAX_M)
    wide = m.check_plan(comp, vehicle="pod", gap_mm=12.0, approach_ms=2.5,
                        lateral_m=0.10, yaw_deg=0.8,
                        fleet=("taxi", "pod", "shuttle"))
    assert wide["violations"] == ["LIFT_MARGIN"]


def test_gap11_accepted():
    comp = m.dijkstra("ENTRY", "BERTH_2",
                      max_segment_m=m.BEACON_SPACING_MAX_M)
    tight = m.check_plan(comp, vehicle="pod", gap_mm=11.0, approach_ms=2.5,
                         lateral_m=0.10, yaw_deg=0.8,
                         fleet=("taxi", "pod", "shuttle"))
    assert tight["accepted"] is True


# ---- classifier
def test_classifier_report():
    rep = m.docking_report()
    assert rep["nominal_pod"] == "dockable"
    assert rep["tight_gap_taxi"] == "dockable"
    assert rep["edge_gap_shuttle"] == "marginal"
    assert rep["fast_in_pod"] == "refused"
    assert rep["wide_misaligned_cargo"] == "refused"


def test_classifier_bad_input():
    with pytest.raises(ValueError):
        m.classify_docking(-1.0, 0.1, 2.0)


# ---- gates + seats
def test_hold_gates():
    assert len(m.HOLD_GATES) == 14
    assert all(v.startswith("HOLD") for v in m.HOLD_GATES.values())
    assert m.gate("real_docking_guidance") == "HOLD"
    assert m.gate("cryogenic_handling") == "HOLD"
    assert m.gate("run_simulation") == "PROCEED"


def test_brains_cannot_override():
    b = m.brains_report()
    assert b["decider"]["can_override_hold"] is False
    assert b["advisor"]["can_override_hold"] is False
    assert b["flow"]["rendered"] is False and b["flow"]["live"] == 0


def test_run_checks_all_pass():
    checks = m.run_checks()
    failed = [name for name, ok in checks if not ok]
    assert failed == [], f"failing checks: {failed}"
    assert len(checks) == 78
