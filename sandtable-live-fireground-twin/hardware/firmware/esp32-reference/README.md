# ESP32 + DW3000 reference firmware (not for the owner's boards)

**Not applicable to the owner's boards.** The owner's boards are Qorvo DWM3001CDK (nRF52833 + DW3110). This folder is an ESP32 sketch for ESP32 + DW3000 boards (Makerfabs-style), kept as a reference for future CIR work. There is no nRF toolchain on this bench.

## What it is

- `uwbkit_tagless/`: lane L08's firmware delta, copied byte for byte from `../tagless-uwb-echo-radar/hardware/firmware/uwbkit_tagless/` on Sat Nov 14. It is the bench's shared uwb-kit DW3000 firmware (kit fw 0.3.0, unmodified) plus a CIR (channel impulse response) capture. After the host sends `CIR ON`, the capture reads 64 accumulator taps around the first path of every ranging frame the board overhears, and sends them as binary frames over USB serial and over UDP port 1946. `DELTA.md` lists the four hooks.
- `make_merged.py`: L08's script that regenerates `uwbkit_tagless/` from the current kit firmware (`python make_merged.py ../../../../uwb-kit/firmware`).
- `CIR_NOTES.md`: L08's notes on what the DW3000 driver exposes (accumulator read, first-path index, diagnostics). The DW3110 belongs to the same family, so these notes are the starting point for a DWM3001CDK port.

## Build status (honest)

Before the board model was confirmed, this sketch was compiled once on this bench: `arduino-cli compile --fqbn esp32:esp32:esp32` returned exit 0, and the log is `../../build_logs/uwbkit_tagless__esp32_esp32_esp32.txt`. So it is compile-verified for ESP32. It has never run on a radio. The binaries were taken out of the repository, and nothing here is meant to be flashed this weekend.

## What a DWM3001CDK version would need (future work, not attempted)

The same accumulator read (`dwt_readaccdata` plus the CIA diagnostics, per `CIR_NOTES.md`), placed in firmware for the nRF52833 built with Qorvo's SDK for that board and toolchain. On top of that, a host path that delivers the frames next to the ranging stream. The host-side parser for L08's frame format is already in `sandtable/scout/source_kit.py`.
