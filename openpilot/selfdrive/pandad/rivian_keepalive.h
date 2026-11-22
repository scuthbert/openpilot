#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

// Rivian: the EPAS and VDM latch a fault after ~60-80 ms without ACM_SteeringControl or ACM_longitudinalRequest,
// and the stock ACM leaves ACC without the relayed VDM_AdasSts. Device-wide stalls of ~100 ms freeze card but not
// pandad, so while sendcan is quiet pandad repeats the last frame of each control message every 10 ms with the
// counter advanced, for at most 150 ms. Card's later frames get the same counter shift, keeping the sequence
// continuous. The repeats carry the last real command and still pass the panda's safety checks.

struct CanFrame {
  uint32_t addr;
  uint8_t bus, len;
  uint8_t dat[64];
};

class RivianKeepalive {
public:
  static constexpr uint64_t GAP_NS = 25000000, PERIOD_NS = 10000000, STALE_NS = 50000000;
  static constexpr int MAX_FILLS = 15;

  // a real frame from card: remember it and shift its counter by the fills so far
  void on_frame(CanFrame &f, uint64_t now) {
    for (auto &s : slots) {
      if (s.addr == f.addr && s.bus == f.bus && s.len == f.len) {
        memcpy(s.last, f.dat, s.len);
        s.last_ns = now;
        shift(s, f.dat);
      }
    }
  }

  // end of a real sendcan message, returns how many fills its gap needed
  int on_message(uint64_t now) {
    const int n = fills;
    fills = 0;
    last_real_ns = now;
    return n;
  }

  // sendcan is quiet: the frames to send now
  std::vector<CanFrame> fill(uint64_t now) {
    std::vector<CanFrame> out;
    const uint64_t due = fills == 0 ? last_real_ns + GAP_NS : last_fill_ns + PERIOD_NS;
    if (last_real_ns == 0 || fills >= MAX_FILLS || now < due) return out;
    for (auto &s : slots) {
      if (s.last_ns + STALE_NS < last_real_ns) continue;  // card wasn't sending it before the gap
      s.offset = (s.offset + 1) % 15;
      CanFrame &f = out.emplace_back(CanFrame{s.addr, s.bus, s.len, {}});
      memcpy(f.dat, s.last, s.len);
      shift(s, f.dat);
    }
    if (!out.empty()) {
      fills++;
      last_fill_ns = now - due < PERIOD_NS ? due : now;  // stay on the 10 ms grid when polled late
    }
    return out;
  }

  static uint8_t checksum(const uint8_t *dat, uint8_t len, uint8_t xor_out) {
    uint8_t crc = 0;
    for (int i = 1; i < len; i++) {
      crc ^= dat[i];
      for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (crc << 1) ^ 0x1D : crc << 1;
    }
    return crc ^ xor_out;
  }

private:
  struct Slot {
    uint32_t addr;
    uint8_t bus, len, xor_out, offset = 0, last[8] = {};
    uint64_t last_ns = 0;
  };
  // card sends all at 100 Hz: checksum in byte 0, counter 0-14 in the low nibble of byte 1
  std::array<Slot, 5> slots = {{
    {0x100, 0, 8, 0x5F},  // ACM_Status
    {0x110, 0, 8, 0x41},  // ACM_SteeringControl
    {0x120, 0, 8, 0x63},  // ACM_lkaHbaCmd
    {0x160, 0, 5, 0x12},  // ACM_longitudinalRequest
    {0x162, 2, 8, 0xD1},  // VDM_AdasSts relayed to the stock ACM
  }};
  uint64_t last_real_ns = 0, last_fill_ns = 0;
  int fills = 0;

  static void shift(const Slot &s, uint8_t *dat) {
    if (s.offset == 0) return;
    dat[1] = (dat[1] & 0xF0) | ((dat[1] & 0x0F) + s.offset) % 15;
    dat[0] = checksum(dat, s.len, s.xor_out);
  }
};
