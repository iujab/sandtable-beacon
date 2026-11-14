"""Build hardware/firmware/uwbkit_tagless/: the shared uwb-kit DW3000 firmware + the Tagless CIR delta.

    python hardware/firmware/make_merged.py [path/to/uwb-kit/firmware]

It copies the kit's `uwbkit_dw3000` sketch (unmodified kit code) next to `tagless_cir.h` and applies four small,
mechanical patches (listed in DELTA.md, which this script rewrites). A board flashed with the result behaves as a
normal kit board for every other project and additionally answers `CIR ON` by streaming CIR frames captured on the
ranging frames it overhears: no extra airtime, no change to the kit's protocol.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[3] / "uwb-kit" / "firmware"
SRC = KIT / "uwbkit_dw3000"
OUT = HERE / "uwbkit_tagless"

GLUE = r'''
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
'''

PATCHES = [
    ("uwbkit_common.h", "static void uk_command(char* line) {",
     "static bool tagless_command(const char* cmd, const char* a1, const char* a2, const char* a3);   // TAGLESS\nstatic void uk_command(char* line) {",
     "forward declaration of the CIR command handler"),
    ("uwbkit_common.h", '  if (!strcmp(cmd, "?")) { uk_banner(); return; }',
     '  if (tagless_command(cmd, a1, a2, a3)) return;   // TAGLESS: CIR ON|OFF|?\n  if (!strcmp(cmd, "?")) { uk_banner(); return; }',
     "route the CIR command"),
    ("uwbkit_common.h", "static void uk_engine_step() {",
     "static void tagless_on_rx(const uint8_t* f, uint16_t n);   // TAGLESS\nstatic void uk_engine_step() {",
     "forward declaration of the RX hook"),
    ("uwbkit_common.h", "    uint16_t n = radio_read(uk_rxf, sizeof uk_rxf);\n",
     "    uint16_t n = radio_read(uk_rxf, sizeof uk_rxf);\n    tagless_on_rx(uk_rxf, n);   // TAGLESS: CIR of overheard frames, before RX is re-enabled\n",
     "call the RX hook right after the frame is read (the accumulator is still valid)"),
    ("uwbkit_tagless.ino", '#include "uwbkit_common.h"\n', '#include "uwbkit_common.h"\n' + GLUE,
     "include tagless_cir.h and define the hook + command (about 50 lines, all inside the marked block)"),
]


def main():
    if not SRC.is_dir():
        sys.exit(f"kit firmware not found at {SRC}")
    OUT.mkdir(exist_ok=True)
    shutil.copy(SRC / "uwbkit_common.h", OUT / "uwbkit_common.h")
    shutil.copy(SRC / "uwbkit_dw3000.ino", OUT / "uwbkit_tagless.ino")
    shutil.copy(HERE / "tagless_cir_node" / "tagless_cir.h", OUT / "tagless_cir.h")
    lines = ["# uwbkit_tagless: what differs from the shared kit firmware", "",
             "Generated by `hardware/firmware/make_merged.py` from `uwb-kit/firmware/uwbkit_dw3000` (kit code copied unmodified)",
             "plus `tagless_cir.h`. Patches applied, each a single textual insertion:", ""]
    for fname, old, new, why in PATCHES:
        p = OUT / fname
        s = p.read_text()
        if s.count(old) != 1:
            sys.exit(f"patch anchor not found exactly once in {fname}: {old[:60]!r} (kit firmware changed; update make_merged.py)")
        p.write_text(s.replace(old, new))
        lines.append(f"- `{fname}`: {why}.")
    lines += ["", "Behaviour: identical to the kit firmware until the host sends `CIR ON [hz] [baud]`. Then every ranging frame this",
              "board overhears (frames addressed to it are skipped so DS-TWR reply deadlines are untouched) is followed by one",
              "binary CIR frame on USB serial (dropped instead of blocking if the UART buffer is full) and, when Wi-Fi and a hub",
              "address are configured, one UDP datagram to port 1946.", "",
              "Status: compile-verified only (see `hardware/build_logs/uwbkit_tagless.esp32.log`). Never run on a radio. Whether a",
              "~1.5 ms accumulator read between overheard frames costs the kit any ranging rate is unknown until real boards run it."]
    (OUT / "DELTA.md").write_text("\n".join(lines) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
