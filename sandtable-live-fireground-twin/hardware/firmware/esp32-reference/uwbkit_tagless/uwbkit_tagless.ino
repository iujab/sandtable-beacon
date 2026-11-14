// uwbkit_dw3000.ino -- uwbkit board firmware for the Makerfabs "ESP32 UWB DW3000" (ESP32-WROOM/WROVER + Qorvo
// DW3000 module). Board line protocol v1 (uwb-kit/docs/CONTRACTS.md s.4), DS-TWR ranging, Wi-Fi UDP to the hub.
//
// STATUS: compile-verified only (firmware/build_logs/). Never run on real hardware by its authors.
//
// Library: UWB_DW1000_DW3000 1.0.1 (Arduino library index; `arduino-cli lib install "UWB_DW1000_DW3000@1.0.1"`).
// Only its bundled Qorvo DW3000 C0 device driver (src/qorvo, "DW3000 C0 Device Driver Version 04.00.00": dwt_* API)
// and its SPI port are used; the library's UWB_DW3000 wrapper class is NOT used.
//
// Pins (Makerfabs example sketches and Dw3000/src/dw3000_board_config.h, see firmware/README.md for URLs):
//   SPI SCK 18, MISO 19, MOSI 23 (ESP32 VSPI defaults), CS 4, RST 27, IRQ 34 (IRQ unused: the chip is polled).
//
// Default role/id at flash time: --build-property "compiler.cpp.extra_flags=-DUK_DEFAULT_ID=A1"
// Channel 9 instead of 5: add -DUK_CHANNEL=9 to the same property (all boards must match).

#define UK_CHIP_NAME "DW3000"
#define UK_CHIP_DEFAULT_ANTDLY 16385   // Qorvo DW3000 examples / Makerfabs range_tx.ino TX_ANT_DLY = RX_ANT_DLY = 16385
#ifndef UK_CHANNEL
#define UK_CHANNEL 5
#endif
#if UK_CHANNEL != 5 && UK_CHANNEL != 9
#error "DW3000 supports UWB channel 5 or 9"
#endif

#ifndef UK_PIN_SCK
#define UK_PIN_SCK 18
#endif
#ifndef UK_PIN_MISO
#define UK_PIN_MISO 19
#endif
#ifndef UK_PIN_MOSI
#define UK_PIN_MOSI 23
#endif
#ifndef UK_PIN_CS
#define UK_PIN_CS 4
#endif
#ifndef UK_PIN_RST
#define UK_PIN_RST 27
#endif

#include <SPI.h>
#include <UWB_DW3000.h>            // pulls library UWB_DW1000_DW3000 into the build (header only used for discovery)
#include <qorvo/dw3000.h>          // Qorvo dwt_* driver bundled in that library
#include <DW3000Ng/DW3000Ng.hpp>   // DW3000NgClass::_cs is the CS pin used by the library's SPI port functions
#include "uwbkit_common.h"

// ================= TAGLESS delta (hardware/firmware/make_merged.py) ==========================================
// CIR capture on OVERHEARD ranging frames, before the receiver is re-enabled. Frames addressed to this node are
// left alone so the kit's DS-TWR reply deadlines are never touched.
#include "tagless_cir.h"
static uint8_t tl_frame[TL_FRAME_MAX];
static uint32_t tl_last_ms[48];

static void tagless_on_rx(const uint8_t* f, uint16_t n) {
  if (!tl_cir.on) return;
  UkFrame fr;
  if (!uk_parse_frame(f, n, &fr)) return;
  if (fr.dst[0] == ukc.id[0] && fr.dst[1] == ukc.id[1]) return;
  int tl = uk_id_letter_idx(fr.src[0]), rl = uk_id_letter_idx(ukc.id[0]);     // 0 = A, 1 = T, 2 = N
  if (tl < 0 || rl < 0) return;
  // id byte: low nibble = digit, high nibble = 0 for N, 1 for A, 2 for T (so N1..N9 are plain 1..9)
  uint8_t tx = (uint8_t)((fr.src[1] - '0') | (((tl + 1) % 3) << 4));
  uint8_t rx = (uint8_t)((ukc.id[1] - '0') | (((rl + 1) % 3) << 4));
  uint32_t now = millis();
  if (now - tl_last_ms[tx & 0x2F] < 1000UL / tl_cir.cycle_hz) return;          // at most cycle_hz frames per source
  tl_last_ms[tx & 0x2F] = now;
  size_t len = tl_cir_capture(tx, rx, fr.bseq, tl_frame);
  if (!len) return;
  if ((size_t)Serial.availableForWrite() >= len) Serial.write(tl_frame, len);   // never block the ranging engine
  if (uk_sock >= 0) {
    uint32_t a = uk_hub_addr();
    if (a) {
      struct sockaddr_in to;
      memset(&to, 0, sizeof to);
      to.sin_family = AF_INET;
      to.sin_port = htons(1946);                                               // Tagless engine, CIR-over-UDP
      to.sin_addr.s_addr = a;
      sendto(uk_sock, tl_frame, len, MSG_DONTWAIT, (struct sockaddr*)&to, sizeof to);
    }
  }
}

static bool tagless_command(const char* cmd, const char* a1, const char* a2, const char* a3) {
  if (strcasecmp(cmd, "CIR") && strcasecmp(cmd, "CIR?")) return false;
  if (a1 && !strcasecmp(a1, "ON")) {
    if (a2) tl_cir.cycle_hz = (uint8_t)constrain(atoi(a2), 1, 50);
    uk_kv("cir", "on");
    if (a3 && atol(a3) > 0) { Serial.flush(); Serial.updateBaudRate(atol(a3)); }
    tl_cir.on = true;
    return true;
  }
  if (a1 && !strcasecmp(a1, "OFF")) {
    tl_cir.on = false;
    Serial.flush();
    Serial.updateBaudRate(115200);
  }
  uk_kv("cir", tl_cir.on ? "on" : "off");
  return true;
}
// ================= end of TAGLESS delta ======================================================================

extern SPIClass* _dw3000_spi;                 // library SPI port (qorvo/dw3000_port.cpp), 4 MHz, mode 0
extern dwt_txconfig_t txconfig_options;       // qorvo/dw3000_config_options.cpp: PG delay 0x34, power 0xfdfdfdfd (ch5)
extern dwt_txconfig_t txconfig_options_ch9;   // power 0xfefefefe (ch9)

// Same PHY settings as the Makerfabs/Qorvo DS-TWR examples: 128-symbol preamble, PAC 8, code 9 (64 MHz PRF),
// DW 8-symbol SFD, 6.8 Mb/s, STS off.
static dwt_config_t uk_dwcfg = {
    UK_CHANNEL,       // channel
    DWT_PLEN_128,     // preamble length (TX)
    DWT_PAC8,         // PAC size (RX)
    9,                // TX preamble code
    9,                // RX preamble code
    1,                // SFD type: 1 = DW 8-symbol (as in the Qorvo/Makerfabs examples)
    DWT_BR_6M8,       // data rate
    DWT_PHRMODE_STD,  // PHY header mode
    DWT_PHRRATE_STD,  // PHY header rate
    (129 + 8 - 8),    // SFD timeout = preamble + 1 + SFD length - PAC
    DWT_STS_MODE_OFF, // STS off
    DWT_STS_LEN_64,
    DWT_PDOA_M0};

static uint16_t dw_antdly = UK_CHIP_DEFAULT_ANTDLY;
static uint32_t dw_dx = 0;   // DX_TIME value of the next delayed TX (bits 8..39 of the 40-bit timestamp)

static uint64_t dw_ts5(const uint8_t* b) {
  uint64_t v = 0;
  for (int i = 4; i >= 0; i--) v = (v << 8) | b[i];
  return v;
}

static bool radio_init() {
  pinMode(UK_PIN_CS, OUTPUT);
  digitalWrite(UK_PIN_CS, HIGH);
  SPI.begin(UK_PIN_SCK, UK_PIN_MISO, UK_PIN_MOSI);
  _dw3000_spi = &SPI;
  DW3000NgClass::_cs = UK_PIN_CS;
  DW3000NgClass::_rst = UK_PIN_RST;
  // Hard reset. RSTn is open-drain on the DW3000: drive it low, then release it (never drive it high).
  pinMode(UK_PIN_RST, OUTPUT);
  digitalWrite(UK_PIN_RST, LOW);
  delay(2);
  pinMode(UK_PIN_RST, INPUT);
  delay(10);
  uint32_t dev = dwt_readdevid();   // register 0x00 reads the same way on DW1000 and DW3000
  if ((dev & 0xFFFFFF00UL) == 0xDECA0100UL) {
    uk_log("dw3000: DEV_ID=0x%08lX is a DW1000 (model 01): flash firmware/bin/uwbkit_dw1000 instead",
           (unsigned long)dev);
    return false;
  }
  if ((dev & 0xFFFF0000UL) != 0xDECA0000UL) {
    uk_log("dw3000: DEV_ID=0x%08lX, expected 0xDECA03xx: SPI pins/CS wrong or no DW3000 on this board",
           (unsigned long)dev);
    return false;
  }
  uint32_t t0 = millis();
  while (!dwt_checkidlerc()) {
    if (millis() - t0 > 200) { uk_log("dw3000: never reached IDLE_RC"); return false; }
    delay(1);
  }
  if (dwt_initialise(DWT_DW_INIT) == DWT_ERROR) { uk_log("dw3000: dwt_initialise failed"); return false; }
  if (dwt_configure(&uk_dwcfg) != DWT_SUCCESS) { uk_log("dw3000: dwt_configure failed (PLL/RX cal)"); return false; }
  dwt_configuretxrf(UK_CHANNEL == 9 ? &txconfig_options_ch9 : &txconfig_options);
  dwt_setlnapamode(DWT_LNA_ENABLE | DWT_PA_ENABLE);   // as in the Makerfabs examples (EXTTXE/EXTRXE on GPIO5/6)
  dwt_configciadiag(DW_CIA_DIAG_LOG_ALL);             // keep full RX diagnostics for rssi / first-path power
  dwt_setrxtimeout(0);                                // no chip timeout: the engine times every wait in software
  dwt_setrxaftertxdelay(0);
  uk_log("dw3000: DEV_ID=0x%08lX configured ch%d 6.8M PRF64 code9 plen128", (unsigned long)dev, UK_CHANNEL);
  return true;
}

static uint8_t radio_poll() {
  uint32_t s = dwt_read32bitreg(SYS_STATUS_ID);
  uint8_t ev = 0;
  uint32_t clr = 0;
  if (s & SYS_STATUS_TXFRS_BIT_MASK) { ev |= UK_EV_TX; clr |= SYS_STATUS_ALL_TX; }
  if (s & SYS_STATUS_RXFCG_BIT_MASK) { ev |= UK_EV_RX; clr |= SYS_STATUS_ALL_RX_GOOD; }
  if (s & (SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO)) {
    ev |= UK_EV_ERR;
    clr |= SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO;
  }
  if (clr) dwt_write32bitreg(SYS_STATUS_ID, clr);
  return ev;
}

static uint16_t radio_read(uint8_t* buf, uint16_t cap) {
  uint16_t len = (uint16_t)(dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_BIT_MASK);
  if (len <= 2) return 0;
  len -= 2;   // FCS
  if (len > cap) len = cap;
  dwt_readrxdata(buf, len, 0);
  return len;
}

static uint64_t radio_rx_ts() {
  uint8_t b[5];
  dwt_readrxtimestamp(b);
  return dw_ts5(b);
}

static uint64_t radio_tx_ts() {
  uint8_t b[5];
  dwt_readtxtimestamp(b);
  return dw_ts5(b);
}

static void radio_rx_on() {
  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
  dwt_rxenable(DWT_START_RX_IMMEDIATE);
}

// Qorvo ex_05a/ex_05b: tx_time = (ref + dly * UUS_TO_DWT_TIME) >> 8; actual TX timestamp
// = ((tx_time & 0xFFFFFFFE) << 8) + TX antenna delay (the chip ignores the low bit of DX_TIME).
static uint64_t radio_plan(uint64_t ref_ts, uint32_t dly_uus) {
  dw_dx = (uint32_t)((ref_ts + (uint64_t)dly_uus * UK_UUS_TO_TICKS) >> 8);
  return ((((uint64_t)(dw_dx & 0xFFFFFFFEUL)) << 8) + dw_antdly) & UK_MASK40;
}

static bool radio_send(uint8_t* f, uint16_t len, bool delayed, bool ranging) {
  dwt_forcetrxoff();
  // also clear a stale half-period-delay warning: dwt_starttx() reads that bit to decide "late"
  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX | SYS_STATUS_HPDWARN_BIT_MASK);
  dwt_writetxdata((uint16_t)(len + 2), f, 0);   // length includes the 2 FCS bytes (buffer has room)
  dwt_writetxfctrl((uint16_t)(len + 2), 0, ranging ? 1 : 0);
  if (delayed) {
    dwt_setdelayedtrxtime(dw_dx);
    return dwt_starttx(DWT_START_TX_DELAYED) == DWT_SUCCESS;   // DWT_ERROR when already too late (HPDWARN)
  }
  return dwt_starttx(DWT_START_TX_IMMEDIATE) == DWT_SUCCESS;
}

static void radio_set_antdly(uint16_t v) {
  dw_antdly = v;
  dwt_setrxantennadelay(v);
  dwt_settxantennadelay(v);
}

// DW3000 User Manual s.4.7.1 / 4.7.2 (formulas as used on the Qorvo forum, see README):
//   RX level   = 10 log10(C * 2^21 / N^2) + 6 D - A
//   First path = 10 log10((F1^2 + F2^2 + F3^2) / N^2) + 6 D - A
// C = Ipatov channel power, N = Ipatov accumulation count, F1..F3 first-path amplitudes (22-bit, 2 fractional bits,
// hence /4), D = DGC_DECISION (reg 0x3:0x60 bits 28..30), A = 121.7 for 64 MHz PRF (code 9).
static void radio_diag(float* rssi, float* fp) {
  dwt_rxdiag_t dg;
  memset(&dg, 0, sizeof dg);
  dwt_readdiagnostics(&dg);
  float N = (float)dg.ipatovAccumCount;
  if (N <= 0.0f || dg.ipatovPower == 0) { *rssi = NAN; *fp = NAN; return; }
  uint8_t dgc = (uint8_t)((dwt_read8bitoffsetreg(0x30060, 3) >> 4) & 0x7);
  const float A = 121.7f;
  *rssi = 10.0f * log10f((float)dg.ipatovPower * 2097152.0f / (N * N)) + 6.0f * dgc - A;
#ifndef UK_DW3000_FP_NODIV4
  float f1 = dg.ipatovF1 / 4.0f, f2 = dg.ipatovF2 / 4.0f, f3 = dg.ipatovF3 / 4.0f;
#else
  float f1 = (float)dg.ipatovF1, f2 = (float)dg.ipatovF2, f3 = (float)dg.ipatovF3;
#endif
  float s = f1 * f1 + f2 * f2 + f3 * f3;
  *fp = s > 0.0f ? 10.0f * log10f(s / (N * N)) + 6.0f * dgc - A : NAN;
}

void setup() {
  uk_setup_common();
  uk_engine_begin();
  uk_banner();
}

void loop() {
  uk_service();
  uk_engine_step();
}
