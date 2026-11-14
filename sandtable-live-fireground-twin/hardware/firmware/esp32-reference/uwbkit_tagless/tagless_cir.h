// tagless_cir.h -- CIR capture module for Qorvo DW3000 (header-only; include after <qorvo/dw3000.h>).
//
// After a good reception and BEFORE the receiver is re-enabled, read the first-path index from the CIA
// diagnostics and TL_CIR_N complex accumulator samples starting TL_CIR_PRE samples before the first path,
// and pack them into one binary frame for the host (tagless/io/boards.py parses it):
//
//   A5 5A | ver u8=1 | flags u8 | tx_id u8 | rx_id u8 | seq u16 | t_ms u32 | fp_q6 u16 | pacc u16 |
//   rxp_cdb i16 | fpp_cdb i16 | n u8 | fmt u8=0 | n * (I i16, Q i16) | crc16-ccitt u16        (little-endian)
//
// flags bits 0..1 = right shift applied to the 18-bit samples to fit int16 (host multiplies back).
// fp_q6 = first-path index relative to the first sample of the window, in 1/64 sample (DW3000 IP_DIAG_8 is 10.6).
// STATUS: compile-verified only. Never run on a real DW3000 by its authors.
#ifndef TAGLESS_CIR_H
#define TAGLESS_CIR_H

#include <Arduino.h>

#ifndef TL_CIR_N
#define TL_CIR_N 64          // samples per frame (1.0016 ns each)
#endif
#ifndef TL_CIR_PRE
#define TL_CIR_PRE 8         // samples kept before the first path (noise reference)
#endif
#define TL_ACC_LEN 1016      // Ipatov accumulator length at 64 MHz PRF (preamble code 9)
#define TL_FRAME_MAX (22 + TL_CIR_N * 4 + 2)

struct TlCirState {
  bool on = false;
  uint8_t cycle_hz = 20;
  uint32_t frames = 0;
  uint32_t fails = 0;
};
static TlCirState tl_cir;

static uint16_t tl_crc16(const uint8_t* d, size_t n) {   // CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
  }
  return c;
}

static inline int32_t tl_s18(const uint8_t* p) {          // 3 bytes little-endian, 18-bit signed
  int32_t v = (int32_t)p[0] | ((int32_t)p[1] << 8) | ((int32_t)(p[2] & 0x03) << 16);
  if (v & 0x20000) v -= 0x40000;
  return v;
}

static inline void tl_put16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }

// Build one CIR frame for the frame that was just received. Call before dwt_rxenable()/TX.
// Returns the frame length (0 if the diagnostics look invalid). `out` must hold TL_FRAME_MAX bytes.
static size_t tl_cir_capture(uint8_t tx_id, uint8_t rx_id, uint16_t seq, uint8_t* out) {
  static uint8_t acc[TL_CIR_N * 6 + 1];
  dwt_rxdiag_t dg;
  memset(&dg, 0, sizeof dg);
  dwt_readdiagnostics(&dg);                                // needs dwt_configciadiag(DW_CIA_DIAG_LOG_ALL) at init
  uint16_t fp = dg.ipatovFpIndex;                          // 10.6 fixed point, accumulator sample index
  uint16_t fp_int = fp >> 6;
  if (fp_int == 0 || fp_int >= TL_ACC_LEN || dg.ipatovAccumCount == 0) { tl_cir.fails++; return 0; }
  int start = (int)fp_int - TL_CIR_PRE;
  if (start < 0) start = 0;
  if (start > TL_ACC_LEN - TL_CIR_N) start = TL_ACC_LEN - TL_CIR_N;
  dwt_readaccdata(acc, (uint16_t)sizeof acc, (uint16_t)start);   // first octet is a dummy (driver note)
  int32_t peak = 1;
  for (int i = 0; i < TL_CIR_N * 2; i++) {
    int32_t v = tl_s18(acc + 1 + 3 * i);
    if (v < 0) v = -v;
    if (v > peak) peak = v;
  }
  uint8_t shift = 0;
  while ((peak >> shift) > 32767) shift++;
  // receive level and first-path level, DW3000 user manual 4.7.1 / 4.7.2 (64 MHz PRF constant 121.7)
  float N = (float)dg.ipatovAccumCount;
  uint8_t dgc = (uint8_t)((dwt_read8bitoffsetreg(0x30060, 3) >> 4) & 0x7);
  float rxp = dg.ipatovPower ? 10.0f * log10f((float)dg.ipatovPower * 2097152.0f / (N * N)) + 6.0f * dgc - 121.7f : -120.0f;
  float f1 = dg.ipatovF1 / 4.0f, f2 = dg.ipatovF2 / 4.0f, f3 = dg.ipatovF3 / 4.0f;
  float fs = f1 * f1 + f2 * f2 + f3 * f3;
  float fpp = fs > 0 ? 10.0f * log10f(fs / (N * N)) + 6.0f * dgc - 121.7f : -120.0f;

  uint8_t* p = out;
  *p++ = 0xA5; *p++ = 0x5A; *p++ = 1; *p++ = shift & 0x03;
  *p++ = tx_id; *p++ = rx_id;
  tl_put16(p, seq); p += 2;
  uint32_t ms = millis();
  p[0] = (uint8_t)ms; p[1] = (uint8_t)(ms >> 8); p[2] = (uint8_t)(ms >> 16); p[3] = (uint8_t)(ms >> 24); p += 4;
  tl_put16(p, (uint16_t)(fp - ((uint16_t)start << 6))); p += 2;
  tl_put16(p, dg.ipatovAccumCount); p += 2;
  tl_put16(p, (uint16_t)(int16_t)lroundf(rxp * 100.0f)); p += 2;
  tl_put16(p, (uint16_t)(int16_t)lroundf(fpp * 100.0f)); p += 2;
  *p++ = TL_CIR_N; *p++ = 0;
  for (int i = 0; i < TL_CIR_N * 2; i++) {
    int32_t v = tl_s18(acc + 1 + 3 * i) >> shift;
    tl_put16(p, (uint16_t)(int16_t)v); p += 2;
  }
  uint16_t crc = tl_crc16(out + 2, (size_t)(p - out - 2));
  tl_put16(p, crc); p += 2;
  tl_cir.frames++;
  return (size_t)(p - out);
}

#endif  // TAGLESS_CIR_H
