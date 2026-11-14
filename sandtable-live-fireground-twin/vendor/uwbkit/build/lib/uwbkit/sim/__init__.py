"""uwbkit.sim: deterministic UWB two-way-ranging simulator driven by a JSON scenario file.

    from uwbkit.sim import Simulator, list_scenarios, load_scenario
    sim = Simulator("office-walk")          # built-in name | path to .json | inline dict
    msgs = sim.step(now)                    # call at sim.rate_hz with wall-clock (or synthetic) Unix seconds

Time comes only from ``step(now)``: the simulator never reads the system clock. For a given seed and
sequence of ``now`` values the output is bit-for-bit reproducible (numpy PCG64 stream seeded by
``seed``; ``survey()`` uses a separate stream).

Noise model (per range measurement between nodes a and b; every parameter is a key of the scenario
``"noise"`` object and is reported by ``Simulator.noise_model``; defaults in brackets):

    true   = 3-D slant distance between the true positions (anchors default z = 2.0 m, tags z = 1.0 m)
    walls  = room walls whose 2-D segment properly crosses the 2-D line a-b (walls are full height)
    d      = true
             + N(0, sigma_m^2 + sum over walls of nlos_extra_sigma_m^2)              [0.08 m; 0.15 m/wall]
             + sum over walls of nlos_bias_m * Gamma(k, 1/k), k = nlos_bias_gamma_k
               (always positive; mean nlos_bias_m, sd = mean/2)                     [0.30 m/wall; k = 4]
             + bias_m(a) + bias_m(b)            (optional per-node constant, default 0)
             + outlier with prob outlier_prob: + outlier_m * U(0.2, 1)               [2 %; 1.5 m]
               (a fraction outlier_neg_frac of outliers is short instead:
                - outlier_neg_scale * outlier_m * U(0.2, 1))                         [10 %; 0.25]
             clamped at >= 0
    raw    = d + antenna_offset_m  (the kit's constant antenna-delay correction)     [0.035 m]
    rssi   = rssi_1m_dbm - 10*path_loss_exp*log10(true / 1 m) - sum attenuation_db
             + N(0, rssi_sigma_db^2)                                  [-68 dBm; 2.0; 8 dB/wall; 1.5 dB]
    fp     = rssi - gap,  gap = fp_gap_los_db + fp_gap_sigma_db*|N(0,1)|
             + fp_nlos_frac * sum attenuation_db (+ fp_outlier_db on a long outlier)
                                                                      [2 dB; 2 dB; 0.8; 4 dB]
             i.e. through a wall the first path drops 1.8x the wall attenuation, total power 1.0x
    los    = 1 / (1 + exp((gap - los_mid_db) / los_scale_db))                        [8 dB; 1.2 dB]
             -> clear LOS: median 0.98, 5th percentile ~0.8; one 8 dB wall: median ~0.2; two walls: ~0
    dropout: the pair is omitted this cycle with probability
             dropout_prob * (1 + dropout_per_wall * walls) * (1 + max(0, true - far_m) / far_m), cap 0.95
                                                                      [3 %; 1.0; 10 m]
    rounding: d, raw to 1 mm; rssi, fp to 0.1 dB; los to 0.01

Motion: waypoints with absolute ``t``, ``speed`` (m/s from the previous waypoint) or ``hold_s``;
``path_mode`` ``linear`` (default; unrecognised values fall back to linear) or ``spline`` (time-parameterised
Catmull-Rom, zero velocity into holds); ``jitter_m`` adds smooth sway (per axis two sinusoids at 0.2-0.7 Hz,
RMS jitter_m). With ``loop`` (default true) each node's path repeats with its own period, a closing leg back to
the first waypoint is added when needed (node ``"close_loop": false`` disables it), and node ``events`` repeat
every ``duration_s`` (default: end of the longest path). With ``loop: false`` nodes stop at their last waypoint.
``truth()`` velocities are exact path derivatives (m per sim second).

Additive scenario keys (all optional): top-level ``description``; node ``speed`` (default leg speed, 1.0 m/s),
``bias_m``, ``close_loop``, ``at`` with 3 values ``[x, y, z]`` (also in waypoints, for 3-D motion);
per-wall ``nlos_extra_sigma_m``; every key of the noise model above.
"""
from .engine import Simulator
from .scenario import NOISE_DEFAULTS, ScenarioError, list_scenarios, load_scenario

__all__ = ["Simulator", "list_scenarios", "load_scenario", "ScenarioError", "NOISE_DEFAULTS"]
