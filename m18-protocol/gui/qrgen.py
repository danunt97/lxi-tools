"""
Minimal, dependency-free QR Code encoder (byte mode) with SVG and PNG output.

Follows ISO/IEC 18004 and the structure of Project Nayuki's reference
implementation (MIT licence). Only what the battery labels need is
implemented: byte mode, all versions 1-40, error correction L/M/Q/H,
automatic version and mask selection.

    from qrgen import QrCode
    qr = QrCode.encode("http://host:8018/b/36-863604", ecl="M")
    svg = qr.to_svg()
    png = qr.to_png(scale=8)
"""

import struct
import zlib

# Index 0 is padding so tables can be indexed by version number directly.
_ECC_PER_BLOCK = {
    "L": (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
_NUM_BLOCKS = {
    "L": (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
          8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
          23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
          25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}


def _bit(x, i):
    return (x >> i) & 1 != 0


def _num_raw_data_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _num_data_codewords(ver, ecl):
    return _num_raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ecl][ver] * _NUM_BLOCKS[ecl][ver]


def _rs_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _rs_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _rs_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _rs_mul(coef, factor)
    return result


class QrCode:
    def __init__(self, version, ecl, codewords, mask=None):
        self.version = version
        self.ecl = ecl
        self.size = version * 4 + 17
        n = self.size
        self.modules = [[False] * n for _ in range(n)]
        self._isfunc = [[False] * n for _ in range(n)]
        self._draw_function_patterns()
        allcw = self._add_ecc_and_interleave(codewords)
        self._draw_codewords(allcw)
        if mask is None:
            best, best_penalty = 0, None
            for m in range(8):
                self._apply_mask(m)
                self._draw_format_bits(m)
                p = self._penalty()
                if best_penalty is None or p < best_penalty:
                    best, best_penalty = m, p
                self._apply_mask(m)  # XOR again = undo
            mask = best
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)
        del self._isfunc

    # ---------------------------------------------------------------- encode
    @classmethod
    def encode(cls, text, ecl="M", min_version=1, max_version=40):
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        ecl = ecl.upper()
        for ver in range(min_version, max_version + 1):
            cap_bits = _num_data_codewords(ver, ecl) * 8
            count_bits = 8 if ver <= 9 else 16
            used = 4 + count_bits + len(data) * 8
            if used <= cap_bits:
                break
        else:
            raise ValueError("data too long for a QR code")

        bits = []

        def append(val, n):
            for i in reversed(range(n)):
                bits.append((val >> i) & 1)

        append(0x4, 4)                 # byte mode
        append(len(data), count_bits)
        for b in data:
            append(b, 8)
        append(0, min(4, cap_bits - len(bits)))   # terminator
        append(0, (-len(bits)) % 8)               # byte align
        pad = 0xEC
        while len(bits) < cap_bits:
            append(pad, 8)
            pad ^= 0xEC ^ 0x11
        codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        return cls(ver, ecl, codewords)

    # ------------------------------------------------------- function patterns
    def _set_func(self, x, y, dark):
        self.modules[y][x] = dark
        self._isfunc[y][x] = True

    def _draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self._set_func(6, i, i % 2 == 0)
            self._set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set_func(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self._alignment_positions()
        na = len(pos)
        for i in range(na):
            for j in range(na):
                if (i == 0 and j == 0) or (i == 0 and j == na - 1) or (i == na - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_func(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # reserve area; overwritten later
        self._draw_version()

    def _alignment_positions(self):
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        return [6] + [self.size - 7 - k * step for k in range(numalign - 2, -1, -1)]

    def _draw_format_bits(self, mask):
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size
        for i in range(6):
            self._set_func(8, i, _bit(bits, i))
        self._set_func(8, 7, _bit(bits, 6))
        self._set_func(8, 8, _bit(bits, 7))
        self._set_func(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self._set_func(14 - i, 8, _bit(bits, i))
        for i in range(8):
            self._set_func(n - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self._set_func(8, n - 15 + i, _bit(bits, i))
        self._set_func(8, n - 8, True)

    def _draw_version(self):
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            b = _bit(bits, i)
            a, c = self.size - 11 + i % 3, i // 3
            self._set_func(a, c, b)
            self._set_func(c, a, b)

    # ------------------------------------------------------------- codewords
    def _add_ecc_and_interleave(self, data):
        ver, ecl = self.version, self.ecl
        numblocks = _NUM_BLOCKS[ecl][ver]
        ecclen = _ECC_PER_BLOCK[ecl][ver]
        raw = _num_raw_data_modules(ver) // 8
        numshort = numblocks - raw % numblocks
        shortlen = raw // numblocks
        div = _rs_divisor(ecclen)
        blocks, k = [], 0
        for i in range(numblocks):
            take = shortlen - ecclen + (0 if i < numshort else 1)
            dat = list(data[k:k + take])
            k += take
            ecc = _rs_remainder(dat, div)
            if i < numshort:
                dat.append(0)
            blocks.append(dat + ecc)
        out = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != shortlen - ecclen or j >= numshort:
                    out.append(blk[i])
        return out

    def _draw_codewords(self, data):
        n = self.size
        i, total = 0, len(data) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = (n - 1 - vert) if upward else vert
                    if not self._isfunc[y][x] and i < total:
                        self.modules[y][x] = _bit(data[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    _MASKS = (
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    )

    def _apply_mask(self, mask):
        fn = self._MASKS[mask]
        for y in range(self.size):
            row, frow = self.modules[y], self._isfunc[y]
            for x in range(self.size):
                if not frow[x] and fn(x, y):
                    row[x] = not row[x]

    def _penalty(self):
        n, m = self.size, self.modules
        score = 0
        lines = [m[y] for y in range(n)] + [[m[y][x] for y in range(n)] for x in range(n)]
        p1 = (True, False, True, True, True, False, True)
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, prev = 1, v
            if run >= 5:
                score += 3 + run - 5
            for i in range(n - 6):
                if tuple(line[i:i + 7]) == p1:
                    before = line[max(0, i - 4):i]
                    after = line[i + 7:i + 11]
                    if (i < 4 or not any(before)) or (i + 11 > n or not any(after)):
                        score += 40
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(r) for r in m)
        total = n * n
        score += (abs(dark * 20 - total * 10) + total - 1) // total * 10 - 10
        return score

    # --------------------------------------------------------------- output
    def to_svg(self, border=4, dark="#000", light="#fff", px=None):
        n = self.size + 2 * border
        parts = []
        for y in range(self.size):
            for x in range(self.size):
                if self.modules[y][x]:
                    parts.append(f"M{x + border},{y + border}h1v1h-1z")
        size_attr = f' width="{px}" height="{px}"' if px else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}"{size_attr} '
                f'shape-rendering="crispEdges">'
                f'<rect width="100%" height="100%" fill="{light}"/>'
                f'<path d="{"".join(parts)}" fill="{dark}"/></svg>')

    def to_png(self, scale=8, border=4):
        n = (self.size + 2 * border) * scale
        rows = []
        for py in range(n):
            y = py // scale - border
            row = bytearray([0])  # filter type 0
            for px in range(n):
                x = px // scale - border
                dark = 0 <= x < self.size and 0 <= y < self.size and self.modules[y][x]
                row.append(0 if dark else 255)
            rows.append(bytes(row))

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", n, n, 8, 0, 0, 0, 0)  # 8-bit grayscale
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
