# Scout radar engine: notes (everything here is SIMULATED)

`geometry.py`, `scene.py`, `sim.py`, `dsp.py`. No board has produced a single frame for this code. The simulator
and the DSP were written by the same author in the same afternoon, so every number below is optimistic.

## Signal model (sim.py)

Every ordered pair of pucks tx -> rx gives one complex CIR frame per 50 ms cycle (20 Hz, 12 s, 96 taps):

```
h[k] = g e^{j phi} ( S/d p(t_k) + sum_i a_i (1 + f_i(t)) e^{j th_i} p(t_k - tau_i) + a_v(t) e^{j th_v(t)} p(t_k - tau_v(t)) ) * 10000 + n[k]
t_k  = (k - 8 - u - lead - early) * 1.0016 ns
```

Paths: the direct path (amplitude 1/d, times the knife-edge loss S when the body is near the line of sight);
first-order wall reflections from the twin's room polygon (image source, floor-level 2-D, |Gamma| 0.3-0.5 per wall,
phase pi, amplitude Gamma/path); a ceiling bounce (Gamma 0.3 times cos^2 of the elevation: a vertical puck
antenna); the debris (rcs 2.5 m^2) and five weak furniture scatterers along the walls (0.02-0.15 m^2), all with
the bistatic radar equation sqrt(rcs/4pi)/(R1 R2); the person, lying, chest at 0.25 m, rcs 0.4-0.9 m^2, a 0.14 m
cylinder (near-surface path), breathing 11-18 per minute with 3 mm surface displacement seen from the floor (a link
broadside to the body sees all of it, end-on 40 %), plus a heartbeat, sway and RCS fluctuation.

| Constant | Value | Source |
|---|---|---|
| carrier, wavelength | 6489.6 MHz, 46.2 mm | L08 `wire.py` (DW3000 channel 5) |
| tap, path per tap | 1.0016 ns, 0.300 m | L08 `wire.py` (1 / (2 x 499.2 MHz)) |
| first path at tap | 8 (`K0`) | L08 `wire.py` |
| taps per frame | 96 (L08: 64) | new: a room needs ~26 m of excess path |
| pulse | Gaussian 0.8 ns + pre/post lobes | L08 `sim/core.py` |
| direct-path SNR | 36 dB at 1 m | L08 `DEFAULT_IMPAIR` |
| first-path index error | 0.09 tap RMS, 0.4 % whole-tap, 4 % 2-4 taps early, pulse peak 1 tap after it | L08 (code values) |
| carrier phase, sampling offset | U(0, 2 pi), U(0, 1) tap per frame | L08 |
| gain jitter, frame loss | 3 %, 2 % | L08 |
| static-path flicker | 0.4 RMS complex OU, 0.5 s | L08 (fitted there to a real DW1000 recording) |
| ranges | 10 Hz, 3 cm, 3 % lost; rssi/fp/los formulas | L08 v1 model |
| shadowing | ITU-R P.526 knife edge, capped 25 dB | L08 |
| person rcs, radius, rate wander, heartbeat, sway | 0.4-0.9 m^2, 0.14 m, 3 %, 0.25 mm at 1.15 Hz, 0.5 mm OU 2 s | L08 (seated sway) |
| breathing displacement | 3 mm (L08: 4 mm standing chest) | new: lateral torso motion seen from floor level, an assumption |
| wall, ceiling, debris, furniture | above | new, assumptions |

Not modelled: antenna patterns (except the ceiling term), second-order bounces, the floor bounce, wall
transmission, the crew standing in the doorway, chip artefacts, clock drift, heat or smoke effects (negligible at
6.5 GHz).

## DSP (dsp.py)

1. `self_locate`: classical MDS on median two-way ranges, then SMACOF. Own frame (centroid at the origin, P1 on +x, P2 on the +y side).
2. Per ordered link (L08 stages 2-3, copied): FFT phase-ramp shift to the first-path index, drop early reports, re-seat whole-tap errors, lock the leading edge to the link's mean pulse, divide by the direct path. Both directions of a pair are then averaged.
3. `static_echoes`: the time average, minus a direct-pulse template (the complex median of all links' profiles), peaks at 12 dB or more above the noise of the mean, sub-tap by log-parabola. `amp` is relative to the direct path.
4. `find_victims` (new, because thrown pucks never see an empty room): per tap, remove the static mean. Breathing is phase modulation, so its dynamic part is perpendicular to the echo's own static phasor. Project onto that quadrature (U); the other quadrature (V) measures the tap's flicker and noise without the breathing. The per-link statistic is U power over (noise + 1.5 x the flicker measured on V, shaped by the pooled flicker spectrum). For every 0.1 m cell inside `bounds` and every breathing frequency, sum the soft-capped statistic of all links at the cell's predicted tap (its ellipse). Threshold: chi-square tail, p_fa 1e-2 per scan over about 5000 cells x frequencies. Then Gauss-Newton on the ellipse equations (L08 stage 8). The rate comes from the summed spectrum at the used taps. A motion band (0.8-4 Hz) gives "moving".

## Measured (simulated, room r310, seeds 300-315, victim within 4.5 m of the nearest puck)

| Pucks | Reported | Within 0.6 m | Position errors | Rate errors | False alarms, empty room |
|---|---|---|---|---|---|
| 5 | 9/16 | 8/16 | 0.04-0.31 m (one 0.68) | at most 1.5 /min | 0/16 |
| 4 | 9/16 | 9/16 | 0.04-0.55 m | at most 1.5 /min | 0/16 |
| 3 | 5/16 | 4/16 | 0.12-0.41 m, one ghost 9.4 m off | at most 1.7 /min | 0/16 |
| 2 | presence on about 1/3 of seeds; range right in about half of those | | | | about 5/24 |

Wall echoes: 38/40 and 39/40 found within one tap (seeds 310, 312). Self-location residual: 1-3 mm (3 cm range
noise averaged over 12 s). A still victim (no breathing, no motion) is never reported. These rates come from ad-hoc
runs during the build; the R7 evaluation script should regenerate them into `results/`.

## Known weaknesses (plain words)

- **Reach.** In 12 s under this noise model, a victim more than about 5 m from every puck is usually missed (0 of
  4 seeds at 6 m or more in an early run). By default the scene puts the victim within 4.5 m of the nearest puck
  (`victim_reach_m`; None places it anywhere in the far part). Even within reach, only about half are found. Ways
  to reach further: throw deeper, scan longer, a longer preamble.
- **One target.** A second person shows in the map only.
- **The scene must be still during the scan.** The crew in the doorway, swinging doors and fire movement are not
  modelled. Any of them would show as motion.
- **Floor-level 2-D geometry.** The pucks lie on the floor; antenna patterns and the floor bounce at 4 cm height are
  not modelled, and real link budgets at floor level may be much worse.
- **Two pucks are unreliable.** With nothing to corroborate one link, flicker outliers give false presence about one
  time in five, so confidence is capped at 0.3.
- **A breathing person can be masked** when their echo lands on the same tap as a strong wall echo on many links.
- **Validation is simulation-only, by a simulator written alongside the DSP.** L08 reached the same conclusion: real
  boards will be worse.
