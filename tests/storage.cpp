// Exercise the upstream production parser using its in-memory GPT test harness.
#define main upstream_test_main
#include "tests/RootLayout.cpp"
#undef main

constexpr uint64_t linuxBlocks = 124748038144ULL / 4096;
constexpr uint64_t linuxStart = 33550848;

static void entry(unsigned index, const char *name, uint64_t first, uint64_t last) {
    auto p = entries + index * 128;
    memset(p, 0, 128);
    p[0] = 1;
    p[16] = uint8_t(index + 1);
    put64(p + 32, first);
    put64(p + 40, last);
    for (size_t i = 0; i < strlen(name); ++i) p[56 + i * 2] = uint8_t(name[i]);
}

static void setup() {
    fixture();
    // The harness rejects any manifest read outside this particular LBA.
    userdata = linuxStart;
    put64(header + 32, linuxStart + 2 * linuxBlocks + 9);
    put64(header + 48, linuxStart + 2 * linuxBlocks + 4);
    // The recorded sheng GPT has adjacent, equally sized userdata and linux partitions.
    entry(0, "userdata", linuxStart - linuxBlocks, linuxStart - 1);
    entry(1, "linux", linuxStart, linuxStart + linuxBlocks - 1);
}

static void verify(bool expected) {
    checksums();
    RootLayout layout;
    const char *why = nullptr;
    assert(findRootLayout(&layout, &why) == expected);
    if (expected) {
        assert(layout.userdataFirst == linuxStart && layout.userdataLast + 1 == linuxStart + linuxBlocks);
        assert(layout.manifestStatus == 0 && layout.windows == 2);
    } else {
        assert(reads == 2);  // Only the GPT header and entries were read.
    }
}

int main() {
    setup(); verify(true);
    setup(); memset(entries + 128, 0, 128); verify(false);
    setup(); entry(1, "linux-old", linuxStart, linuxStart + linuxBlocks - 1); verify(false);
    setup(); entry(1, "linux", linuxStart, linuxStart + linuxBlocks - 2); verify(false);
    setup(); entry(2, "linux", linuxStart + linuxBlocks, linuxStart + 2 * linuxBlocks - 1); verify(false);
    setup(); entry(0, "userdata", 1024, linuxStart); verify(false);
    puts("PASS: isolated root, missing/exact label, capacity, duplicates and overlap");
}
