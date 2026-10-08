"""Generate synthetic ICU stays in the canonical table format.

What this is for
    * Running the whole pipeline without credentialed data (tests, CI, the public demo).
    * Patients for the decision-support screen. The screen never shows a real patient.

What this is not
    The generator is not fitted to MIMIC-IV and its numbers mean nothing clinically.
    It only has the same *structure* as the real data: irregular measurement times,
    sparse labs, lactate measured more often in sicker patients, vasopressors started
    after sustained low blood pressure, and deaths after ICU discharge.

How a stay is generated
    1. Patient characteristics: age, sex, era, care unit, hidden baseline severity.
    2. A hidden path through three stages (0 stable, 1 organ dysfunction, 2 shock),
       ending in death or ICU discharge. Stage changes follow a continuous-time Markov
       chain whose rates rise with baseline severity.
    3. Measurements on an irregular schedule. Each variable drifts towards the mean of
       the current stage (an Ornstein-Uhlenbeck process), so values are autocorrelated
       the way bedside vital signs are.
    4. Treatment decisions that react to the measurements, as clinicians do. A
       vasopressor raises blood pressure, which hides part of the patient's severity.
    5. SOFA over the preceding 24 hours, and the Sepsis-3 recognition time R.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sepsis_decision_support.data.canonical_tables import (
    ERA_LABELS,
    MEASUREMENTS,
    RECOGNITION,
    SOFA_HOURLY,
    STAYS,
    TREATMENTS,
    CanonicalTables,
    coerce_to_specification,
)

STABLE, ORGAN_DYSFUNCTION, SHOCK = 0, 1, 2
DEATH, DISCHARGE = "death", "discharge"

SYNTHETIC_SUBJECT_ID_START = 90_000_000
SYNTHETIC_ADMISSION_ID_START = 92_000_000
SYNTHETIC_STAY_ID_START = 93_000_000

VITAL_SIGNS = (
    "heart_rate",
    "mean_arterial_pressure",
    "systolic_blood_pressure",
    "respiratory_rate",
    "spo2",
    "temperature",
)
LABORATORY_TESTS = ("creatinine", "platelets", "bilirubin", "white_blood_cells")


@dataclass(frozen=True)
class EmissionProfile:
    """How one variable behaves: mean and spread per stage, and how fast it moves.

    ``mean_by_stage[k]`` is the level the variable drifts towards in stage k.
    ``reversion_per_hour`` is the Ornstein-Uhlenbeck rate: after Δ hours, a deviation
    from the stage mean shrinks by a factor exp(-reversion_per_hour * Δ).
    """

    mean_by_stage: tuple[float, float, float]
    standard_deviation: float
    reversion_per_hour: float
    decimals: int = 1


@dataclass(frozen=True)
class GeneratorParameters:
    """All knobs of the generator. Defaults give plausible-looking (not realistic) stays."""

    # Hidden stage changes, events per hour, before adjusting for baseline severity.
    worsening_rate_per_hour: tuple[float, float] = (0.015, 0.020)  # 0->1, 1->2
    improvement_rate_per_hour: tuple[float, float] = (0.025, 0.040)  # 1->0, 2->1
    death_rate_per_hour: tuple[float, float, float] = (0.0001, 0.0007, 0.0060)
    discharge_rate_per_hour: tuple[float, float, float] = (0.0200, 0.0040, 0.0003)
    severity_effect_on_worsening: float = 0.45
    severity_effect_on_death: float = 0.60
    # Later eras have slightly lower death rates, so the temporal hold-out sees drift.
    era_effect_on_death: float = -0.08

    emissions: dict[str, EmissionProfile] = field(
        default_factory=lambda: {
            "heart_rate": EmissionProfile((88.0, 104.0, 118.0), 9.0, 0.6, 0),
            "mean_arterial_pressure": EmissionProfile((81.0, 74.0, 58.0), 6.0, 0.8, 0),
            "systolic_blood_pressure": EmissionProfile((122.0, 108.0, 88.0), 9.0, 0.8, 0),
            "respiratory_rate": EmissionProfile((18.0, 23.0, 27.0), 3.0, 0.7, 0),
            "spo2": EmissionProfile((97.0, 95.0, 92.5), 1.6, 0.9, 0),
            "temperature": EmissionProfile((37.0, 37.9, 38.2), 0.35, 0.3, 1),
            "lactate": EmissionProfile((1.3, 2.1, 4.2), 0.55, 0.15, 1),
            "creatinine": EmissionProfile((1.0, 1.6, 2.4), 0.25, 0.05, 2),
            "platelets": EmissionProfile((230.0, 170.0, 110.0), 30.0, 0.04, 0),
            "bilirubin": EmissionProfile((0.7, 1.3, 2.3), 0.25, 0.04, 1),
            "white_blood_cells": EmissionProfile((10.0, 14.0, 17.0), 2.0, 0.08, 1),
            "gcs_total": EmissionProfile((14.5, 13.0, 10.5), 1.2, 0.3, 0),
        }
    )
    # Treatment effects on what is measured (not on the hidden stage).
    vasopressor_effect_on_map_mmhg: float = 13.0
    vasopressor_effect_on_systolic_mmhg: float = 18.0
    oxygen_effect_on_spo2_percent: float = 2.5

    # Clinician behaviour.
    probability_vasopressor_after_sustained_hypotension: float = 0.75
    probability_vasopressor_on_any_single_low_map: float = 0.04
    probability_sepsis_suspected: float = 0.85
    mean_hours_admission_to_antibiotic: float = 5.0

    # How often things are measured (hours between measurements).
    vital_sign_interval_hours: tuple[float, float] = (0.5, 1.5)
    laboratory_interval_hours_by_stage: tuple[float, float, float] = (22.0, 14.0, 8.0)
    lactate_interval_hours_by_stage: tuple[float, float, float] = (36.0, 10.0, 4.0)
    gcs_interval_hours: float = 4.0

    maximum_stay_hours: float = 21 * 24.0


def generate_canonical_tables(
    number_of_stays: int,
    seed: int,
    parameters: GeneratorParameters | None = None,
) -> CanonicalTables:
    """Generate ``number_of_stays`` synthetic ICU stays.

    Args:
        number_of_stays: how many ICU stays to create (some belong to the same patient).
        seed: random seed; the same seed always gives the same tables.
        parameters: generator settings; defaults are used when omitted.

    Returns:
        CanonicalTables with the five tables described in ``canonical_tables``.
    """
    parameters = parameters or GeneratorParameters()
    random = np.random.default_rng(seed)
    patients = _sample_stay_characteristics(number_of_stays, random)

    stay_rows: list[dict] = []
    recognition_rows: list[dict] = []
    measurement_rows: list[tuple] = []
    treatment_rows: list[tuple] = []
    sofa_rows: list[tuple] = []

    for patient in patients:
        stay = _simulate_one_stay(patient, parameters, random)
        stay_rows.append(stay["stay_row"])
        if stay["recognition_row"] is not None:
            recognition_rows.append(stay["recognition_row"])
        measurement_rows.extend(stay["measurement_rows"])
        treatment_rows.extend(stay["treatment_rows"])
        sofa_rows.extend(stay["sofa_rows"])

    stays = coerce_to_specification(pd.DataFrame(stay_rows), STAYS)
    recognition = coerce_to_specification(
        pd.DataFrame(recognition_rows, columns=list(RECOGNITION.all_columns)), RECOGNITION
    )
    measurements = coerce_to_specification(
        pd.DataFrame(measurement_rows, columns=["stay_id", "charttime", "variable", "value"]),
        MEASUREMENTS,
    )
    treatments = coerce_to_specification(
        pd.DataFrame(
            treatment_rows, columns=["stay_id", "treatment", "start_time", "end_time", "rate"]
        ),
        TREATMENTS,
    )
    sofa_hourly = coerce_to_specification(
        pd.DataFrame(
            sofa_rows, columns=["stay_id", "hour_end_time", "sofa_24h", "cardiovascular_24h"]
        ),
        SOFA_HOURLY,
    )
    return CanonicalTables(stays, recognition, measurements, treatments, sofa_hourly)


# ---------------------------------------------------------------------------
# Step 1: who the patients are
# ---------------------------------------------------------------------------


def _sample_stay_characteristics(number_of_stays: int, random: np.random.Generator) -> list[dict]:
    """Patient-level and stay-level characteristics. About 15% of patients have two stays."""
    care_units = np.array(
        [
            "Medical ICU",
            "Surgical ICU",
            "Cardiac Vascular ICU",
            "Coronary Care Unit",
            "Trauma Surgical ICU",
            "Neuro ICU",
        ]
    )
    care_unit_probabilities = np.array([0.40, 0.15, 0.15, 0.10, 0.10, 0.10])
    race_groups = np.array(["White", "Black", "Hispanic", "Asian", "Other", "Unknown"])
    race_probabilities = np.array([0.64, 0.12, 0.06, 0.04, 0.08, 0.06])

    stays: list[dict] = []
    # Identifiers start at 90, 92 and 93 million, outside the 10-39 million ranges that
    # MIMIC-IV uses, so a synthetic identifier can never be mistaken for a real one.
    subject_id = SYNTHETIC_SUBJECT_ID_START
    stay_id = SYNTHETIC_STAY_ID_START
    while len(stays) < number_of_stays:
        subject_id += 1
        age = float(np.clip(random.normal(64, 16), 18, 95))
        sex = "M" if random.random() < 0.56 else "F"
        race_group = str(random.choice(race_groups, p=race_probabilities))
        baseline_severity = float(random.normal())
        era_index = int(random.integers(len(ERA_LABELS)))
        first_admission_hours = float(random.uniform(0, 3 * 365 * 24))  # within the era's 3 years
        number_of_stays_for_patient = 2 if random.random() < 0.15 else 1
        for stay_number in range(number_of_stays_for_patient):
            if len(stays) >= number_of_stays:
                break
            stay_id += 1
            care_unit = str(random.choice(care_units, p=care_unit_probabilities))
            stays.append(
                {
                    "stay_id": stay_id,
                    "subject_id": subject_id,
                    "hadm_id": SYNTHETIC_ADMISSION_ID_START + stay_id - SYNTHETIC_STAY_ID_START,
                    "age_years": round(age + stay_number * 0.5, 1),
                    "sex": sex,
                    "race_group": race_group,
                    "first_careunit": care_unit,
                    "hospital_service": _sample_hospital_service(care_unit, random),
                    "era_index": era_index,
                    "baseline_severity": baseline_severity + float(random.normal(0, 0.3)),
                    "is_first_icu_stay": stay_number == 0,
                    "admission_offset_hours": first_admission_hours + stay_number * 24 * 120,
                }
            )
    return stays


def _sample_hospital_service(care_unit: str, random: np.random.Generator) -> str:
    """Hospital service at ICU admission. Cardiac surgery patients mostly go to the CVICU."""
    if care_unit == "Cardiac Vascular ICU":
        return str(random.choice(["CSURG", "TSURG", "VSURG", "CMED"], p=[0.6, 0.1, 0.1, 0.2]))
    if care_unit in ("Surgical ICU", "Trauma Surgical ICU"):
        return str(random.choice(["SURG", "TRAUM", "NSURG", "MED"], p=[0.5, 0.3, 0.1, 0.1]))
    if care_unit == "Neuro ICU":
        return str(random.choice(["NMED", "NSURG", "MED"], p=[0.6, 0.3, 0.1]))
    return str(random.choice(["MED", "CMED", "OMED"], p=[0.8, 0.15, 0.05]))


# ---------------------------------------------------------------------------
# Steps 2-5: one stay
# ---------------------------------------------------------------------------


def _simulate_one_stay(
    patient: dict, parameters: GeneratorParameters, random: np.random.Generator
) -> dict:
    """Simulate the hidden path, measurements, treatments, SOFA and recognition for one stay."""
    era_start_year = 2008 + 3 * patient["era_index"]
    icu_intime = (
        pd.Timestamp(year=era_start_year, month=1, day=1)
        + pd.Timedelta(hours=patient["admission_offset_hours"])
    ).floor("min")

    path = _simulate_hidden_path(patient, parameters, random)
    stay_hours = path["end_hours"]
    icu_outtime = icu_intime + pd.Timedelta(hours=stay_hours)

    timeline = _simulate_measurements_and_treatments(patient, path, parameters, random)
    sofa_by_hour = _hourly_sofa(timeline, stay_hours)
    recognition = _recognition_times(patient, sofa_by_hour, stay_hours, parameters, random)

    death_time, date_of_death, hospital_discharge_hours = _deaths_after_icu(
        path, stay_hours, patient, random
    )

    def to_time(hours: float) -> pd.Timestamp:
        return icu_intime + pd.Timedelta(hours=float(hours))

    stay_id = patient["stay_id"]
    stay_row = {
        "stay_id": stay_id,
        "subject_id": patient["subject_id"],
        "hadm_id": patient["hadm_id"],
        "age_years": patient["age_years"],
        "sex": patient["sex"],
        "race_group": patient["race_group"],
        "first_careunit": patient["first_careunit"],
        "era": ERA_LABELS[patient["era_index"]],
        "hospital_service": patient["hospital_service"],
        "icu_intime": icu_intime,
        "icu_outtime": icu_outtime,
        "hospital_dischtime": to_time(hospital_discharge_hours),
        "death_time": to_time(death_time) if death_time is not None else pd.NaT,
        "date_of_death": to_time(date_of_death).normalize()
        if date_of_death is not None
        else pd.NaT,
        "is_first_icu_stay": patient["is_first_icu_stay"],
    }

    recognition_row = None
    if recognition is not None:
        recognition_row = {
            "stay_id": stay_id,
            **{name: to_time(hours) for name, hours in recognition.items()},
        }

    measurement_rows = [
        (stay_id, to_time(hours), variable, value)
        for hours, variable, value in timeline["measurements"]
    ]
    treatment_rows = [
        (stay_id, treatment, to_time(start), to_time(end), rate)
        for treatment, start, end, rate in timeline["treatments"]
    ]
    if recognition is not None:
        antibiotic_end = min(recognition["antibiotic_time"] + 7 * 24, stay_hours)
        treatment_rows.append(
            (
                stay_id,
                "antibiotic",
                to_time(recognition["antibiotic_time"]),
                to_time(antibiotic_end),
                np.nan,
            )
        )
    sofa_rows = [
        (stay_id, to_time(hour), float(sofa_total), float(cardiovascular))
        for hour, sofa_total, cardiovascular in sofa_by_hour
    ]
    return {
        "stay_row": stay_row,
        "recognition_row": recognition_row,
        "measurement_rows": measurement_rows,
        "treatment_rows": treatment_rows,
        "sofa_rows": sofa_rows,
    }


def _simulate_hidden_path(
    patient: dict, parameters: GeneratorParameters, random: np.random.Generator
) -> dict:
    """Continuous-time Markov chain over stages 0-2 with absorbing death and discharge.

    Returns:
        dict with ``segments`` (list of (start_hour, end_hour, stage)), ``end_hours``
        and ``ending`` ("death" or "discharge").
    """
    severity = patient["baseline_severity"]
    initial_logits = np.array([0.0, -0.4 + 0.6 * severity, -1.4 + 1.0 * severity])
    initial_probabilities = np.exp(initial_logits) / np.exp(initial_logits).sum()
    stage = int(random.choice(3, p=initial_probabilities))

    worsening_multiplier = np.exp(parameters.severity_effect_on_worsening * severity)
    death_multiplier = np.exp(
        parameters.severity_effect_on_death * severity
        + parameters.era_effect_on_death * patient["era_index"]
    )

    segments: list[tuple[float, float, int]] = []
    current_hour = 0.0
    while True:
        rates: dict[object, float] = {
            DEATH: parameters.death_rate_per_hour[stage] * death_multiplier,
            DISCHARGE: parameters.discharge_rate_per_hour[stage],
        }
        if stage < SHOCK:
            rates[stage + 1] = parameters.worsening_rate_per_hour[stage] * worsening_multiplier
        if stage > STABLE:
            rates[stage - 1] = parameters.improvement_rate_per_hour[stage - 1]
        total_rate = sum(rates.values())
        holding_hours = float(random.exponential(1.0 / total_rate))
        # ICU stays shorter than 2 hours are rare; give every stay at least that long.
        if not segments:
            holding_hours = max(holding_hours, 2.0)
        end_hour = min(current_hour + holding_hours, parameters.maximum_stay_hours)
        segments.append((current_hour, end_hour, stage))
        if end_hour >= parameters.maximum_stay_hours:
            return {"segments": segments, "end_hours": end_hour, "ending": DISCHARGE}
        targets = list(rates)
        next_state = targets[
            int(random.choice(len(targets), p=np.array(list(rates.values())) / total_rate))
        ]
        current_hour = end_hour
        if next_state in (DEATH, DISCHARGE):
            return {"segments": segments, "end_hours": end_hour, "ending": next_state}
        stage = int(next_state)


def _stage_at(path: dict, hour: float) -> int:
    for start, end, stage in path["segments"]:
        if start <= hour < end:
            return stage
    return path["segments"][-1][2]


def _simulate_measurements_and_treatments(
    patient: dict, path: dict, parameters: GeneratorParameters, random: np.random.Generator
) -> dict:
    """Measurements on an irregular schedule, with treatment decisions made along the way.

    The loop walks forward through vital-sign times. At each time the clinician (the
    policy below) sees the measurements so far and may start or stop a vasopressor or
    oxygen. Labs, lactate and GCS have their own, stage-dependent schedules.
    """
    end_hours = path["end_hours"]
    emissions = parameters.emissions
    current_values = {
        name: profile.mean_by_stage[_stage_at(path, 0.0)]
        + random.normal(0, profile.standard_deviation)
        for name, profile in emissions.items()
    }
    last_update_hour = {name: 0.0 for name in emissions}

    def draw(name: str, hour: float, shift: float = 0.0) -> float:
        """Ornstein-Uhlenbeck step from the last value towards the current stage mean."""
        profile = emissions[name]
        target = profile.mean_by_stage[_stage_at(path, hour)] + shift
        elapsed = max(hour - last_update_hour[name], 1e-3)
        decay = np.exp(-profile.reversion_per_hour * elapsed)
        noise_scale = profile.standard_deviation * np.sqrt(1.0 - decay**2)
        value = target + (current_values[name] - target) * decay + random.normal(0, noise_scale)
        current_values[name] = value
        last_update_hour[name] = hour
        return value

    measurements: list[tuple[float, str, float]] = []
    treatments: list[tuple[str, float, float, float]] = []

    vasopressor_start: float | None = None
    oxygen_start: float | None = None
    ventilation_start: float | None = None
    low_map_since: float | None = None
    next_lab_hour = 0.0
    next_lactate_hour = 0.0 if random.random() < 0.6 else 3.0
    next_gcs_hour = 0.0

    hour = 0.0
    while hour < end_hours:
        stage = _stage_at(path, hour)
        # A decision to start is recorded slightly ahead of the infusion actually running.
        vasopressor_decided = vasopressor_start is not None
        on_vasopressor = vasopressor_decided and hour >= vasopressor_start
        on_oxygen = oxygen_start is not None
        map_shift = parameters.vasopressor_effect_on_map_mmhg if on_vasopressor else 0.0
        systolic_shift = parameters.vasopressor_effect_on_systolic_mmhg if on_vasopressor else 0.0
        spo2_shift = parameters.oxygen_effect_on_spo2_percent if on_oxygen else 0.0

        mean_arterial_pressure = draw("mean_arterial_pressure", hour, map_shift)
        values = {
            "heart_rate": draw("heart_rate", hour),
            "mean_arterial_pressure": mean_arterial_pressure,
            "systolic_blood_pressure": draw("systolic_blood_pressure", hour, systolic_shift),
            "respiratory_rate": draw("respiratory_rate", hour),
            "spo2": min(draw("spo2", hour, spo2_shift), 100.0),
            "temperature": draw("temperature", hour),
        }
        for name in VITAL_SIGNS:
            # Each vital sign is occasionally not charted at a given time.
            if random.random() < 0.93:
                measurements.append((hour, name, round(values[name], emissions[name].decimals)))

        # --- vasopressor policy: react to sustained low blood pressure ------------------
        if mean_arterial_pressure < 65:
            low_map_since = hour if low_map_since is None else low_map_since
        else:
            low_map_since = None
        sustained_low = low_map_since is not None and hour - low_map_since >= 0.5
        if not vasopressor_decided:
            starts = (
                sustained_low
                and random.random() < parameters.probability_vasopressor_after_sustained_hypotension
            ) or (
                mean_arterial_pressure < 65
                and random.random() < parameters.probability_vasopressor_on_any_single_low_map
            )
            if starts:
                vasopressor_start = hour + float(random.uniform(0.1, 0.6))
        elif (
            on_vasopressor
            and stage < SHOCK
            and mean_arterial_pressure > 72
            and random.random() < 0.25
        ):
            dose = round(float(random.uniform(0.04, 0.35)), 3)
            treatments.append(("vasopressor", vasopressor_start, hour, dose))
            if random.random() < 0.8:
                treatments.append(("norepinephrine", vasopressor_start, hour, dose))
            vasopressor_start = None

        # --- oxygen and ventilation -----------------------------------------------------
        if not on_oxygen and (
            values["spo2"] < 92 or (stage >= ORGAN_DYSFUNCTION and random.random() < 0.05)
        ):
            oxygen_start = hour
        elif on_oxygen and stage == STABLE and values["spo2"] > 95 and random.random() < 0.1:
            treatments.append(("supplemental_oxygen", oxygen_start, hour, np.nan))
            oxygen_start = None
        if ventilation_start is None and stage == SHOCK and random.random() < 0.03:
            ventilation_start = hour
        elif ventilation_start is not None and stage < SHOCK and random.random() < 0.05:
            treatments.append(("invasive_ventilation", ventilation_start, hour, np.nan))
            ventilation_start = None

        # --- labs, lactate and GCS on their own schedules ---------------------------------
        if hour >= next_lab_hour:
            for name in LABORATORY_TESTS:
                measurements.append((hour, name, round(draw(name, hour), emissions[name].decimals)))
            next_lab_hour = hour + parameters.laboratory_interval_hours_by_stage[
                stage
            ] * random.uniform(0.7, 1.3)
        lactate_due = hour >= next_lactate_hour or (sustained_low and random.random() < 0.3)
        if lactate_due:
            lactate = max(draw("lactate", hour), 0.3)
            measurements.append((hour, "lactate", round(lactate, 1)))
            next_lactate_hour = hour + parameters.lactate_interval_hours_by_stage[
                stage
            ] * random.uniform(0.6, 1.4)
        if hour >= next_gcs_hour:
            gcs = float(np.clip(round(draw("gcs_total", hour)), 3, 15))
            measurements.append((hour, "gcs_total", gcs))
            next_gcs_hour = hour + parameters.gcs_interval_hours * random.uniform(0.7, 1.3)

        low, high = parameters.vital_sign_interval_hours
        # Sicker patients are watched more closely.
        interval = random.uniform(low, high) * (0.6 if stage == SHOCK else 1.0)
        hour = round(hour + interval, 3)

    for name, start in (
        ("vasopressor", vasopressor_start),
        ("supplemental_oxygen", oxygen_start),
        ("invasive_ventilation", ventilation_start),
    ):
        if start is not None and start < end_hours:
            rate = round(float(random.uniform(0.05, 0.4)), 3) if name == "vasopressor" else np.nan
            treatments.append((name, start, end_hours, rate))
            if name == "vasopressor" and random.random() < 0.8:
                treatments.append(("norepinephrine", start, end_hours, rate))
    return {"measurements": measurements, "treatments": treatments}


def _hourly_sofa(timeline: dict, stay_hours: float) -> list[tuple[float, float, float]]:
    """Simplified SOFA per hour: worst value of each organ score over the preceding 24 hours.

    Only for synthetic data. The real pipeline uses mimic-code's ``sofa`` concept.
    Missing components score 0, as in mimic-code.

    Returns:
        list of (hour_end, sofa_total_24h, cardiovascular_24h).
    """
    hourly_scores: dict[int, dict[str, float]] = {}

    def record(hour: float, organ: str, score: float) -> None:
        bucket = hourly_scores.setdefault(int(np.ceil(hour + 1e-9)), {})
        bucket[organ] = max(bucket.get(organ, 0.0), score)

    for hour, name, value in timeline["measurements"]:
        if name == "mean_arterial_pressure":
            record(hour, "cardiovascular", 1.0 if value < 70 else 0.0)
        elif name == "platelets":
            record(
                hour,
                "coagulation",
                0.0 if value >= 150 else 1.0 if value >= 100 else 2.0 if value >= 50 else 3.0,
            )
        elif name == "bilirubin":
            record(
                hour,
                "liver",
                0.0 if value < 1.2 else 1.0 if value < 2.0 else 2.0 if value < 6.0 else 3.0,
            )
        elif name == "creatinine":
            record(
                hour,
                "renal",
                0.0 if value < 1.2 else 1.0 if value < 2.0 else 2.0 if value < 3.5 else 3.0,
            )
        elif name == "gcs_total":
            record(
                hour,
                "cns",
                0.0 if value >= 15 else 1.0 if value >= 13 else 2.0 if value >= 10 else 3.0,
            )
        elif name == "spo2":
            record(hour, "respiration", 0.0 if value >= 96 else 1.0 if value >= 93 else 2.0)
    for treatment, start, end, rate in timeline["treatments"]:
        if treatment == "vasopressor":
            score = 3.0 if rate <= 0.1 else 4.0
            for hour in np.arange(np.floor(start), min(end, stay_hours) + 1):
                record(float(hour), "cardiovascular", score)

    rows: list[tuple[float, float, float]] = []
    last_hour = int(np.ceil(stay_hours))
    organs = ("cardiovascular", "coagulation", "liver", "renal", "cns", "respiration")
    for hour_end in range(1, last_hour + 1):
        window = [hourly_scores.get(h, {}) for h in range(max(1, hour_end - 23), hour_end + 1)]
        worst = {
            organ: max((scores.get(organ, 0.0) for scores in window), default=0.0)
            for organ in organs
        }
        rows.append((float(hour_end), float(sum(worst.values())), worst["cardiovascular"]))
    return rows


def _recognition_times(
    patient: dict,
    sofa_by_hour: list[tuple[float, float, float]],
    stay_hours: float,
    parameters: GeneratorParameters,
    random: np.random.Generator,
) -> dict[str, float] | None:
    """Sepsis-3 recognition, following the same rule as the real-data SQL.

    Infection is suspected (culture plus antibiotic) in most stays. Recognition time R is
    the moment the last of the three criteria is on record: antibiotic, culture, and the
    first SOFA >= 2 within 48 hours before to 24 hours after the suspicion time.
    """
    if random.random() > parameters.probability_sepsis_suspected:
        return None
    antibiotic_hour = float(random.exponential(parameters.mean_hours_admission_to_antibiotic))
    if antibiotic_hour >= stay_hours:
        return None
    culture_hour = max(0.0, antibiotic_hour - float(random.uniform(0.0, 2.0)))
    suspicion_hour = min(culture_hour, antibiotic_hour)
    qualifying_hours = [
        hour_end
        for hour_end, sofa_total, _ in sofa_by_hour
        if sofa_total >= 2 and suspicion_hour - 48 <= hour_end <= suspicion_hour + 24
    ]
    if not qualifying_hours:
        return None
    organ_dysfunction_hour = float(min(qualifying_hours))
    return {
        "recognition_time": max(antibiotic_hour, culture_hour, organ_dysfunction_hour),
        "antibiotic_time": antibiotic_hour,
        "culture_time": culture_hour,
        "organ_dysfunction_time": organ_dysfunction_hour,
    }


def _deaths_after_icu(
    path: dict, stay_hours: float, patient: dict, random: np.random.Generator
) -> tuple[float | None, float | None, float]:
    """Death time (in hospital), date of death (up to a year later) and hospital discharge.

    About a fifth of hospital deaths in the real cohort happen after ICU discharge, so the
    generator also produces some, more often after a stay that ended in a sicker stage.

    Returns:
        (death_hour or None, date_of_death_hour or None, hospital_discharge_hour), all in
        hours after ICU admission.
    """
    if path["ending"] == DEATH:
        return stay_hours, stay_hours, stay_hours
    last_stage = path["segments"][-1][2]
    hospital_discharge_hour = stay_hours + float(random.gamma(2.0, 36.0))
    probability_death_in_hospital = (
        0.03 + 0.04 * last_stage + 0.02 * max(patient["baseline_severity"], 0)
    )
    if random.random() < probability_death_in_hospital:
        death_hour = float(random.uniform(stay_hours + 2, hospital_discharge_hour + 2))
        return death_hour, death_hour, death_hour
    if random.random() < 0.07:
        return (
            None,
            hospital_discharge_hour + float(random.uniform(24, 365 * 24)),
            hospital_discharge_hour,
        )
    return None, None, hospital_discharge_hour
