// g++ -std=c++17 -I. openpilot/selfdrive/pandad/tests/test_rivian_keepalive.cc -o /tmp/t && /tmp/t
#undef NDEBUG
#include <cassert>
#include <cstdio>

#include "openpilot/selfdrive/pandad/rivian_keepalive.h"

static const uint64_t MS = 1000000;

// opendbc's riviancan.py frames at counters 3 and 4, in the keep-alive's own order
struct Ref { uint32_t addr; uint8_t len, xor_out, c[2][8]; };
static const Ref REFS[] = {
  {0x100, 8, 0x5F, {{0xe8, 0x03, 0x40}, {0x66, 0x04, 0x40}}},
  {0x110, 8, 0x41, {{0x26, 0x13, 0x80, 0x18}, {0xa8, 0x14, 0x80, 0x18}}},
  {0x160, 5, 0x12, {{0x31, 0x03, 0x79, 0x60, 0x10}, {0xc5, 0x04, 0x79, 0x60, 0x10}}},
};

static CanFrame frame(const Ref &r, int i) {
  CanFrame f{r.addr, 0, r.len, {}};
  memcpy(f.dat, r.c[i], r.len);
  return f;
}

static void send(RivianKeepalive &ka, int i, uint64_t now, std::vector<CanFrame> *out = nullptr) {
  for (const auto &r : REFS) {
    CanFrame f = frame(r, i);
    ka.on_frame(f, now);
    if (out) out->push_back(f);
  }
}

int main() {
  for (const auto &r : REFS) {
    for (int i = 0; i < 2; i++) assert(RivianKeepalive::checksum(r.c[i], r.len, r.xor_out) == r.c[i][0]);
  }

  RivianKeepalive ka;
  assert(ka.fill(100 * MS).empty());  // nothing before the first message
  send(ka, 0, 1000 * MS);
  assert(ka.on_message(1000 * MS) == 0);
  assert(ka.fill(1024 * MS).empty());  // sendcan on time

  // a stall: the first fill at 25 ms is exactly what card would have sent next (counter 4)
  auto fill = ka.fill(1025 * MS);
  assert(fill.size() == 3);
  for (size_t i = 0; i < fill.size(); i++) assert(memcmp(fill[i].dat, REFS[i].c[1], REFS[i].len) == 0);
  assert(ka.fill(1034 * MS).empty() && ka.fill(1035 * MS).size() == 3);  // then every 10 ms
  for (int t = 1045; t < 1300; t += 10) ka.fill(t * MS);
  assert(ka.fill(1300 * MS).empty());  // at most 15 fills
  assert(ka.on_message(1300 * MS) == 15);

  // card's next frame (counter 4) moves on by the 15 fills, mod 15: unchanged
  std::vector<CanFrame> real;
  send(ka, 1, 1310 * MS, &real);
  for (size_t i = 0; i < real.size(); i++) assert(memcmp(real[i].dat, REFS[i].c[1], REFS[i].len) == 0);

  // after one fill card's counter 4 goes out as 5, with a valid checksum
  ka.on_message(1310 * MS);
  assert(ka.fill(1335 * MS).size() == 3);
  real.clear();
  send(ka, 1, 1340 * MS, &real);
  for (size_t i = 0; i < real.size(); i++) {
    assert((real[i].dat[1] & 0x0F) == 5);
    assert(RivianKeepalive::checksum(real[i].dat, REFS[i].len, REFS[i].xor_out) == real[i].dat[0]);
  }

  // a message card stopped sending is not repeated
  RivianKeepalive ka2;
  CanFrame old = frame(REFS[1], 0);
  ka2.on_frame(old, 1000 * MS);
  ka2.on_message(1100 * MS);
  assert(ka2.fill(1125 * MS).empty());

  printf("rivian keepalive: all tests passed\n");
  return 0;
}
