"""
Pure-Python QR Code generator (Byte mode, Error Correction Level M, Versions 1-10)
producing standard scannable SVG and PNG outputs with zero external dependencies.
"""
import struct
import zlib

# GF(256) arithmetic with primitive polynomial 0x11D
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        root = _EXP[i]
        for j, coef in enumerate(poly):
            next_poly[j] ^= coef
            next_poly[j + 1] ^= _gf_mul(coef, root)
        poly = next_poly
    return poly


def _rs_remainder(data: list[int], ec_count: int) -> list[int]:
    gen = _rs_generator_poly(ec_count)
    res = list(data) + [0] * ec_count
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


# Version specs for Error Correction Level M (ISO/IEC 18004):
# version: (total_data_codewords, ec_per_block, group1_blocks, group1_data, group2_blocks, group2_data, alignment_centers)
_VERSION_SPECS_M = {
    1:  (16,  10, 1, 16, 0, 0,  []),
    2:  (28,  16, 1, 28, 0, 0,  [6, 18]),
    3:  (44,  26, 1, 44, 0, 0,  [6, 22]),
    4:  (64,  18, 2, 32, 0, 0,  [6, 26]),
    5:  (86,  24, 2, 43, 0, 0,  [6, 30]),
    6:  (108, 16, 4, 27, 0, 0,  [6, 34]),
    7:  (124, 18, 4, 31, 0, 0,  [6, 22, 38]),
    8:  (154, 22, 2, 38, 2, 39, [6, 24, 42]),
    9:  (182, 22, 3, 36, 2, 37, [6, 26, 46]),
    10: (216, 26, 4, 43, 1, 44, [6, 28, 50]),
}

# Precomputed 15-bit BCH format strings for ECC level M (00) and masks 0..7, XORed with 0x5412
_FORMAT_INFO_M = [
    0b101010000010010,  # mask 0
    0b101000100100101,  # mask 1
    0b101111001111100,  # mask 2
    0b101101101001011,  # mask 3
    0b100010111111001,  # mask 4
    0b100000011001110,  # mask 5
    0b100111110010111,  # mask 6
    0b100101010100000,  # mask 7
]

# Precomputed 18-bit version info for versions 7..10
_VERSION_INFO = {
    7:  0x07C94,
    8:  0x085BC,
    9:  0x09A99,
    10: 0x0A4D3,
}


def _select_version(payload_len: int) -> int:
    for ver in range(1, 11):
        total_dc = _VERSION_SPECS_M[ver][0]
        char_count_bits = 8 if ver <= 9 else 16
        # 4 bits mode + char_count_bits + 8 * payload_len + up to 4 terminator bits
        needed_bits = 4 + char_count_bits + 8 * payload_len
        if needed_bits <= total_dc * 8:
            return ver
    raise ValueError(f"QR payload too long ({payload_len} bytes; max 213 bytes).")


def _encode_codewords(data_bytes: bytes, version: int) -> list[int]:
    total_dc, ec_len, g1_blocks, g1_dc, g2_blocks, g2_dc, _ = _VERSION_SPECS_M[version]
    char_count_bits = 8 if version <= 9 else 16

    bits: list[int] = []

    def push_bits(val: int, width: int):
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)

    # Byte mode indicator 0100
    push_bits(0b0100, 4)
    push_bits(len(data_bytes), char_count_bits)
    for b in data_bytes:
        push_bits(b, 8)

    capacity_bits = total_dc * 8
    terminator = min(4, capacity_bits - len(bits))
    push_bits(0, terminator)
    while len(bits) % 8 != 0:
        bits.append(0)

    data_cw = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        data_cw.append(byte)

    pad_bytes = [0xEC, 0x11]
    idx = 0
    while len(data_cw) < total_dc:
        data_cw.append(pad_bytes[idx % 2])
        idx += 1

    # Split into blocks and compute RS ECC
    data_blocks = []
    ec_blocks = []
    offset = 0
    for count, block_dc in ((g1_blocks, g1_dc), (g2_blocks, g2_dc)):
        for _ in range(count):
            blk = data_cw[offset:offset + block_dc]
            offset += block_dc
            data_blocks.append(blk)
            ec_blocks.append(_rs_remainder(blk, ec_len))

    # Interleave data codewords then ECC codewords
    interleaved = []
    max_dc = max(len(b) for b in data_blocks)
    for i in range(max_dc):
        for blk in data_blocks:
            if i < len(blk):
                interleaved.append(blk[i])
    for i in range(ec_len):
        for blk in ec_blocks:
            interleaved.append(blk[i])
    return interleaved


def _build_base_matrix(version: int):
    size = 17 + 4 * version
    modules = [[False] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, val: bool):
        modules[r][c] = val
        is_func[r][c] = True

    def draw_finder(top: int, left: int):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top + dr, left + dc
                if 0 <= r < size and 0 <= c < size:
                    if 0 <= dr <= 6 and 0 <= dc <= 6:
                        val = (
                            dr in (0, 6)
                            or dc in (0, 6)
                            or (2 <= dr <= 4 and 2 <= dc <= 4)
                        )
                    else:
                        val = False
                    set_func(r, c, val)

    draw_finder(0, 0)
    draw_finder(0, size - 7)
    draw_finder(size - 7, 0)

    # Alignment patterns
    centers = _VERSION_SPECS_M[version][6]
    for r0 in centers:
        for c0 in centers:
            if is_func[r0][c0]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    val = max(abs(dr), abs(dc)) != 1
                    set_func(r0 + dr, c0 + dc, val)

    # Timing patterns
    for i in range(8, size - 8):
        val = (i % 2 == 0)
        if not is_func[6][i]:
            set_func(6, i, val)
        if not is_func[i][6]:
            set_func(i, 6, val)

    # Dark module
    set_func(size - 8, 8, True)

    # Reserve format info areas
    for i in range(9):
        if i != 6:
            is_func[8][i] = True
            is_func[i][8] = True
    for i in range(8):
        is_func[8][size - 1 - i] = True
        if i < 7:
            is_func[size - 1 - i][8] = True

    # Version info for v >= 7
    if version >= 7:
        v_bits = _VERSION_INFO[version]
        for i in range(18):
            bit = bool((v_bits >> i) & 1)
            a = size - 11 + (i % 3)
            b = i // 3
            set_func(a, b, bit)
            set_func(b, a, bit)

    return modules, is_func


def _place_data_bits(modules, is_func, codewords: list[int]):
    size = len(modules)
    all_bits = []
    for cw in codewords:
        for s in range(7, -1, -1):
            all_bits.append((cw >> s) & 1)

    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        row_range = range(size - 1, -1, -1) if upward else range(size)
        for row in row_range:
            for c in (col, col - 1):
                if not is_func[row][c]:
                    if bit_idx < len(all_bits):
                        modules[row][c] = bool(all_bits[bit_idx])
                        bit_idx += 1
                    else:
                        modules[row][c] = False
        upward = not upward
        col -= 2


def _apply_mask(modules, is_func, mask_id: int):
    size = len(modules)
    masked = [row[:] for row in modules]

    def cond(r: int, c: int) -> bool:
        if mask_id == 0:
            return (r + c) % 2 == 0
        if mask_id == 1:
            return r % 2 == 0
        if mask_id == 2:
            return c % 3 == 0
        if mask_id == 3:
            return (r + c) % 3 == 0
        if mask_id == 4:
            return (r // 2 + c // 3) % 2 == 0
        if mask_id == 5:
            return ((r * c) % 2 + (r * c) % 3) == 0
        if mask_id == 6:
            return (((r * c) % 2 + (r * c) % 3) % 2) == 0
        return (((r + c) % 2 + (r * c) % 3) % 2) == 0

    for r in range(size):
        for c in range(size):
            if not is_func[r][c] and cond(r, c):
                masked[r][c] = not masked[r][c]

    # Write format info
    fmt = _FORMAT_INFO_M[mask_id]
    for i in range(15):
        bit = bool((fmt >> i) & 1)
        # Copy 1
        if i < 6:
            masked[8][i] = bit
        elif i < 8:
            masked[8][i + 1] = bit
        elif i == 8:
            masked[7][8] = bit
        else:
            masked[14 - i][8] = bit

        # Copy 2
        if i < 8:
            masked[size - 1 - i][8] = bit
        else:
            masked[8][size - 15 + i] = bit

    return masked


def _penalty_score(grid) -> int:
    size = len(grid)
    score = 0

    # Rule 1: 5+ consecutive same color in rows/cols
    for r in range(size):
        run = 1
        for c in range(1, size):
            if grid[r][c] == grid[r][c - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)

    for c in range(size):
        run = 1
        for r in range(1, size):
            if grid[r][c] == grid[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)

    # Rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            v = grid[r][c]
            if grid[r][c + 1] == v and grid[r + 1][c] == v and grid[r + 1][c + 1] == v:
                score += 3

    # Rule 3: dark ratio penalty
    dark = sum(1 for row in grid for v in row if v)
    pct = (dark * 100) // (size * size)
    score += (abs(pct - 50) // 5) * 10
    return score


def generate_qr_matrix(text: str) -> list[list[bool]]:
    """Return a 2D boolean matrix (True = dark module) for the given text."""
    data_bytes = text.encode('utf-8')
    version = _select_version(len(data_bytes))
    codewords = _encode_codewords(data_bytes, version)
    modules, is_func = _build_base_matrix(version)
    _place_data_bits(modules, is_func, codewords)

    best_grid = None
    best_score = None
    for mask_id in range(8):
        candidate = _apply_mask(modules, is_func, mask_id)
        score = _penalty_score(candidate)
        if best_score is None or score < best_score:
            best_score = score
            best_grid = candidate
    return best_grid


def generate_qr_svg(text: str, box_size: int = 8, border: int = 4) -> str:
    """Render QR code as a crisp standalone SVG string."""
    matrix = generate_qr_matrix(text)
    dim = len(matrix)
    total = (dim + 2 * border) * box_size
    paths = []
    for r, row in enumerate(matrix):
        y = (r + border) * box_size
        for c, val in enumerate(row):
            if val:
                x = (c + border) * box_size
                paths.append(f"M{x},{y}h{box_size}v{box_size}h-{box_size}z")
    path_data = ''.join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" shape-rendering="crispEdges">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<path d="{path_data}" fill="#0f172a"/>'
        f'</svg>'
    )


def generate_qr_png(text: str, box_size: int = 10, border: int = 4) -> bytes:
    """Render QR code as a lossless grayscale PNG using Python stdlib only."""
    matrix = generate_qr_matrix(text)
    dim = len(matrix)
    img_size = (dim + 2 * border) * box_size

    raw_rows = bytearray()
    for py in range(img_size):
        raw_rows.append(0)  # PNG filter type 0
        mr = (py // box_size) - border
        row_data = matrix[mr] if 0 <= mr < dim else None
        for px in range(img_size):
            mc = (px // box_size) - border
            is_dark = bool(row_data and 0 <= mc < dim and row_data[mc])
            raw_rows.append(15 if is_dark else 255)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', crc)

    ihdr = struct.pack('>IIBBBBB', img_size, img_size, 8, 0, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', idat)
        + chunk(b'IEND', b'')
    )
