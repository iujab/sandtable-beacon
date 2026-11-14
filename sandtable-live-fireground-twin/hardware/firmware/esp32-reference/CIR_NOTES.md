<!-- Copied from lane L08 (../tagless-uwb-echo-radar/hardware/CIR_NOTES.md) on Sat Nov 14 as a reference for future CIR work on the DW3000 family (the DW3110 on the owner's DWM3001CDK is a member). Not verified on any board. -->
# What the DW3000 actually exposes (checked before depending on it)

Sources: the Qorvo driver bundled in the Arduino library `UWB_DW1000_DW3000` 1.0.1 (installed at
`~/Arduino/libraries/UWB_DW1000_DW3000/src/qorvo/`), the Qorvo forum, and three papers. Nothing here has been
measured on a board by us.

| Fact | Where it comes from |
|---|---|
| The accumulator (complex CIR) is readable: `void dwt_readaccdata(uint8_t *buffer, uint16_t len, uint16_t accOffset)` | `dw3000_device_api.h:1771`, implementation `dw3000_device_api.cpp:1853` |
| Each sample is 6 bytes (3 real, 3 imaginary, 18-bit signed); the first octet of every read is a dummy; `accOffset` is a complex-sample index | header comment above the prototype (lines 1752-1770) |
| Accumulator memory is at `ACC_MEM_ID 0x150000`; reads beyond offset 127 go through the indirect pointer registers, which the driver handles | `dw3000_vals.h:49-56`, `dw3000_device_api.cpp:1858-1873` |
| First-path index of the Ipatov (preamble) CIR: `dwt_rxdiag_t.ipatovFpIndex`, 16 bits read from `IP_DIAG_8` (10.6 fixed point: 1/64 of a sample); accumulation count `ipatovAccumCount`; first-path amplitudes `ipatovF1..F3`; channel power `ipatovPower` | `dw3000_device_api.h:440-485`, `dw3000_device_api.cpp:2097-2228` |
| Full diagnostics must be enabled once: `dwt_configciadiag(DW_CIA_DIAG_LOG_ALL)` | `dw3000_device_api.h:1809`, `:315-319` |
| 1016 accumulator samples at 64 MHz PRF (992 at 16 MHz); one sample per ~1 ns (1/(2 x 499.2 MHz) = 1.0016 ns) | Qorvo forum threads "DWM3000EVB Raw CIR Capture Issues" and "DWM3000 CIR time between samples" |
| The accumulator is only valid until the receiver is re-enabled | DW3000 user manual; this is why the library's `UWB_DW3000::onIRQ()` wrapper cannot be used: it calls `dwt_rxenable()` before the application sees the frame (`UWB_DW3000.cpp`, RXFCG branch). Our firmware polls `SYS_STATUS` and reads the accumulator first. |

What the literature says is achievable with this class of radio:

- **Breathing from DW3000 CIR**: 41 taps per CIR, up to 77.5 CIRs per second over SPI on an nRF52840, 1.73 breaths/min
  mean absolute error on unseen subjects, magnitude only (arXiv 2504.03772, IEEE Sensors Journal 2025).
- **Device-free localisation with DW1000 CIR**: four anchors, six pairs, first-path alignment (the chip's
  first-path estimate is 64 times finer than the 1 ns tap), background variance subtraction, one ellipse per pair,
  particle filter: about 15 cm RMSE with 4 anchors, 25 cm with 3 (arXiv 2507.13938).
- **DW3000 bistatic radar sensor**: Santoro et al., IEEE Sensors Letters 2023.

What differs from those papers here: Tagless references the *phase* of every frame to its own direct path (so two
free-running radios become coherent and breathing shows up as phase), and fuses pairs with a Gauss-Newton solve on
the ellipse equations instead of a learned model.

## Budget per frame on the board (estimates, unverified)

- 64 taps x 6 bytes + 1 = 385 bytes over SPI at the library's 4 MHz: about 0.8 ms plus the diagnostics read.
- One binary frame = 280 bytes. Four received blinks per 50 ms cycle = 1120 bytes per cycle = 22.4 kB/s:
  needs 921600 baud on USB (the kit's 115200 carries about 40 frames per second and nothing else).

## DW1000 fallback (not built)

DW1000 exposes the same thing differently: accumulator register `0x25` (`ACC_MEM`, 4 bytes per sample: 16-bit I, 16-bit
Q, plus a dummy octet), first-path index `FP_INDEX` in `RX_TIME` (reg `0x15`, 10.6 fixed point), 1016 samples at 64 MHz PRF.
`tagless_cir.h` would need a second capture function (`dwt_readaccdata` exists in the DW1000 driver too, with a byte
offset rather than a sample index). The host side and the DSP do not change. Estimated port: 1-2 hours with a board.
