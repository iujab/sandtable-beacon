// uwbkit_common.h -- shared logic of the uwbkit board firmware (board line protocol v1, docs/CONTRACTS.md s.4)
//
// CANONICAL COPY: firmware/common/uwbkit_common.h
// Each sketch folder (uwbkit_dw3000/, uwbkit_dw1000/, uwbkit_mauwb_bridge/) holds a byte-identical copy so that
// every folder builds standalone with arduino-cli. Edit THIS file, then run `python3 firmware/tools/build_all.py`
// (it re-copies the header into every sketch folder before compiling) or copy it by hand.
//
// Part 1  pure C++ (no Arduino; also compiled natively by firmware/host_test): checksum, line formatting,
//         DS-TWR maths, over-the-air frame packing, node-id helpers.
// Part 2  Arduino / ESP32: config in NVS, USB-serial + Wi-Fi UDP output, command parser, S/banner/button.
// Part 3  DS-TWR ranging engine (only when the sketch does not define UK_NO_ENGINE). It runs on a tiny polled
//         radio HAL (radio_*) that each chip sketch implements. Polled, not interrupt driven: the DW IRQ line
//         is not used, which removes "IRQ never fires" as a failure mode.
//
// Nothing here has run on real UWB hardware. It is compile-verified (see firmware/build_logs/) and the pure part is
// unit-checked on the host (firmware/host_test/).

#ifndef UWBKIT_COMMON_H
#define UWBKIT_COMMON_H

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

#define UK_FW_VERSION "0.3.0"
#define UK_LINE_CAP 168          // protocol limit is 160 chars per line

// =====================================================================================================
// Part 1 -- pure C++
// =====================================================================================================

// NMEA-style checksum: XOR of every byte before the '*'.
static inline uint8_t uk_xor(const char* s, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; i++) c ^= (uint8_t)s[i];
  return c;
}

// Terminate a line body in buf[0..len): appends "*HH\n" (with_cs) or "\n". Returns the new length.
static size_t uk_seal(char* buf, size_t len, size_t cap, bool with_cs) {
  if (len + 5 > cap) len = cap - 5;
  if (with_cs) {
    uint8_t c = uk_xor(buf, len);
    static const char hx[] = "0123456789ABCDEF";
    buf[len++] = '*';
    buf[len++] = hx[c >> 4];
    buf[len++] = hx[c & 15];
  }
  buf[len++] = '\n';
  buf[len] = 0;
  return len;
}

// LOS confidence from total RX power minus first-path power (contract: < 6 dB -> ~1.0, > 10 dB -> ~0.0).
static inline float uk_los(float rssi, float fp) {
  float diff = rssi - fp;
  float v = (10.0f - diff) / 4.0f;
  if (v < 0.0f) v = 0.0f;
  if (v > 1.0f) v = 1.0f;
  return v;
}

// R,<bseq>,<a>,<b>,<d_m>[,<raw_m>[,<rssi_dbm>[,<fp_dbm>[,<los>]]]]   (body only; seal with uk_seal)
static size_t uk_fmt_range(char* buf, size_t cap, unsigned bseq, const char* a, const char* b, float d, float raw,
                           float rssi, float fp) {
  int n;
  if (isnan(rssi)) {
    n = snprintf(buf, cap, "R,%u,%s,%s,%.3f,%.3f", bseq & 0xFFFFu, a, b, (double)d, (double)raw);
  } else if (isnan(fp)) {   // e.g. MaUWB AT firmware: RSSI only, no first-path power -> no los either
    n = snprintf(buf, cap, "R,%u,%s,%s,%.3f,%.3f,%.1f", bseq & 0xFFFFu, a, b, (double)d, (double)raw, (double)rssi);
  } else {
    n = snprintf(buf, cap, "R,%u,%s,%s,%.3f,%.3f,%.1f,%.1f,%.2f", bseq & 0xFFFFu, a, b, (double)d, (double)raw,
                 (double)rssi, (double)fp, (double)uk_los(rssi, fp));
  }
  return (n < 0) ? 0 : ((size_t)n >= cap ? cap - 1 : (size_t)n);
}

// S,<id>,<role>,<uptime_s>,<vbat_v>,<rate_hz>,<ok_count>,<fail_count>   (vbat empty when NAN)
static size_t uk_fmt_status(char* buf, size_t cap, const char* id, const char* role, unsigned long uptime_s,
                            float vbat, float rate_hz, unsigned long ok, unsigned long fail) {
  char vb[12] = "";
  if (!isnan(vbat)) snprintf(vb, sizeof vb, "%.2f", (double)vbat);
  int n = snprintf(buf, cap, "S,%s,%s,%lu,%s,%.1f,%lu,%lu", id, role, uptime_s, vb, (double)rate_hz, ok, fail);
  return (n < 0) ? 0 : ((size_t)n >= cap ? cap - 1 : (size_t)n);
}

// K,<id>,<key>,<value>
static size_t uk_fmt_kv(char* buf, size_t cap, const char* id, const char* key, const char* value) {
  int n = snprintf(buf, cap, "K,%s,%s,%s", id, key, value);
  return (n < 0) ? 0 : ((size_t)n >= cap ? cap - 1 : (size_t)n);
}

// E,<id>,<name>[,<k=v>...]
static size_t uk_fmt_event(char* buf, size_t cap, const char* id, const char* name, const char* kvs) {
  int n = (kvs && kvs[0]) ? snprintf(buf, cap, "E,%s,%s,%s", id, name, kvs) : snprintf(buf, cap, "E,%s,%s", id, name);
  return (n < 0) ? 0 : ((size_t)n >= cap ? cap - 1 : (size_t)n);
}

// #UWBKIT fw=.. chip=.. role=.. id=.. mac=.. ch=.. antdly=.. [ip=..]   (sent without checksum, like the host's
// format_banner default; the parser accepts both)
static size_t uk_fmt_banner(char* buf, size_t cap, const char* chip, const char* role, const char* id,
                            const char* mac12, int ch, unsigned antdly, const char* ip) {
  int n;
  if (ip && ip[0])
    n = snprintf(buf, cap, "#UWBKIT fw=%s chip=%s role=%s id=%s mac=%s ch=%d antdly=%u ip=%s", UK_FW_VERSION, chip,
                 role, id, mac12, ch, antdly, ip);
  else
    n = snprintf(buf, cap, "#UWBKIT fw=%s chip=%s role=%s id=%s mac=%s ch=%d antdly=%u", UK_FW_VERSION, chip, role,
                 id, mac12, ch, antdly);
  return (n < 0) ? 0 : ((size_t)n >= cap ? cap - 1 : (size_t)n);
}

// ---- node ids: one letter (A anchor, T tag, N mesh) + one digit 1..9 ------------------------------------------
enum { UK_ROLE_ANCHOR = 0, UK_ROLE_TAG = 1, UK_ROLE_MESH = 2 };
static const char* const UK_ROLE_NAMES[3] = {"anchor", "tag", "mesh"};
static const char UK_ID_LETTERS[3] = {'A', 'T', 'N'};
#define UK_NIDS 27  // 3 letters x 9 digits

static inline int uk_role_from_name(const char* s) {
  for (int i = 0; i < 3; i++) {
    const char* r = UK_ROLE_NAMES[i];
    size_t k = 0;
    while (r[k] && s[k] && ((s[k] | 0x20) == r[k])) k++;
    if (!r[k] && !s[k]) return i;
  }
  return -1;
}
static inline int uk_id_letter_idx(char c) {
  c = (char)(c & ~0x20);  // upper case
  for (int i = 0; i < 3; i++)
    if (UK_ID_LETTERS[i] == c) return i;
  return -1;
}
static inline bool uk_valid_id(const char* s) {
  return s && uk_id_letter_idx(s[0]) >= 0 && s[1] >= '1' && s[1] <= '9' && s[2] == 0;
}
// 0..26, or -1
static inline int uk_id_index(const char* s) {
  if (!s || uk_id_letter_idx(s[0]) < 0 || s[1] < '1' || s[1] > '9') return -1;
  return uk_id_letter_idx(s[0]) * 9 + (s[1] - '1');
}
static inline void uk_id_from_index(int idx, char out[3]) {
  out[0] = UK_ID_LETTERS[idx / 9];
  out[1] = (char)('1' + idx % 9);
  out[2] = 0;
}

// ---- DS-TWR maths (asymmetric double-sided TWR, Qorvo ex_05a/ex_05b, DW1000 UM s.12.3.2) ---------------------
#define UK_MASK40 0xFFFFFFFFFFULL
#define UK_TICK_S (1.0 / 499.2e6 / 128.0)        // DW1000/DW3000 time unit, ~15.65 ps
#define UK_C_AIR 299702547.0                     // m/s, same value as Qorvo's SPEED_OF_LIGHT
#define UK_M_PER_TICK (UK_TICK_S * UK_C_AIR)     // ~4.69 mm per tick
#define UK_UUS_TO_TICKS 63898ULL                 // 1 "UWB microsecond" (512/499.2 us) in ticks (Qorvo UUS_TO_DWT_TIME)

static inline int64_t uk_d40(uint64_t later, uint64_t earlier) { return (int64_t)((later - earlier) & UK_MASK40); }

// Initiator timestamps (poll_tx, resp_rx, final_tx) + responder timestamps (poll_rx, resp_tx, final_rx) -> metres.
//   Ra = resp_rx - poll_tx   Rb = final_rx - resp_tx   Da = final_tx - resp_rx   Db = resp_tx - poll_rx
//   ToF = (Ra*Rb - Da*Db) / (Ra + Rb + Da + Db)
// Integer products are exact in int64 for intervals < 2^31 ticks (33 ms); larger intervals are rejected (NAN).
static double uk_dstwr_m(uint64_t poll_tx, uint64_t resp_rx, uint64_t final_tx, uint64_t poll_rx, uint64_t resp_tx,
                         uint64_t final_rx) {
  int64_t Ra = uk_d40(resp_rx, poll_tx), Rb = uk_d40(final_rx, resp_tx);
  int64_t Da = uk_d40(final_tx, resp_rx), Db = uk_d40(resp_tx, poll_rx);
  const int64_t lim = (int64_t)1 << 31;
  if (Ra >= lim || Rb >= lim || Da >= lim || Db >= lim) return NAN;
  int64_t den = Ra + Rb + Da + Db;
  if (den <= 0) return NAN;
  double tof = (double)(Ra * Rb - Da * Db) / (double)den;
  return tof * UK_M_PER_TICK;
}

// ---- over-the-air frames (IEEE 802.15.4 data frame header, 16-bit addresses, PAN id compression) ---------------
//  0 0x41 1 0x88   frame control           2 seq8          3-4 PAN id 'U''K'
//  5-6 dst id (2 ASCII, "**" = broadcast)  7-8 src id (2 ASCII)    9 type    10-11 bseq (LE)    12.. payload
enum {
  UK_F_POLL = 'P',    // initiator -> responder, no payload
  UK_F_RESP = 'A',    // responder -> initiator, no payload (sent at a scheduled time)
  UK_F_FINAL = 'F',   // initiator -> responder: poll_tx(5) resp_rx(5) final_tx(5)
  UK_F_RESULT = 'X',  // responder -> initiator (overheard by everyone): d_mm(i32) rssi_dBm*10(i16) fp_dBm*10(i16)
  UK_F_TOKEN = 'T',   // broadcast "my mesh/survey turn is over"
  UK_F_CMD = 'C'      // broadcast ASCII command for the other boards (used for SURVEY <s>)
};
#define UK_HDR_LEN 12
#define UK_PAN0 'U'
#define UK_PAN1 'K'

struct UkFrame {
  uint8_t type;
  char dst[3];
  char src[3];
  uint16_t bseq;
  const uint8_t* pl;
  uint16_t pl_len;
};

static inline void uk_put40(uint8_t* p, uint64_t v) {
  for (int i = 0; i < 5; i++) p[i] = (uint8_t)(v >> (8 * i));
}
static inline uint64_t uk_get40(const uint8_t* p) {
  uint64_t v = 0;
  for (int i = 0; i < 5; i++) v |= (uint64_t)p[i] << (8 * i);
  return v;
}
static inline void uk_put32(uint8_t* p, int32_t v) {
  for (int i = 0; i < 4; i++) p[i] = (uint8_t)((uint32_t)v >> (8 * i));
}
static inline int32_t uk_get32(const uint8_t* p) {
  return (int32_t)((uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
}
static inline void uk_put16(uint8_t* p, int16_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)((uint16_t)v >> 8);
}
static inline int16_t uk_get16(const uint8_t* p) { return (int16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8)); }

static inline uint16_t uk_hdr(uint8_t* f, uint8_t seq8, const char* dst, const char* src, uint8_t type,
                              uint16_t bseq) {
  f[0] = 0x41; f[1] = 0x88; f[2] = seq8; f[3] = UK_PAN0; f[4] = UK_PAN1;
  f[5] = (uint8_t)dst[0]; f[6] = (uint8_t)dst[1];
  f[7] = (uint8_t)src[0]; f[8] = (uint8_t)src[1];
  f[9] = type;
  f[10] = (uint8_t)bseq; f[11] = (uint8_t)(bseq >> 8);
  return UK_HDR_LEN;
}

static inline bool uk_parse_frame(const uint8_t* f, uint16_t len, UkFrame* o) {
  if (len < UK_HDR_LEN || f[0] != 0x41 || f[1] != 0x88 || f[3] != UK_PAN0 || f[4] != UK_PAN1) return false;
  o->dst[0] = (char)f[5]; o->dst[1] = (char)f[6]; o->dst[2] = 0;
  o->src[0] = (char)f[7]; o->src[1] = (char)f[8]; o->src[2] = 0;
  o->type = f[9];
  o->bseq = (uint16_t)(f[10] | (f[11] << 8));
  o->pl = f + UK_HDR_LEN;
  o->pl_len = (uint16_t)(len - UK_HDR_LEN);
  return uk_valid_id(o->src);
}

#ifndef UK_HOST_TEST
// =====================================================================================================
// Part 2 -- Arduino / ESP32: config, I/O, commands
// =====================================================================================================
#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <lwip/sockets.h>
#include <lwip/inet.h>
#include <fcntl.h>

#define UK_XSTR(x) UK_STR(x)
#define UK_STR(x) #x

// ---- compile-time defaults (override with: --build-property "compiler.cpp.extra_flags=-DUK_DEFAULT_ID=A2") ------
#ifdef UK_DEFAULT_ID
#define UK_DEFAULT_ID_STR UK_XSTR(UK_DEFAULT_ID)
#else
#define UK_DEFAULT_ID_STR "T1"
#endif
static_assert(sizeof(UK_DEFAULT_ID_STR) == 3, "UK_DEFAULT_ID must be a letter A/T/N plus one digit 1-9, e.g. A2");
static_assert(UK_DEFAULT_ID_STR[0] == 'A' || UK_DEFAULT_ID_STR[0] == 'T' || UK_DEFAULT_ID_STR[0] == 'N',
              "UK_DEFAULT_ID must start with A, T or N");
#ifdef UK_DEFAULT_ROLE
#define UK_DEFAULT_ROLE_STR UK_XSTR(UK_DEFAULT_ROLE)   // anchor | tag | mesh
#else
#define UK_DEFAULT_ROLE_STR ""                          // derived from the id letter
#endif
#ifndef UK_WIFI_SSID
#define UK_WIFI_SSID ""            // compile-time Wi-Fi default; edit here or use the WIFI command (persisted)
#endif
#ifndef UK_WIFI_PASS
#define UK_WIFI_PASS ""
#endif
#ifndef UK_HUB_IP
#define UK_HUB_IP ""               // empty: send to the subnet broadcast address until HUB is set
#endif
#ifndef UK_HUB_PORT
#define UK_HUB_PORT 1801
#endif
#ifndef UK_LOCAL_UDP_PORT
#define UK_LOCAL_UDP_PORT 1801     // the board sends from and listens for commands on this port
#endif
#ifndef UK_RATE_HZ
#define UK_RATE_HZ 10
#endif
#ifndef UK_BUTTON_PIN
#define UK_BUTTON_PIN 0            // BOOT button on ESP32 and ESP32-S3 boards; -1 disables
#endif
#ifndef UK_VBAT_PIN
#define UK_VBAT_PIN -1             // ADC pin of a battery divider; -1 = none (vbat field left empty)
#endif
#ifndef UK_VBAT_DIV
#define UK_VBAT_DIV 2.0f
#endif
#ifndef UK_CHIP_NAME
#define UK_CHIP_NAME "UNKNOWN"
#endif
#ifndef UK_CHIP_DEFAULT_ANTDLY
#define UK_CHIP_DEFAULT_ANTDLY 16385
#endif
#ifndef UK_CHANNEL
#define UK_CHANNEL 5
#endif

struct UkConfig {
  uint8_t role;
  char id[3];
  uint16_t antdly;
  char ssid[33];
  char pass[65];
  char hub[16];
  uint16_t hub_port;
  uint8_t rate_hz;
  uint8_t relay;
};
static UkConfig ukc;

static unsigned long uk_ok = 0, uk_fail = 0;      // exchanges completed / failed on this board
static unsigned long uk_rate_count = 0;           // own results in the current second
static float uk_rate_hz = 0.0f;
static char uk_mac[13] = "000000000000";
static char uk_ip[16] = "";
static int uk_sock = -1;
static uint32_t uk_hub_learned = 0;               // hub address learnt from a command datagram (network order)
static char uk_udpq[1200];
static size_t uk_udpq_len = 0;
static unsigned long uk_udp_dropped = 0;

// hooks implemented by Part 3 (engine) or by the bridge sketch
static void uk_on_config_changed();
static void uk_on_antdly(uint16_t v);
static void uk_on_cal(const char* peer, float true_m, int n);
static void uk_on_survey(int seconds);
static bool uk_engine_busy();
#ifdef UK_HAVE_CMD_EXT
static bool uk_command_ext(char* line);   // return true when the sketch handled the line
#endif

// ---- output -------------------------------------------------------------------------------------------------
static void uk_out_raw(const char* line, size_t n) {
  Serial.write((const uint8_t*)line, n);
  if (uk_sock >= 0) {
    if (uk_udpq_len + n <= sizeof(uk_udpq)) {
      memcpy(uk_udpq + uk_udpq_len, line, n);
      uk_udpq_len += n;
    } else {
      uk_udp_dropped++;
    }
  }
}
static void uk_emit(char* buf, size_t len, bool with_cs) { uk_out_raw(buf, uk_seal(buf, len, UK_LINE_CAP, with_cs)); }

static void uk_log(const char* fmt, ...) {
  char b[UK_LINE_CAP];
  b[0] = '#'; b[1] = ' ';
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(b + 2, sizeof(b) - 8, fmt, ap);
  va_end(ap);
  if (n < 0) n = 0;
  size_t len = 2 + ((size_t)n > sizeof(b) - 9 ? sizeof(b) - 9 : (size_t)n);
  uk_emit(b, len, false);
}

static void uk_kv(const char* key, const char* value) {
  char b[UK_LINE_CAP];
  uk_emit(b, uk_fmt_kv(b, sizeof b, ukc.id, key, value), true);
}
static void uk_kv_int(const char* key, long v) {
  char s[16];
  snprintf(s, sizeof s, "%ld", v);
  uk_kv(key, s);
}

// Report one ranging result. own=true when this board took part in the exchange (counts towards ok/rate).
static void uk_report_range(unsigned bseq, const char* a, const char* b, float d, float rssi, float fp, bool own) {
  if (isnan(d) || d < 0.0f || d > 100.0f) {   // sanity limits
    if (own) uk_fail++;
    return;
  }
  if (own) { uk_ok++; uk_rate_count++; }
  char buf[UK_LINE_CAP];
  // raw == d: the firmware applies no bias correction beyond the antenna delay
  uk_emit(buf, uk_fmt_range(buf, sizeof buf, bseq, a, b, d, d, rssi, fp), true);
}

static void uk_banner() {
  char b[UK_LINE_CAP];
  uk_emit(b, uk_fmt_banner(b, sizeof b, UK_CHIP_NAME, UK_ROLE_NAMES[ukc.role], ukc.id, uk_mac, UK_CHANNEL, ukc.antdly,
                           uk_ip), false);
}

// ---- config / NVS ------------------------------------------------------------------------------------------
static void uk_set_id(const char* id) {
  ukc.id[0] = (char)(id[0] & ~0x20);
  ukc.id[1] = id[1];
  ukc.id[2] = 0;
}
static void uk_config_defaults() {
  memset(&ukc, 0, sizeof ukc);
  uk_set_id(UK_DEFAULT_ID_STR);
  int r = uk_role_from_name(UK_DEFAULT_ROLE_STR);
  ukc.role = (uint8_t)(r >= 0 ? r : uk_id_letter_idx(ukc.id[0]));
  ukc.antdly = UK_CHIP_DEFAULT_ANTDLY;
  strncpy(ukc.ssid, UK_WIFI_SSID, sizeof(ukc.ssid) - 1);
  strncpy(ukc.pass, UK_WIFI_PASS, sizeof(ukc.pass) - 1);
  strncpy(ukc.hub, UK_HUB_IP, sizeof(ukc.hub) - 1);
  ukc.hub_port = UK_HUB_PORT;
  ukc.rate_hz = UK_RATE_HZ;
  ukc.relay = 1;
}
static void uk_config_load() {
  uk_config_defaults();
  Preferences p;
  if (!p.begin("uwbkit", true)) return;  // namespace absent on a fresh board: keep defaults
  if (p.isKey("id")) {
    char t[4] = "";
    p.getString("id", t, sizeof t);
    if (uk_valid_id(t)) uk_set_id(t);
  }
  if (p.isKey("role")) { uint8_t r = p.getUChar("role", ukc.role); if (r < 3) ukc.role = r; }
  if (p.isKey("antdly")) ukc.antdly = p.getUShort("antdly", ukc.antdly);
  if (p.isKey("ssid")) p.getString("ssid", ukc.ssid, sizeof ukc.ssid);
  if (p.isKey("pass")) p.getString("pass", ukc.pass, sizeof ukc.pass);
  if (p.isKey("hub")) p.getString("hub", ukc.hub, sizeof ukc.hub);
  if (p.isKey("hubport")) ukc.hub_port = p.getUShort("hubport", ukc.hub_port);
  if (p.isKey("rate")) { uint8_t r = p.getUChar("rate", ukc.rate_hz); if (r >= 1 && r <= 50) ukc.rate_hz = r; }
  if (p.isKey("relay")) ukc.relay = p.getUChar("relay", 1);
  p.end();
}
static bool uk_config_save() {
  Preferences p;
  if (!p.begin("uwbkit", false)) return false;
  p.putString("id", ukc.id);
  p.putUChar("role", ukc.role);
  p.putUShort("antdly", ukc.antdly);
  p.putString("ssid", ukc.ssid);
  p.putString("pass", ukc.pass);
  p.putString("hub", ukc.hub);
  p.putUShort("hubport", ukc.hub_port);
  p.putUChar("rate", ukc.rate_hz);
  p.putUChar("relay", ukc.relay);
  p.end();
  return true;
}

// ---- Wi-Fi + UDP (raw lwIP socket: non-blocking, no heap use per datagram) -------------------------------------
static bool uk_wifi_wanted() { return ukc.ssid[0] != 0; }
static void uk_wifi_start() {
  if (!uk_wifi_wanted()) return;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ukc.ssid, ukc.pass);   // returns immediately; connection happens in the background
  uk_log("wifi connecting to '%s' (ranging does not wait for it)", ukc.ssid);
}
static void uk_sock_open() {
  int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (s < 0) return;
  int yes = 1;
  setsockopt(s, SOL_SOCKET, SO_BROADCAST, &yes, sizeof yes);
  struct sockaddr_in a;
  memset(&a, 0, sizeof a);
  a.sin_family = AF_INET;
  a.sin_port = htons(UK_LOCAL_UDP_PORT);
  a.sin_addr.s_addr = htonl(INADDR_ANY);
  if (bind(s, (struct sockaddr*)&a, sizeof a) < 0) { close(s); return; }
  int fl = fcntl(s, F_GETFL, 0);
  fcntl(s, F_SETFL, fl | O_NONBLOCK);
  uk_sock = s;
}
static uint32_t uk_hub_addr() {   // network byte order, 0 = none
  if (ukc.hub[0]) {
    uint32_t a = inet_addr(ukc.hub);
    if (a != (uint32_t)0xFFFFFFFFu || strcmp(ukc.hub, "255.255.255.255") == 0) return a;
  }
  if (uk_hub_learned) return uk_hub_learned;
  IPAddress b = WiFi.broadcastIP();
  return (uint32_t)b;
}
static void uk_udp_flush() {
  if (!uk_udpq_len) return;
  if (uk_sock < 0 || WiFi.status() != WL_CONNECTED) { uk_udpq_len = 0; return; }
  struct sockaddr_in to;
  memset(&to, 0, sizeof to);
  to.sin_family = AF_INET;
  to.sin_port = htons(ukc.hub_port);
  to.sin_addr.s_addr = uk_hub_addr();
  if (to.sin_addr.s_addr) sendto(uk_sock, uk_udpq, uk_udpq_len, MSG_DONTWAIT, (struct sockaddr*)&to, sizeof to);
  uk_udpq_len = 0;
}
static void uk_wifi_poll() {
  if (!uk_wifi_wanted()) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !uk_ip[0]) {
    IPAddress ip = WiFi.localIP();
    snprintf(uk_ip, sizeof uk_ip, "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    if (uk_sock < 0) uk_sock_open();
    uk_log("wifi up ip=%s udp=%s hub=%s:%u", uk_ip, uk_sock >= 0 ? "ok" : "FAILED",
           ukc.hub[0] ? ukc.hub : "broadcast", ukc.hub_port);
    uk_banner();
  } else if (!up && uk_ip[0]) {
    uk_ip[0] = 0;
    uk_log("wifi down (auto-reconnect running)");
  }
}

// ---- commands -----------------------------------------------------------------------------------------------
static void uk_reply_config() {
  uk_kv("role", UK_ROLE_NAMES[ukc.role]);
  uk_kv("id", ukc.id);
  uk_kv_int("antdly", ukc.antdly);
  uk_kv_int("rate", ukc.rate_hz);
  uk_kv("wifi", ukc.ssid[0] ? ukc.ssid : "-");
  char hp[32];
  snprintf(hp, sizeof hp, "%s:%u", ukc.hub[0] ? ukc.hub : "broadcast", ukc.hub_port);
  uk_kv("hub", hp);
  uk_kv_int("relay", ukc.relay);
}

static bool tagless_command(const char* cmd, const char* a1, const char* a2, const char* a3);   // TAGLESS
static void uk_command(char* line) {
  // trim
  while (*line == ' ' || *line == '\t') line++;
  size_t n = strlen(line);
  while (n && (line[n - 1] == ' ' || line[n - 1] == '\r' || line[n - 1] == '\n' || line[n - 1] == '\t')) line[--n] = 0;
  if (!n) return;
#ifdef UK_HAVE_CMD_EXT
  if (uk_command_ext(line)) return;   // sketch-specific commands (e.g. the MaUWB bridge's AT passthrough)
#endif
  char* save = nullptr;
  char* cmd = strtok_r(line, " \t", &save);
  char* a1 = strtok_r(nullptr, " \t", &save);
  char* a2 = strtok_r(nullptr, " \t", &save);
  char* a3 = strtok_r(nullptr, " \t", &save);
  if (!cmd) return;

  if (tagless_command(cmd, a1, a2, a3)) return;   // TAGLESS: CIR ON|OFF|?
  if (!strcmp(cmd, "?")) { uk_banner(); return; }
  if (!strcasecmp(cmd, "ROLE") && a1) {
    int r = uk_role_from_name(a1);
    if (r < 0) { uk_kv("error", "role must be anchor|tag|mesh"); return; }
    ukc.role = (uint8_t)r;
    uk_on_config_changed();
    uk_kv("role", UK_ROLE_NAMES[ukc.role]);
    return;
  }
  if (!strcasecmp(cmd, "ID") && a1) {
    if (!uk_valid_id(a1)) {
      uk_kv("error", "id must be A1..A9 / T1..T9 / N1..N9");
      return;
    }
    uk_set_id(a1);
    ukc.role = (uint8_t)uk_id_letter_idx(ukc.id[0]);  // ID A2 also makes the board an anchor (ROLE can override)
    uk_on_config_changed();
    uk_kv("id", ukc.id);
    uk_kv("role", UK_ROLE_NAMES[ukc.role]);
    return;
  }
  if (!strcasecmp(cmd, "RATE") && a1) {
    int r = atoi(a1);
    if (r < 1 || r > 50) { uk_kv("error", "rate must be 1..50"); return; }
    ukc.rate_hz = (uint8_t)r;
    uk_kv_int("rate", ukc.rate_hz);
    return;
  }
  if (!strcasecmp(cmd, "SAVE")) { uk_kv_int("saved", uk_config_save() ? 1 : 0); return; }
  if (!strcasecmp(cmd, "REBOOT")) {
    uk_kv_int("reboot", 1);
    uk_udp_flush();
    Serial.flush();
    delay(50);
    ESP.restart();
    return;
  }
  if (!strcasecmp(cmd, "ANTDLY?")) { uk_kv_int("antdly", ukc.antdly); return; }
  if (!strcasecmp(cmd, "ANTDLY") && a1) {
    if (a1[0] == '?') { uk_kv_int("antdly", ukc.antdly); return; }
    long v = atol(a1);
    if (v < 0 || v > 65535) { uk_kv("error", "antdly must be 0..65535"); return; }
    ukc.antdly = (uint16_t)v;
    uk_on_antdly(ukc.antdly);
    uk_kv_int("antdly", ukc.antdly);
    return;
  }
  if (!strcasecmp(cmd, "CAL") && a1 && a2) {
    if (!uk_valid_id(a1)) { uk_kv("error", "CAL <peer_id> <true_m> [n]"); return; }
    char peer[3] = {(char)(a1[0] & ~0x20), a1[1], 0};
    float tm = (float)atof(a2);
    int cnt = a3 ? atoi(a3) : 20;
    if (tm < 0.2f || tm > 50.0f) { uk_kv("error", "true_m must be 0.2..50"); return; }
    if (cnt < 5) cnt = 5;
    if (cnt > 200) cnt = 200;
    uk_on_cal(peer, tm, cnt);
    return;
  }
  if (!strcasecmp(cmd, "SURVEY") && a1) {
    int s = atoi(a1);
    if (s < 0) s = 0;
    if (s > 600) s = 600;
    uk_on_survey(s);
    return;
  }
  if (!strcasecmp(cmd, "WIFI") && a1) {
    if (!strcmp(a1, "-") || !strcasecmp(a1, "OFF")) {
      ukc.ssid[0] = 0; ukc.pass[0] = 0;
      WiFi.disconnect(true);
      uk_ip[0] = 0;
      uk_kv("wifi", "-");
      return;
    }
    strncpy(ukc.ssid, a1, sizeof(ukc.ssid) - 1); ukc.ssid[sizeof(ukc.ssid) - 1] = 0;
    strncpy(ukc.pass, a2 ? a2 : "", sizeof(ukc.pass) - 1); ukc.pass[sizeof(ukc.pass) - 1] = 0;
    WiFi.disconnect();
    uk_ip[0] = 0;
    uk_wifi_start();
    uk_kv("wifi", ukc.ssid);
    return;
  }
  if (!strcasecmp(cmd, "HUB") && a1) {
    if (!strcmp(a1, "-")) ukc.hub[0] = 0;
    else { strncpy(ukc.hub, a1, sizeof(ukc.hub) - 1); ukc.hub[sizeof(ukc.hub) - 1] = 0; }
    if (a2) { long p = atol(a2); if (p > 0 && p < 65536) ukc.hub_port = (uint16_t)p; }
    char hp[32];
    snprintf(hp, sizeof hp, "%s:%u", ukc.hub[0] ? ukc.hub : "broadcast", ukc.hub_port);
    uk_kv("hub", hp);
    return;
  }
  if (!strcasecmp(cmd, "RELAY") && a1) { ukc.relay = atoi(a1) ? 1 : 0; uk_kv_int("relay", ukc.relay); return; }
  if (!strcasecmp(cmd, "CONFIG?")) { uk_reply_config(); return; }
  uk_kv("error", "unknown command");
}

// ---- service loop pieces ------------------------------------------------------------------------------------
static char uk_ser_line[128];
static size_t uk_ser_len = 0;

static void uk_poll_serial() {
  int budget = 64;
  while (budget-- > 0 && Serial.available() > 0) {
    int c = Serial.read();
    if (c < 0) break;
    if (c == '\n' || c == '\r') {
      if (uk_ser_len) {
        uk_ser_line[uk_ser_len] = 0;
        uk_command(uk_ser_line);
        uk_ser_len = 0;
      }
    } else if (uk_ser_len < sizeof(uk_ser_line) - 1) {
      uk_ser_line[uk_ser_len++] = (char)c;
    }
  }
}

static void uk_poll_udp() {
  if (uk_sock < 0) return;
  static char buf[256];
  struct sockaddr_in from;
  socklen_t fl = sizeof from;
  int n = recvfrom(uk_sock, buf, sizeof(buf) - 1, MSG_DONTWAIT, (struct sockaddr*)&from, &fl);
  if (n <= 0) return;
  buf[n] = 0;
  if (!ukc.hub[0]) uk_hub_learned = from.sin_addr.s_addr;  // zero-config: answer whoever commands us
  char* save = nullptr;
  for (char* l = strtok_r(buf, "\r\n", &save); l; l = strtok_r(nullptr, "\r\n", &save)) uk_command(l);
}

static float uk_read_vbat() {
#if UK_VBAT_PIN >= 0
  return (float)analogReadMilliVolts(UK_VBAT_PIN) * UK_VBAT_DIV / 1000.0f;
#else
  return NAN;
#endif
}

static void uk_setup_common() {
  Serial.setTxBufferSize(2048);   // printing must never stall the ranging loop
  Serial.setRxBufferSize(512);
  Serial.begin(115200);
  delay(200);
  uint64_t m = ESP.getEfuseMac();
  snprintf(uk_mac, sizeof uk_mac, "%02X%02X%02X%02X%02X%02X", (unsigned)(m & 0xFF), (unsigned)((m >> 8) & 0xFF),
           (unsigned)((m >> 16) & 0xFF), (unsigned)((m >> 24) & 0xFF), (unsigned)((m >> 32) & 0xFF),
           (unsigned)((m >> 40) & 0xFF));
  uk_config_load();
#if UK_BUTTON_PIN >= 0
  pinMode(UK_BUTTON_PIN, INPUT_PULLUP);
#endif
  randomSeed((uint32_t)(m ^ (m >> 32)) ^ esp_random());
  uk_log("uwbkit firmware %s chip=%s built %s %s (compile-verified only; never run on hardware by the authors)",
         UK_FW_VERSION, UK_CHIP_NAME, __DATE__, __TIME__);
  uk_wifi_start();
}

static void uk_service() {
  static uint32_t t_udp = 0, t_wifi = 0, t_stat = 0, t_banner = 0, t_btn = 0;
  static uint8_t btn_prev = 1;
  uint32_t now = millis();
  uk_poll_serial();
  bool idle = !uk_engine_busy();
  if (idle && now - t_udp >= 5) {
    t_udp = now;
    uk_poll_udp();
    uk_udp_flush();
  }
  if (now - t_wifi >= 500) { t_wifi = now; uk_wifi_poll(); }
  if (now - t_stat >= 1000) {
    t_stat = now;
    uk_rate_hz = (float)uk_rate_count;
    uk_rate_count = 0;
    char b[UK_LINE_CAP];
    uk_emit(b, uk_fmt_status(b, sizeof b, ukc.id, UK_ROLE_NAMES[ukc.role], now / 1000UL, uk_read_vbat(), uk_rate_hz,
                             uk_ok, uk_fail), true);
  }
  if (t_banner == 0 || now - t_banner >= 10000) { t_banner = now ? now : 1; uk_banner(); }
#if UK_BUTTON_PIN >= 0
  if (now - t_btn >= 20) {   // 20 ms debounce
    t_btn = now;
    uint8_t v = (uint8_t)digitalRead(UK_BUTTON_PIN);
    if (btn_prev == 1 && v == 0) {
      char b[UK_LINE_CAP];
      uk_emit(b, uk_fmt_event(b, sizeof b, ukc.id, "button", "n=1"), true);
    }
    btn_prev = v;
  }
#endif
  yield();
}

#ifndef UK_NO_ENGINE
// =====================================================================================================
// Part 3 -- DS-TWR ranging engine on a polled radio HAL
// =====================================================================================================
// The sketch implements these (all non-blocking except radio_init):
enum { UK_EV_TX = 1, UK_EV_RX = 2, UK_EV_ERR = 4 };
static bool radio_init();                                        // reset + configure; false if the chip is not found
static uint8_t radio_poll();                                     // read+clear status, return UK_EV_* bits
static uint16_t radio_read(uint8_t* buf, uint16_t cap);         // last good frame, without FCS
static uint64_t radio_rx_ts();                                   // 40-bit, antenna-delay adjusted
static uint64_t radio_tx_ts();                                   // 40-bit, antenna-delay adjusted
static void radio_rx_on();                                       // force idle, clear status, receiver on now
static uint64_t radio_plan(uint64_t ref_ts, uint32_t dly_uus);  // arm next delayed TX; return its TX timestamp
static bool radio_send(uint8_t* f, uint16_t len, bool delayed, bool ranging);  // f needs 2 spare bytes (FCS)
static void radio_set_antdly(uint16_t v);
static void radio_diag(float* rssi_dbm, float* fp_dbm);         // diagnostics of the last received frame

// Timing (UWB microseconds ~ 1.026 us). Generous for an ESP32 polling the chip over SPI; DS-TWR cancels the
// clock-drift error of long reply times to first order.
#ifndef UK_RESP_DLY_UUS
#define UK_RESP_DLY_UUS 2000     // poll RX  -> resp TX   (responder)
#endif
#ifndef UK_FINAL_DLY_UUS
#define UK_FINAL_DLY_UUS 2000    // resp RX  -> final TX  (initiator)
#endif
#ifndef UK_RESULT_DLY_UUS
#define UK_RESULT_DLY_UUS 1500   // final RX -> result TX (responder; gives the initiator time to re-enable RX)
#endif
#define UK_RING_SLOT_MS 60       // mesh/survey token: wait per missing node before taking the turn anyway

enum UkSt : uint8_t {
  S_LISTEN, S_I_POLL_TX, S_I_WAIT_RESP, S_I_FINAL_TX, S_I_WAIT_RESULT, S_R_RESP_TX, S_R_WAIT_FINAL, S_R_RESULT_TX,
  S_MISC_TX
};
static UkSt uk_st = S_LISTEN;
static uint32_t uk_st_t0 = 0, uk_st_to = 0;   // micros
static bool uk_radio_ok = false;
static uint16_t uk_bseq_ctr = 0;
static uint8_t uk_seq8 = 0;
static uint8_t uk_txf[64];
static uint8_t uk_rxf[128];
// initiator side
static char i_peer[3];
static uint16_t i_bseq;
static uint64_t i_poll_tx, i_resp_rx, i_final_tx;
// responder side
static char r_peer[3];
static uint16_t r_bseq;
static uint64_t r_poll_rx, r_resp_tx;
// neighbourhood
static uint32_t pe_seen[UK_NIDS];
static uint8_t pe_fail[UK_NIDS];
static uint32_t pe_backoff[UK_NIDS];
static uint32_t uk_last_foreign_us = 0;
// tag cycle
static uint32_t tag_next_ms = 0;
static uint8_t tag_peers[9], tag_np = 0, tag_pi = 0;
// mesh / survey token ring
static int ring_last = 0;          // number (1..9) of the last node that finished its turn, 0 = none heard
static uint32_t ring_last_ms = 0;
static bool ring_in_turn = false;
static uint8_t ring_peers[9], ring_np = 0, ring_pi = 0;
static uint32_t survey_until = 0;
// pending broadcast command (SURVEY propagation)
static char bc_cmd[24];
static uint8_t bc_left = 0;
static uint32_t bc_next = 0;
// calibration
static bool cal_on = false;
static char cal_peer[3];
static float cal_true = 0;
static int cal_n = 20, cal_cnt = 0, cal_iter = 0;
static double cal_sum = 0;
static uint32_t cal_next = 0, cal_progress = 0;

static bool uk_engine_busy() { return uk_radio_ok && uk_st != S_LISTEN; }

static void st_set(UkSt s, uint32_t to_us) { uk_st = s; uk_st_t0 = micros(); uk_st_to = to_us; }
static void to_listen() { radio_rx_on(); uk_st = S_LISTEN; }

static bool survey_active() { return survey_until && (int32_t)(millis() - survey_until) < 0; }
static bool in_ring() { return ukc.role == UK_ROLE_MESH || (ukc.role == UK_ROLE_ANCHOR && survey_active()); }
static char ring_letter() { return ukc.role == UK_ROLE_MESH ? 'N' : 'A'; }
static int my_num() { return ukc.id[1] - '0'; }

static void peer_ok(const char* id) { int k = uk_id_index(id); if (k >= 0) { pe_fail[k] = 0; pe_backoff[k] = 0; } }
static void peer_fail(const char* id) {
  uk_fail++;
  int k = uk_id_index(id);
  if (k < 0) return;
  if (pe_fail[k] < 255) pe_fail[k]++;
  if (pe_fail[k] >= 3) pe_backoff[k] = millis() + 1000;   // silent peer: retry once a second
}

static void start_exchange(const char* peer) {
  i_peer[0] = peer[0]; i_peer[1] = peer[1]; i_peer[2] = 0;
  i_bseq = ++uk_bseq_ctr;
  uint16_t len = uk_hdr(uk_txf, uk_seq8++, i_peer, ukc.id, UK_F_POLL, i_bseq);
  if (!radio_send(uk_txf, len, false, true)) { peer_fail(i_peer); to_listen(); return; }
  st_set(S_I_POLL_TX, 3000);
}

static void send_broadcast(uint8_t type, const char* text) {
  uint16_t len = uk_hdr(uk_txf, uk_seq8++, "**", ukc.id, type, 0);
  if (text) {
    size_t t = strlen(text);
    if (t > 40) t = 40;
    memcpy(uk_txf + len, text, t);
    len += (uint16_t)t;
  }
  if (radio_send(uk_txf, len, false, false)) st_set(S_MISC_TX, 3000);
  else to_listen();
}

static void cal_feed(float d) {
  if (!cal_on || isnan(d) || d < -10.0f || d > 100.0f) return;
  cal_sum += d;
  cal_cnt++;
  cal_progress = millis();
  if (cal_cnt < cal_n) return;
  float mean = (float)(cal_sum / cal_cnt);
  float err = mean - cal_true;
  long delta = lroundf(err / (float)UK_M_PER_TICK);   // +1 tick of own TX+RX delay shortens the range ~4.69 mm
  long nv = (long)ukc.antdly + delta;
  if (nv < 0) nv = 0;
  if (nv > 65535) nv = 65535;
  cal_iter++;
  uk_log("cal iter=%d peer=%s n=%d mean=%.3f true=%.3f err=%+.3f antdly %u -> %ld", cal_iter, cal_peer, cal_cnt,
         (double)mean, (double)cal_true, (double)err, ukc.antdly, fabsf(err) < 0.02f ? (long)ukc.antdly : nv);
  bool done = fabsf(err) < 0.02f || cal_iter >= 6;
  if (fabsf(err) >= 0.02f) { ukc.antdly = (uint16_t)nv; radio_set_antdly(ukc.antdly); }
  cal_sum = 0;
  cal_cnt = 0;
  if (done) {
    cal_on = false;
    if (fabsf(err) >= 0.02f) uk_log("cal stopped after %d iterations without reaching 2 cm", cal_iter);
    uk_kv_int("antdly", ukc.antdly);
    uk_log("send SAVE to keep antdly=%u across reboots", ukc.antdly);
  }
}

static void on_tx_done() {
  switch (uk_st) {
    case S_I_POLL_TX:
      i_poll_tx = radio_tx_ts();
      radio_rx_on();
      st_set(S_I_WAIT_RESP, 5000);
      break;
    case S_I_FINAL_TX:
      radio_rx_on();
      st_set(S_I_WAIT_RESULT, 6000);
      break;
    case S_R_RESP_TX:
      radio_rx_on();
      st_set(S_R_WAIT_FINAL, 6000);
      break;
    case S_R_RESULT_TX:
    case S_MISC_TX:
      to_listen();
      break;
    default:
      break;
  }
}

static bool st_listening() {
  return uk_st == S_LISTEN || uk_st == S_I_WAIT_RESP || uk_st == S_I_WAIT_RESULT || uk_st == S_R_WAIT_FINAL;
}

static void on_frame(const uint8_t* f, uint16_t len, uint64_t rxts) {
  UkFrame fr;
  if (!uk_parse_frame(f, len, &fr)) {   // foreign or empty frame: the receiver is off now, turn it back on
    if (st_listening()) radio_rx_on();
    return;
  }
  int sk = uk_id_index(fr.src);
  if (sk >= 0) pe_seen[sk] = millis() | 1;
  bool to_me = fr.dst[0] == ukc.id[0] && fr.dst[1] == ukc.id[1];
  bool bcast = fr.dst[0] == '*' && fr.dst[1] == '*';
  if (!to_me && !bcast) uk_last_foreign_us = micros();

  switch (fr.type) {
    case UK_F_POLL:
      if (to_me && uk_st == S_LISTEN) {
        r_peer[0] = fr.src[0]; r_peer[1] = fr.src[1]; r_peer[2] = 0;
        r_bseq = fr.bseq;
        r_poll_rx = rxts;
        r_resp_tx = radio_plan(r_poll_rx, UK_RESP_DLY_UUS);
        uint16_t l = uk_hdr(uk_txf, uk_seq8++, r_peer, ukc.id, UK_F_RESP, r_bseq);
        if (radio_send(uk_txf, l, true, true)) { st_set(S_R_RESP_TX, 5000); return; }
        uk_fail++;   // delayed TX was late: the loop was too slow this time
      }
      break;
    case UK_F_RESP:
      if (to_me && uk_st == S_I_WAIT_RESP && fr.bseq == i_bseq && fr.src[0] == i_peer[0] && fr.src[1] == i_peer[1]) {
        i_resp_rx = rxts;
        i_final_tx = radio_plan(i_resp_rx, UK_FINAL_DLY_UUS);
        uint16_t l = uk_hdr(uk_txf, uk_seq8++, i_peer, ukc.id, UK_F_FINAL, i_bseq);
        uk_put40(uk_txf + l, i_poll_tx);
        uk_put40(uk_txf + l + 5, i_resp_rx);
        uk_put40(uk_txf + l + 10, i_final_tx);
        l += 15;
        if (radio_send(uk_txf, l, true, true)) { st_set(S_I_FINAL_TX, 5000); return; }
        peer_fail(i_peer);
        to_listen();
        return;
      }
      break;
    case UK_F_FINAL:
      if (to_me && uk_st == S_R_WAIT_FINAL && fr.bseq == r_bseq && fr.src[0] == r_peer[0] &&
          fr.src[1] == r_peer[1] && fr.pl_len >= 15) {
        uint64_t final_rx = rxts;
        double d = uk_dstwr_m(uk_get40(fr.pl), uk_get40(fr.pl + 5), uk_get40(fr.pl + 10), r_poll_rx, r_resp_tx,
                              final_rx);
        float rssi = NAN, fp = NAN;
        radio_diag(&rssi, &fp);
        int32_t dmm = isnan(d) ? INT32_MIN : (int32_t)lround(d * 1000.0);
        uint16_t l = uk_hdr(uk_txf, uk_seq8++, r_peer, ukc.id, UK_F_RESULT, r_bseq);
        uk_put32(uk_txf + l, dmm);
        uk_put16(uk_txf + l + 4, isnan(rssi) ? INT16_MIN : (int16_t)lroundf(rssi * 10.0f));
        uk_put16(uk_txf + l + 6, isnan(fp) ? INT16_MIN : (int16_t)lroundf(fp * 10.0f));
        l += 8;
        radio_plan(final_rx, UK_RESULT_DLY_UUS);
        bool sent = radio_send(uk_txf, l, true, false) || radio_send(uk_txf, l, false, false);
        if (sent) st_set(S_R_RESULT_TX, 5000);
        uk_report_range(r_bseq, r_peer, ukc.id, (float)d, rssi, fp, true);   // a = initiator, b = responder
        if (sent) return;
        to_listen();
        return;
      }
      break;
    case UK_F_RESULT:
      if (fr.pl_len >= 8) {
        int32_t dmm = uk_get32(fr.pl);
        int16_t r10 = uk_get16(fr.pl + 4), f10 = uk_get16(fr.pl + 6);
        float d = dmm == INT32_MIN ? NAN : dmm / 1000.0f;
        float rssi = r10 == INT16_MIN ? NAN : r10 / 10.0f, fp = f10 == INT16_MIN ? NAN : f10 / 10.0f;
        if (to_me && uk_st == S_I_WAIT_RESULT && fr.bseq == i_bseq && fr.src[0] == i_peer[0] &&
            fr.src[1] == i_peer[1]) {
          peer_ok(i_peer);
          uk_report_range(i_bseq, ukc.id, i_peer, d, rssi, fp, true);
          if (cal_on && i_peer[0] == cal_peer[0] && i_peer[1] == cal_peer[1]) cal_feed(d);
          to_listen();
          return;
        }
        if (ukc.relay && uk_valid_id(fr.dst)) uk_report_range(fr.bseq, fr.dst, fr.src, d, rssi, fp, false);  // relay
      }
      break;
    case UK_F_TOKEN:
      if (fr.src[0] == ring_letter()) { ring_last = fr.src[1] - '0'; ring_last_ms = millis(); }
      break;
    case UK_F_CMD: {
      char t[24];
      uint16_t k = fr.pl_len < sizeof(t) - 1 ? fr.pl_len : (uint16_t)(sizeof(t) - 1);
      memcpy(t, fr.pl, k);
      t[k] = 0;
      if (!strncmp(t, "SURVEY ", 7) && ukc.role == UK_ROLE_ANCHOR) {
        int s = atoi(t + 7);
        survey_until = s > 0 ? (millis() + (uint32_t)s * 1000u) | 1 : 0;
        ring_last = 0;               // stagger the first turns by anchor number
        ring_last_ms = millis();
        uk_log("survey %ds requested over UWB by %s", s, fr.src);
      }
      break;
    }
    default:
      break;
  }
  // nothing transmitted: keep (or go back to) listening
  if (st_listening()) radio_rx_on();
}

static bool channel_busy() { return (uint32_t)(micros() - uk_last_foreign_us) < 4000u; }

static void schedule() {
  uint32_t now = millis();
  if (bc_left && (int32_t)(now - bc_next) >= 0) {
    bc_left--;
    bc_next = now + 15;
    send_broadcast(UK_F_CMD, bc_cmd);
    return;
  }
  if (cal_on) {
    if (now - cal_progress > 5000) {
      cal_on = false;
      uk_kv("cal", "fail_no_ranges");
      return;
    }
    if ((int32_t)(now - cal_next) >= 0) { cal_next = now + 25; start_exchange(cal_peer); }
    return;
  }
  if (ukc.role == UK_ROLE_TAG) {
    if (tag_pi < tag_np) {
      if (channel_busy()) return;
      char p[3];
      uk_id_from_index(tag_peers[tag_pi++], p);
      start_exchange(p);
      return;
    }
    if ((int32_t)(now - tag_next_ms) >= 0) {
      tag_np = 0;
      tag_pi = 0;
      for (int k = 0; k < 9; k++) {          // anchors A1..A9: A1..A4 always tried, A5+ once heard
        bool recent = pe_seen[k] && now - pe_seen[k] < 5000;
        if (k >= 4 && !recent) continue;
        if (pe_backoff[k] && (int32_t)(now - pe_backoff[k]) < 0) continue;
        tag_peers[tag_np++] = (uint8_t)k;
      }
      uint32_t period = 1000u / (ukc.rate_hz ? ukc.rate_hz : 10);
      tag_next_ms = now + period + (uint32_t)random(0, 8);   // random back-off keeps two tags from locking in step
    }
    return;
  }
  if (in_ring()) {
    int N = ukc.role == UK_ROLE_MESH ? 5 : 4;
    int me = my_num();
    char L = ring_letter();
    for (int k = 0; k < 9; k++) {
      int idx = uk_id_letter_idx(L) * 9 + k;
      if (pe_seen[idx] && now - pe_seen[idx] < 3000 && k + 1 > N) N = k + 1;
    }
    if (me > N) N = me;
    if (ring_in_turn) {
      if (ring_pi < ring_np) {
        char p[3];
        uk_id_from_index(ring_peers[ring_pi++], p);
        start_exchange(p);
        return;
      }
      ring_in_turn = false;
      ring_last = me;
      ring_last_ms = now;
      send_broadcast(UK_F_TOKEN, nullptr);
      return;
    }
    int dist = ring_last == 0 ? me : ((me - ring_last - 1 + N) % N) + 1;
    uint32_t wait = ring_last == 0 ? (uint32_t)me * UK_RING_SLOT_MS : (uint32_t)(dist - 1) * UK_RING_SLOT_MS;
    if (ring_last == me) wait = (uint32_t)N * UK_RING_SLOT_MS;   // alone: run again after a full silent round
    if (now - ring_last_ms >= wait && !channel_busy()) {
      ring_np = 0;
      ring_pi = 0;
      int base = uk_id_letter_idx(L) * 9;
      for (int k = 0; k < 9; k++) {
        if (k + 1 == me) continue;
        if (pe_seen[base + k] && now - pe_seen[base + k] < 3000) ring_peers[ring_np++] = (uint8_t)(base + k);
      }
      ring_in_turn = true;
    }
  }
}

static void uk_engine_reset() {
  if (uk_radio_ok) to_listen();
  uk_st = S_LISTEN;
  tag_np = tag_pi = 0;
  uint32_t period = 1000u / (ukc.rate_hz ? ukc.rate_hz : 10);
  tag_next_ms = millis() + (uint32_t)(my_num() - 1) * period / 2;   // slot by id: T2 starts half a period later
  ring_last = 0;
  ring_last_ms = millis();
  ring_in_turn = false;
}

static void uk_on_config_changed() { uk_engine_reset(); uk_banner(); }
static void uk_on_antdly(uint16_t v) { if (uk_radio_ok) radio_set_antdly(v); }
static void uk_on_cal(const char* peer, float true_m, int n) {
  if (!uk_radio_ok) { uk_kv("error", "radio not initialised"); return; }
  cal_peer[0] = peer[0]; cal_peer[1] = peer[1]; cal_peer[2] = 0;
  cal_true = true_m; cal_n = n; cal_cnt = 0; cal_iter = 0; cal_sum = 0;
  cal_next = millis(); cal_progress = millis();
  cal_on = true;
  uk_kv("cal", "start");
  uk_log("cal: ranging to %s, true %.3f m, %d samples per iteration", cal_peer, (double)true_m, n);
}
static void uk_on_survey(int s) {
  survey_until = s > 0 ? (millis() + (uint32_t)s * 1000u) | 1 : 0;
  snprintf(bc_cmd, sizeof bc_cmd, "SURVEY %d", s);
  bc_left = 3;
  bc_next = millis();
  ring_last = 0;
  ring_last_ms = millis();
  uk_kv_int("survey", s);
}

static void uk_engine_begin() {
  static uint32_t last_try = 0;
  if (uk_radio_ok) return;
  if (last_try && millis() - last_try < 5000) return;
  last_try = millis() | 1;
  uk_radio_ok = radio_init();
  if (uk_radio_ok) {
    radio_set_antdly(ukc.antdly);
    uk_log("radio %s ready ch=%d antdly=%u", UK_CHIP_NAME, UK_CHANNEL, ukc.antdly);
    uk_engine_reset();
  } else {
    uk_log("radio %s init FAILED (check SPI/CS/RST pins in the README); retrying every 5 s", UK_CHIP_NAME);
  }
}

static void tagless_on_rx(const uint8_t* f, uint16_t n);   // TAGLESS
static void uk_engine_step() {
  if (!uk_radio_ok) { uk_engine_begin(); return; }
  static uint32_t last_rx_ms = 0, last_rearm_ms = 0;
  uint8_t ev = radio_poll();
  if (ev & (UK_EV_RX | UK_EV_ERR)) last_rx_ms = millis();
  if (ev & UK_EV_TX) on_tx_done();
  if (ev & UK_EV_RX) {
    uint64_t ts = radio_rx_ts();
    uint16_t n = radio_read(uk_rxf, sizeof uk_rxf);
    tagless_on_rx(uk_rxf, n);   // TAGLESS: CIR of overheard frames, before RX is re-enabled
    on_frame(uk_rxf, n, ts);
  }
  if ((ev & UK_EV_ERR) && !(ev & UK_EV_RX)) {
    if (st_listening()) radio_rx_on();
  }
  // listen watchdog: if an idle board has heard nothing for 500 ms, re-arm the receiver (cheap insurance against a
  // missed status bit leaving the radio deaf; costs at most one frame per re-arm)
  if (uk_st == S_LISTEN && millis() - last_rx_ms > 500 && millis() - last_rearm_ms > 500) {
    last_rearm_ms = millis();
    radio_rx_on();
  }
  if (uk_st != S_LISTEN && (uint32_t)(micros() - uk_st_t0) > uk_st_to) {   // every wait has a timeout
    if (uk_st >= S_I_POLL_TX && uk_st <= S_I_WAIT_RESULT) peer_fail(i_peer);
    else if (uk_st == S_R_RESP_TX || uk_st == S_R_WAIT_FINAL) uk_fail++;
    to_listen();
  }
  if (uk_st == S_LISTEN) schedule();
}
#endif  // UK_NO_ENGINE
#endif  // UK_HOST_TEST
#endif  // UWBKIT_COMMON_H
