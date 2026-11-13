"""Tests for dynamic VHD reader support in legacyrescue/image.py.

This file is extended incrementally as the dynamic VHD reader feature is
implemented; see .kiro/specs/dynamic-vhd-reader/tasks.md.
"""
import collections
import io
import os
import random
import re
import struct
import tempfile
import unittest
from legacyrescue import image, inventory, runbook, report
from tests.fixtures import ReadRecordingFile


class TestReadHeadTailWindows(unittest.TestCase):
    """Unit tests for read_head_tail_windows(f, size).

    Validates: Requirements 2.1
    """

    def test_size_below_512_tail_is_none(self):
        """A file shorter than 512 bytes has no trailing-footer window."""
        data = b"A" * 100
        f = io.BytesIO(data)
        head, tail = image.read_head_tail_windows(f, len(data))
        self.assertEqual(head, data)
        self.assertIsNone(tail)

    def test_size_in_overlap_range_head_and_tail_independent(self):
        """For 512 <= size < 1024, head and tail overlap in the file but
        must be read independently (regression guard for tail = head bug)."""
        size = 700
        data = bytes(range(256)) * (size // 256 + 1)
        data = data[:size]
        f = io.BytesIO(data)
        head, tail = image.read_head_tail_windows(f, size)
        self.assertEqual(len(head), 512)
        self.assertEqual(len(tail), 512)
        self.assertEqual(head, data[:512])
        self.assertEqual(tail, data[size - 512:size])
        # The two windows overlap in the underlying file but are not aliased:
        # tail must not simply equal head.
        self.assertNotEqual(head, tail)

    def test_size_at_or_above_1024_no_overlap(self):
        """For size >= 1024, head and tail cover disjoint byte ranges."""
        size = 2000
        data = bytes((i % 256) for i in range(size))
        f = io.BytesIO(data)
        head, tail = image.read_head_tail_windows(f, size)
        self.assertEqual(head, data[:512])
        self.assertEqual(tail, data[size - 512:size])
        self.assertNotEqual(head, tail)


class TestClassifyFooterCandidate(unittest.TestCase):
    """Unit tests for classify_footer_candidate(window).

    Validates: Requirements 2.1
    """

    def _footer_window(self, cookie=b"conectix", disk_type=3):
        """Build a 512-byte window with the given cookie and disk_type
        placed at their documented offsets (0 and 60 respectively)."""
        buf = bytearray(512)
        buf[0:8] = cookie
        buf[60:64] = struct.pack(">I", disk_type)
        return bytes(buf)

    def test_empty_window(self):
        """An empty window has no cookie and no disk type."""
        result = image.classify_footer_candidate(b"")
        self.assertEqual(result, {"cookie_ok": False, "disk_type": None})

    def test_short_window_below_cookie_length(self):
        """A window shorter than 8 bytes cannot match the cookie."""
        result = image.classify_footer_candidate(b"con")
        self.assertEqual(result, {"cookie_ok": False, "disk_type": None})

    def test_short_window_with_cookie_but_no_disk_type_field(self):
        """A window with the cookie but shorter than 64 bytes has cookie_ok
        True but disk_type None (field not present in window)."""
        result = image.classify_footer_candidate(b"conectix" + b"\x00" * 10)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": None})

    def test_window_without_cookie(self):
        """A full-length window without the conectix cookie is cookie_ok
        False, but disk_type is still read raw from offset 60."""
        window = self._footer_window(cookie=b"XXXXXXXX", disk_type=3)
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": False, "disk_type": 3})

    def test_window_with_cookie_disk_type_fixed(self):
        """cookie present, disk_type 2 (fixed) read correctly."""
        window = self._footer_window(disk_type=2)
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": 2})

    def test_window_with_cookie_disk_type_dynamic(self):
        """cookie present, disk_type 3 (dynamic) read correctly."""
        window = self._footer_window(disk_type=3)
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": 3})

    def test_window_with_cookie_disk_type_differencing(self):
        """cookie present, disk_type 4 (differencing) read correctly."""
        window = self._footer_window(disk_type=4)
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": 4})

    def test_window_with_cookie_disk_type_other(self):
        """cookie present, an out-of-set disk_type value is still read
        raw -- classify_footer_candidate performs no structural validation."""
        window = self._footer_window(disk_type=999)
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": 999})

    def test_window_exactly_64_bytes(self):
        """A window of exactly 64 bytes still contains the full disk_type
        field (offset 60, length 4) and should be read."""
        full = self._footer_window(disk_type=3)
        window = full[:64]
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": 3})

    def test_window_63_bytes_disk_type_none(self):
        """A window of 63 bytes is one byte short of the full disk_type
        field and must report disk_type None rather than reading a
        truncated/garbage value."""
        full = self._footer_window(disk_type=3)
        window = full[:63]
        result = image.classify_footer_candidate(window)
        self.assertEqual(result, {"cookie_ok": True, "disk_type": None})


class TestParseFooter(unittest.TestCase):
    """Unit test for parse_footer(raw).

    Validates: Requirements 2.1
    """

    def test_parse_footer_known_values(self):
        """Hand-construct a 512-byte footer buffer with known values at
        each documented offset and confirm parse_footer unpacks all of
        them correctly, performing no validation."""
        buf = bytearray(512)
        buf[0:8] = b"conectix"
        struct.pack_into(">I", buf, 8, 0x00000002)          # features
        struct.pack_into(">I", buf, 12, 0x00010000)          # fileFormatVersion
        struct.pack_into(">Q", buf, 16, 0x0000000000000600)  # dataOffset
        struct.pack_into(">I", buf, 24, 123456789)           # timestamp
        buf[28:32] = b"vpc "                                 # creatorApplication
        struct.pack_into(">I", buf, 32, 0x00050003)          # creatorVersion
        buf[36:40] = b"Wi2k"                                 # creatorHostOS
        struct.pack_into(">Q", buf, 40, 1024 * 1024 * 50)     # originalSize
        struct.pack_into(">Q", buf, 48, 1024 * 1024 * 50)     # currentSize
        buf[56:60] = bytes([0x00, 0x64, 0x10, 0x3F])          # diskGeometry
        struct.pack_into(">I", buf, 60, 3)                   # diskType = dynamic
        struct.pack_into(">I", buf, 64, 0xDEADBEEF)          # checksum
        buf[68:84] = bytes(range(16))                        # uniqueId
        buf[84] = 0x01                                       # savedState
        buf[85:512] = b"\x00" * (512 - 85)                   # reserved

        parsed = image.parse_footer(bytes(buf))

        self.assertEqual(parsed["cookie"], b"conectix")
        self.assertEqual(parsed["features"], 0x00000002)
        self.assertEqual(parsed["fileFormatVersion"], 0x00010000)
        self.assertEqual(parsed["dataOffset"], 0x0000000000000600)
        self.assertEqual(parsed["timestamp"], 123456789)
        self.assertEqual(parsed["creatorApplication"], b"vpc ")
        self.assertEqual(parsed["creatorVersion"], 0x00050003)
        self.assertEqual(parsed["creatorHostOS"], b"Wi2k")
        self.assertEqual(parsed["originalSize"], 1024 * 1024 * 50)
        self.assertEqual(parsed["currentSize"], 1024 * 1024 * 50)
        self.assertEqual(parsed["diskGeometry"], bytes([0x00, 0x64, 0x10, 0x3F]))
        self.assertEqual(parsed["diskType"], 3)
        self.assertEqual(parsed["checksum"], 0xDEADBEEF)
        self.assertEqual(parsed["uniqueId"], bytes(range(16)))
        self.assertEqual(parsed["savedState"], 0x01)
        self.assertEqual(parsed["reserved"], b"\x00" * (512 - 85))


class TestFooterValidation(unittest.TestCase):
    """Unit tests for validate_footer_checksum and validate_footer_structure.

    Validates: Requirements 2.6, 2.1
    """

    def _build_footer(self, cookie=b"conectix", disk_type=3):
        """Build a 512-byte footer buffer with a correctly-computed
        checksum, using known values at the documented offsets."""
        buf = bytearray(512)
        buf[0:8] = cookie
        struct.pack_into(">I", buf, 8, 0x00000002)           # features
        struct.pack_into(">I", buf, 12, 0x00010000)          # fileFormatVersion
        struct.pack_into(">Q", buf, 16, 0x0000000000000600)  # dataOffset
        struct.pack_into(">I", buf, 24, 123456789)           # timestamp
        buf[28:32] = b"vpc "                                 # creatorApplication
        struct.pack_into(">I", buf, 32, 0x00050003)          # creatorVersion
        buf[36:40] = b"Wi2k"                                 # creatorHostOS
        struct.pack_into(">Q", buf, 40, 1024 * 1024 * 50)    # originalSize
        struct.pack_into(">Q", buf, 48, 1024 * 1024 * 50)    # currentSize
        buf[56:60] = bytes([0x00, 0x64, 0x10, 0x3F])         # diskGeometry
        struct.pack_into(">I", buf, 60, disk_type)           # diskType
        struct.pack_into(">I", buf, 64, 0)                   # checksum placeholder
        buf[68:84] = bytes(range(16))                        # uniqueId
        buf[84] = 0x01                                       # savedState
        buf[85:512] = b"\x00" * (512 - 85)                   # reserved

        checksum = (~sum(buf)) & 0xFFFFFFFF
        struct.pack_into(">I", buf, 64, checksum)
        return bytes(buf)

    def test_valid_footer_round_trips_with_no_error(self):
        """A valid, correctly-checksummed footer passes parse, checksum,
        and structure validation with no exception raised."""
        raw = self._build_footer()
        parsed = image.parse_footer(raw)
        image.validate_footer_checksum(raw, parsed)
        image.validate_footer_structure(parsed)

    def test_corrupted_checksum_raises_value_error(self):
        """Flipping a byte in the footer without updating the checksum
        causes validate_footer_checksum to raise ValueError."""
        raw = bytearray(self._build_footer())
        raw[40] ^= 0xFF  # corrupt originalSize, checksum now stale
        parsed = image.parse_footer(bytes(raw))
        with self.assertRaises(ValueError):
            image.validate_footer_checksum(bytes(raw), parsed)

    def test_wrong_cookie_raises_value_error(self):
        """A footer whose cookie is not b'conectix' is rejected by
        validate_footer_structure."""
        raw = self._build_footer(cookie=b"XXXXXXXX")
        parsed = image.parse_footer(raw)
        with self.assertRaises(ValueError):
            image.validate_footer_structure(parsed)

    def test_out_of_set_disk_type_raises_value_error(self):
        """A footer whose diskType is not in {2, 3, 4} is rejected by
        validate_footer_structure."""
        raw = self._build_footer(disk_type=999)
        parsed = image.parse_footer(raw)
        with self.assertRaises(ValueError):
            image.validate_footer_structure(parsed)


def _build_footer_full(cookie=b"conectix", disk_type=3, unique_id=None,
                        original_size=1024 * 1024 * 50, current_size=1024 * 1024 * 50,
                        data_offset=0x0000000000000600):
    """Build a 512-byte footer buffer with a correctly-computed checksum,
    allowing the identity fields used by validate_footer_pair_consistent
    (diskType, uniqueId, originalSize, currentSize, dataOffset) to be
    independently overridden. Mirrors TestFooterValidation._build_footer
    but parameterized for the five identity fields."""
    if unique_id is None:
        unique_id = bytes(range(16))
    buf = bytearray(512)
    buf[0:8] = cookie
    struct.pack_into(">I", buf, 8, 0x00000002)           # features
    struct.pack_into(">I", buf, 12, 0x00010000)          # fileFormatVersion
    struct.pack_into(">Q", buf, 16, data_offset)         # dataOffset
    struct.pack_into(">I", buf, 24, 123456789)           # timestamp
    buf[28:32] = b"vpc "                                 # creatorApplication
    struct.pack_into(">I", buf, 32, 0x00050003)          # creatorVersion
    buf[36:40] = b"Wi2k"                                 # creatorHostOS
    struct.pack_into(">Q", buf, 40, original_size)       # originalSize
    struct.pack_into(">Q", buf, 48, current_size)        # currentSize
    buf[56:60] = bytes([0x00, 0x64, 0x10, 0x3F])         # diskGeometry
    struct.pack_into(">I", buf, 60, disk_type)           # diskType
    struct.pack_into(">I", buf, 64, 0)                   # checksum placeholder
    buf[68:84] = unique_id                               # uniqueId
    buf[84] = 0x01                                        # savedState
    buf[85:512] = b"\x00" * (512 - 85)                   # reserved

    checksum = (~sum(buf)) & 0xFFFFFFFF
    struct.pack_into(">I", buf, 64, checksum)
    return bytes(buf)


class TestFooterPairConsistent(unittest.TestCase):
    """Unit tests for validate_footer_pair_consistent(footer_a, footer_b).

    Validates: Requirements 2.6
    """

    def test_matching_pair_both_type_3_passes(self):
        """Two footers that agree on all five identity fields and both
        report diskType == 3 pass with no exception."""
        raw_a = _build_footer_full()
        raw_b = _build_footer_full()
        footer_a = image.parse_footer(raw_a)
        footer_b = image.parse_footer(raw_b)
        image.validate_footer_pair_consistent(footer_a, footer_b)

    def test_mismatched_data_offset_with_valid_checksums_rejected(self):
        """Bug 2 regression: two footers each have independently-valid
        checksums (each footer's own checksum was computed over its own
        bytes, including its own dataOffset), but their dataOffset values
        differ. Checksum equality/validity must not be treated as a
        proxy for field agreement -- this must be rejected."""
        raw_a = _build_footer_full(data_offset=0x0000000000000600)
        raw_b = _build_footer_full(data_offset=0x0000000000000A00)
        footer_a = image.parse_footer(raw_a)
        footer_b = image.parse_footer(raw_b)
        # Each footer's own checksum is independently valid.
        image.validate_footer_checksum(raw_a, footer_a)
        image.validate_footer_checksum(raw_b, footer_b)
        with self.assertRaises(ValueError):
            image.validate_footer_pair_consistent(footer_a, footer_b)

    def test_agreeing_pair_both_non_type_3_rejected(self):
        """A pair that agrees on all five fields but both report a
        non-3 disk type (here, 2 == fixed) is rejected, even though the
        mutual-agreement check alone would pass."""
        raw_a = _build_footer_full(disk_type=2)
        raw_b = _build_footer_full(disk_type=2)
        footer_a = image.parse_footer(raw_a)
        footer_b = image.parse_footer(raw_b)
        with self.assertRaises(ValueError):
            image.validate_footer_pair_consistent(footer_a, footer_b)


class TestFooterPairConsistentProperty(unittest.TestCase):
    """Property test for validate_footer_pair_consistent.

    Feature: dynamic-vhd-reader, Property 10: Footer-pair field
    consistency is enforced across all five identity fields, and both
    copies must individually be type 3

    Validates: Requirements 2.6
    """

    IDENTITY_FIELDS = ("diskType", "uniqueId", "originalSize", "currentSize", "dataOffset")

    def _base_kwargs(self):
        return {
            "disk_type": 3,
            "unique_id": bytes(range(16)),
            "original_size": 1024 * 1024 * 50,
            "current_size": 1024 * 1024 * 50,
            "data_offset": 0x0000000000000600,
        }

    def _alternate_value(self, field, rng):
        """Produce a value for `field` guaranteed to differ from the base
        value used in _base_kwargs()."""
        if field == "diskType":
            return rng.choice([2, 4, 5, 7])
        if field == "uniqueId":
            return bytes((rng.randrange(256) for _ in range(16)))
        if field == "originalSize":
            return 1024 * 1024 * 50 + rng.randrange(1, 1000)
        if field == "currentSize":
            return 1024 * 1024 * 50 + rng.randrange(1, 1000)
        if field == "dataOffset":
            return 0x0000000000000600 + rng.randrange(1, 1000) * 512
        raise AssertionError("unknown field %r" % (field,))

    def test_varying_each_identity_field_independently_is_rejected(self):
        """For each of the five identity fields, generate a case where
        exactly that field differs between the two footer copies (all
        others equal, each copy's own checksum valid) and confirm
        validate_footer_pair_consistent rejects it. Uses a fixed-seed
        random.Random per design.md's Testing Strategy for Property 10."""
        rng = random.Random(20240610)
        field_to_kwarg = {
            "diskType": "disk_type",
            "uniqueId": "unique_id",
            "originalSize": "original_size",
            "currentSize": "current_size",
            "dataOffset": "data_offset",
        }
        for field in self.IDENTITY_FIELDS:
            for _ in range(5):  # a modest number of generated cases per field
                kwargs_a = self._base_kwargs()
                kwargs_b = dict(kwargs_a)
                kwarg_name = field_to_kwarg[field]
                kwargs_b[kwarg_name] = self._alternate_value(field, rng)

                raw_a = _build_footer_full(**kwargs_a)
                raw_b = _build_footer_full(**kwargs_b)
                footer_a = image.parse_footer(raw_a)
                footer_b = image.parse_footer(raw_b)

                # Each copy's own checksum must be valid in isolation.
                image.validate_footer_checksum(raw_a, footer_a)
                image.validate_footer_checksum(raw_b, footer_b)

                with self.assertRaises(ValueError):
                    image.validate_footer_pair_consistent(footer_a, footer_b)

    def test_all_fields_equal_and_both_type_3_accepted(self):
        """Baseline: when every identity field matches and both copies
        are diskType == 3, the pair is accepted (control case for the
        property above)."""
        kwargs = self._base_kwargs()
        raw_a = _build_footer_full(**kwargs)
        raw_b = _build_footer_full(**kwargs)
        footer_a = image.parse_footer(raw_a)
        footer_b = image.parse_footer(raw_b)
        image.validate_footer_pair_consistent(footer_a, footer_b)

    def test_all_fields_equal_but_not_type_3_rejected(self):
        """Baseline: when every identity field matches but diskType is
        not 3 for either copy, the pair is still rejected by the
        independent both-type-3 check."""
        rng = random.Random(20240611)
        for _ in range(5):
            non_three = rng.choice([2, 4, 5, 255])
            kwargs = self._base_kwargs()
            kwargs["disk_type"] = non_three
            raw_a = _build_footer_full(**kwargs)
            raw_b = _build_footer_full(**kwargs)
            footer_a = image.parse_footer(raw_a)
            footer_b = image.parse_footer(raw_b)
            with self.assertRaises(ValueError):
                image.validate_footer_pair_consistent(footer_a, footer_b)


def _build_header_full(cookie=b"cxsparse", header_version=0x00010000, table_offset=1536,
                        max_table_entries=4, block_size=2 * 1024 * 1024,
                        data_offset=0xFFFFFFFFFFFFFFFF):
    """Build a 1024-byte header buffer with a correctly-computed checksum,
    parameterized over cookie/headerVersion/tableOffset/maxTableEntries/
    blockSize so corruption tests can override one field at a time.
    Mirrors _build_footer_full but for the HeaderFields layout."""
    buf = bytearray(1024)
    buf[0:8] = cookie
    struct.pack_into(">Q", buf, 8, data_offset)          # dataOffset (reserved)
    struct.pack_into(">Q", buf, 16, table_offset)        # tableOffset
    struct.pack_into(">I", buf, 24, header_version)      # headerVersion
    struct.pack_into(">I", buf, 28, max_table_entries)   # maxTableEntries
    struct.pack_into(">I", buf, 32, block_size)          # blockSize
    struct.pack_into(">I", buf, 36, 0)                   # checksum placeholder
    buf[40:56] = bytes(16)                               # parentUniqueId
    struct.pack_into(">I", buf, 56, 0)                   # parentTimestamp
    buf[60:64] = bytes(4)                                # reserved1
    buf[64:576] = bytes(512)                             # parentUnicodeName
    buf[576:768] = bytes(192)                            # parentLocatorEntries
    buf[768:1024] = bytes(256)                           # reserved2

    checksum = (~sum(buf)) & 0xFFFFFFFF
    struct.pack_into(">I", buf, 36, checksum)
    return bytes(buf)


class TestParseHeader(unittest.TestCase):
    """Unit test for parse_header(raw).

    Validates: Requirements 2.2
    """

    def test_parse_header_known_values(self):
        """Hand-construct a 1024-byte header buffer with known values at
        each documented offset and confirm parse_header unpacks all of
        them correctly, performing no validation."""
        buf = bytearray(1024)
        buf[0:8] = b"cxsparse"
        struct.pack_into(">Q", buf, 8, 0xFFFFFFFFFFFFFFFF)   # dataOffset
        struct.pack_into(">Q", buf, 16, 1536)                # tableOffset
        struct.pack_into(">I", buf, 24, 0x00010000)          # headerVersion
        struct.pack_into(">I", buf, 28, 4)                   # maxTableEntries
        struct.pack_into(">I", buf, 32, 2 * 1024 * 1024)     # blockSize
        struct.pack_into(">I", buf, 36, 0xDEADBEEF)          # checksum
        buf[40:56] = bytes(range(16))                        # parentUniqueId
        struct.pack_into(">I", buf, 56, 123456789)           # parentTimestamp
        buf[60:64] = b"\x00\x00\x00\x00"                     # reserved1
        buf[64:576] = b"\x01" * 512                          # parentUnicodeName
        buf[576:768] = b"\x02" * 192                         # parentLocatorEntries
        buf[768:1024] = b"\x03" * 256                        # reserved2

        parsed = image.parse_header(bytes(buf))

        self.assertEqual(parsed["cookie"], b"cxsparse")
        self.assertEqual(parsed["dataOffset"], 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(parsed["tableOffset"], 1536)
        self.assertEqual(parsed["headerVersion"], 0x00010000)
        self.assertEqual(parsed["maxTableEntries"], 4)
        self.assertEqual(parsed["blockSize"], 2 * 1024 * 1024)
        self.assertEqual(parsed["checksum"], 0xDEADBEEF)
        self.assertEqual(parsed["parentUniqueId"], bytes(range(16)))
        self.assertEqual(parsed["parentTimestamp"], 123456789)
        self.assertEqual(parsed["reserved1"], b"\x00\x00\x00\x00")
        self.assertEqual(parsed["parentUnicodeName"], b"\x01" * 512)
        self.assertEqual(parsed["parentLocatorEntries"], b"\x02" * 192)
        self.assertEqual(parsed["reserved2"], b"\x03" * 256)


class TestHeaderValidation(unittest.TestCase):
    """Unit tests for validate_header_span, validate_header_checksum, and
    validate_header_structure.

    Validates: Requirements 2.2
    """

    def test_valid_header_passes_all_four_checks(self):
        """A valid header offset/buffer passes span, checksum, and
        structure validation with no exception raised."""
        header_offset = 1024
        file_size = 1024 * 1024
        footer_intervals = [(0, 512), (file_size - 512, file_size)]
        raw = _build_header_full()
        image.validate_header_span(header_offset, file_size, footer_intervals)
        parsed = image.parse_header(raw)
        image.validate_header_checksum(raw, parsed)
        image.validate_header_structure(parsed)

    def test_header_offset_not_sector_aligned_rejected(self):
        """A header offset that is not a multiple of 512 is rejected."""
        file_size = 1024 * 1024
        footer_intervals = [(0, 512), (file_size - 512, file_size)]
        with self.assertRaises(ValueError):
            image.validate_header_span(1025, file_size, footer_intervals)

    def test_header_span_overlapping_footer_rejected(self):
        """A header span that overlaps a footer interval is rejected."""
        file_size = 1024 * 1024
        # header at offset 512 would occupy [512, 1536), overlapping a
        # footer interval of [512, 1024).
        footer_intervals = [(0, 512), (512, 1024)]
        with self.assertRaises(ValueError):
            image.validate_header_span(512, file_size, footer_intervals)

    def test_header_corrupted_checksum_rejected(self):
        """Flipping a byte in the header without updating the checksum
        causes validate_header_checksum to raise ValueError."""
        raw = bytearray(_build_header_full())
        raw[16] ^= 0xFF  # corrupt tableOffset, checksum now stale
        parsed = image.parse_header(bytes(raw))
        with self.assertRaises(ValueError):
            image.validate_header_checksum(bytes(raw), parsed)

    def test_wrong_cookie_rejected(self):
        """A header whose cookie is not b'cxsparse' is rejected by
        validate_header_structure."""
        raw = _build_header_full(cookie=b"XXXXXXXX")
        parsed = image.parse_header(raw)
        with self.assertRaises(ValueError):
            image.validate_header_structure(parsed)

    def test_wrong_header_version_rejected(self):
        """A header whose headerVersion is not 0x00010000 is rejected."""
        raw = _build_header_full(header_version=0x00020000)
        parsed = image.parse_header(raw)
        with self.assertRaises(ValueError):
            image.validate_header_structure(parsed)

    def test_table_offset_not_sector_aligned_rejected(self):
        """A header whose tableOffset is not sector-aligned is rejected
        by validate_header_structure."""
        raw = _build_header_full(table_offset=1537)
        parsed = image.parse_header(raw)
        with self.assertRaises(ValueError):
            image.validate_header_structure(parsed)

    def test_table_offset_below_minimum_rejected(self):
        """A header whose tableOffset is below 512 is rejected by
        validate_header_structure."""
        raw = _build_header_full(table_offset=0)
        parsed = image.parse_header(raw)
        with self.assertRaises(ValueError):
            image.validate_header_structure(parsed)


class TestInvalidFooterHeaderRejectedProperty(unittest.TestCase):
    """Property test for invalid footer/header rejection before use.

    Feature: dynamic-vhd-reader, Property 6: Invalid footer/header is
    always rejected before use

    Validates: Requirements 2.1, 2.2
    """

    def test_single_field_corruption_rejected_across_footer_and_header(self):
        """Generate cases corrupting one field at a time across footer
        and header (reusing _build_footer_full and _build_header_full),
        confirming the appropriate validate_* function raises ValueError.
        Uses random.Random with a fixed seed."""
        rng = random.Random(20240701)

        # Footer-side corruptions: (builder kwargs override, validator)
        def check_footer_cookie(rng):
            raw = _build_footer_full(cookie=b"NOTVALID")
            parsed = image.parse_footer(raw)
            with self.assertRaises(ValueError):
                image.validate_footer_structure(parsed)

        def check_footer_disk_type(rng):
            bad_type = rng.choice([0, 1, 5, 6, 255])
            raw = _build_footer_full(disk_type=bad_type)
            parsed = image.parse_footer(raw)
            with self.assertRaises(ValueError):
                image.validate_footer_structure(parsed)

        def check_footer_checksum(rng):
            raw = bytearray(_build_footer_full())
            byte_index = rng.randrange(0, 512)
            raw[byte_index] ^= 0xFF
            parsed = image.parse_footer(bytes(raw))
            # Only assert when the flip actually changed a byte outside
            # the checksum field's own bytes being re-read identically;
            # since checksum is derived from the whole buffer, any flip
            # not in [64:68] will invalidate it, and a flip inside [64:68]
            # directly corrupts the stored checksum value itself -- both
            # cases must raise.
            with self.assertRaises(ValueError):
                image.validate_footer_checksum(bytes(raw), parsed)

        def check_header_cookie(rng):
            raw = _build_header_full(cookie=b"NOTVALID")
            parsed = image.parse_header(raw)
            with self.assertRaises(ValueError):
                image.validate_header_structure(parsed)

        def check_header_version(rng):
            bad_version = rng.choice([0x0, 0x00010001, 0x00020000, 0xFFFFFFFF])
            raw = _build_header_full(header_version=bad_version)
            parsed = image.parse_header(raw)
            with self.assertRaises(ValueError):
                image.validate_header_structure(parsed)

        def check_header_table_offset(rng):
            bad_offset = rng.choice([0, 256, 511, 1025, 2047])
            raw = _build_header_full(table_offset=bad_offset)
            parsed = image.parse_header(raw)
            with self.assertRaises(ValueError):
                image.validate_header_structure(parsed)

        def check_header_checksum(rng):
            raw = bytearray(_build_header_full())
            byte_index = rng.randrange(0, 1024)
            raw[byte_index] ^= 0xFF
            parsed = image.parse_header(bytes(raw))
            with self.assertRaises(ValueError):
                image.validate_header_checksum(bytes(raw), parsed)

        def check_header_span_not_aligned(rng):
            bad_offset = rng.randrange(1, 10) * 512 + rng.randrange(1, 511)
            file_size = 1024 * 1024
            footer_intervals = [(0, 512), (file_size - 512, file_size)]
            with self.assertRaises(ValueError):
                image.validate_header_span(bad_offset, file_size, footer_intervals)

        def check_header_span_overlaps_footer(rng):
            file_size = 1024 * 1024
            footer_intervals = [(0, 512), (file_size - 512, file_size)]
            # header at offset 0 overlaps leading footer [0, 512)
            with self.assertRaises(ValueError):
                image.validate_header_span(0, file_size, footer_intervals)

        checks = [
            check_footer_cookie,
            check_footer_disk_type,
            check_footer_checksum,
            check_header_cookie,
            check_header_version,
            check_header_table_offset,
            check_header_checksum,
            check_header_span_not_aligned,
            check_header_span_overlaps_footer,
        ]

        for _ in range(3):
            for check in checks:
                check(rng)


class TestValidateBlockSize(unittest.TestCase):
    """Unit tests for validate_block_size(block_size).

    Validates: Requirements 2.3
    """

    def test_zero_rejected(self):
        with self.assertRaises(ValueError):
            image.validate_block_size(0)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            image.validate_block_size(-512)

    def test_non_power_of_two_rejected(self):
        """1536 is in range and sector-aligned but not a power of two."""
        with self.assertRaises(ValueError):
            image.validate_block_size(1536)

    def test_non_sector_aligned_rejected(self):
        """513 is not a multiple of SECTOR_SIZE (and not a power of two
        either, but the sector-alignment check applies independently)."""
        with self.assertRaises(ValueError):
            image.validate_block_size(513)

    def test_exactly_max_block_size_accepted(self):
        """MAX_BLOCK_SIZE (2 MiB) is a power of two, sector-aligned, and
        at the top of the valid range -- must be accepted."""
        image.validate_block_size(image.MAX_BLOCK_SIZE)

    def test_max_block_size_plus_one_rejected(self):
        """One byte above MAX_BLOCK_SIZE must be rejected."""
        with self.assertRaises(ValueError):
            image.validate_block_size(image.MAX_BLOCK_SIZE + 1)


class TestValidateBlockSizeProperty(unittest.TestCase):
    """Property test for validate_block_size.

    Feature: dynamic-vhd-reader, Property 7: Block size validation is
    independent and total

    Validates: Requirements 2.3
    """

    def _is_valid(self, block_size):
        return (
            image.MIN_BLOCK_SIZE <= block_size <= image.MAX_BLOCK_SIZE
            and (block_size & (block_size - 1)) == 0
            and block_size % image.SECTOR_SIZE == 0
        )

    def test_block_size_validation_matches_reference_predicate(self):
        """Generate cases spanning in-range, out-of-range, non-power-of-
        two, and non-sector-aligned values using a fixed-seed
        random.Random, and confirm validate_block_size raises ValueError
        if and only if the value fails the reference predicate."""
        rng = random.Random(20240801)
        candidates = []

        # In-range powers of two (valid).
        power = image.MIN_BLOCK_SIZE
        while power <= image.MAX_BLOCK_SIZE:
            candidates.append(power)
            power *= 2

        # Out-of-range values (below min, above max).
        candidates.extend([
            0, 1, -1, -image.MIN_BLOCK_SIZE,
            image.MIN_BLOCK_SIZE - 1,
            image.MAX_BLOCK_SIZE + 1,
            image.MAX_BLOCK_SIZE * 2,
        ])

        # Non-power-of-two, sector-aligned, in-range values.
        candidates.extend([1024 + 512, 2048 + 512, 3 * 512, 5 * 512])

        # Non-sector-aligned, in-range values.
        candidates.extend([513, 1023, 1025, 100000])

        # Randomly generated values across a wide range, including
        # negative and very large values.
        for _ in range(30):
            candidates.append(rng.randrange(-1000, 5 * 1024 * 1024))

        for block_size in candidates:
            expected_valid = self._is_valid(block_size)
            if expected_valid:
                image.validate_block_size(block_size)
            else:
                with self.assertRaises(ValueError):
                    image.validate_block_size(block_size)


class TestValidateVirtualSizeBoundary(unittest.TestCase):
    """Single explicit (non-randomized) unit test for the exact 256 MiB
    virtual size boundary.

    Validates: Requirements 2.4
    """

    def test_exactly_max_virtual_size_accepted(self):
        """268,435,456 (exactly 256 MiB) is sector-aligned and at the top
        of the valid range -- must be accepted."""
        image.validate_virtual_size(image.MAX_VIRTUAL_SIZE)
        self.assertEqual(image.MAX_VIRTUAL_SIZE, 268435456)

    def test_max_virtual_size_plus_one_rejected(self):
        """268,435,457 is both over the cap and not sector-aligned; must
        be rejected."""
        with self.assertRaises(ValueError):
            image.validate_virtual_size(268435456 + 1)

    def test_max_virtual_size_plus_one_sector_rejected(self):
        """268,435,456 + 512 is sector-aligned but still strictly over
        the cap; must be rejected on the range check alone."""
        with self.assertRaises(ValueError):
            image.validate_virtual_size(268435456 + 512)

    def test_in_range_non_sector_aligned_rejected(self):
        """An in-range value that is not a multiple of 512 must be
        rejected by the alignment check, independent of the range
        check."""
        with self.assertRaises(ValueError):
            image.validate_virtual_size(1024 * 1024 + 1)


class TestValidateVirtualSizeProperty(unittest.TestCase):
    """Property test for validate_virtual_size.

    Feature: dynamic-vhd-reader, Property 8: Virtual size cap is enforced
    exactly at the boundary

    Validates: Requirements 2.4
    """

    def _is_valid(self, virtual_size):
        return (
            0 < virtual_size <= image.MAX_VIRTUAL_SIZE
            and virtual_size % image.SECTOR_SIZE == 0
        )

    def test_virtual_size_validation_matches_reference_predicate_near_boundary(self):
        """Generate cases near the MAX_VIRTUAL_SIZE boundary (and a few
        far from it) using a fixed-seed random.Random, and confirm
        validate_virtual_size raises ValueError if and only if the value
        fails the reference predicate."""
        rng = random.Random(20240802)
        cap = image.MAX_VIRTUAL_SIZE
        sector = image.SECTOR_SIZE
        candidates = [
            cap, cap - sector, cap + sector, cap - 1, cap + 1,
            0, -sector, sector, sector - 1, sector + 1,
        ]
        # Randomized offsets around the boundary, both aligned and
        # misaligned.
        for _ in range(20):
            offset = rng.randrange(-5, 6) * sector + rng.randrange(-3, 4)
            candidates.append(cap + offset)
        for _ in range(10):
            candidates.append(rng.randrange(-sector, cap + 5 * sector))

        for virtual_size in candidates:
            expected_valid = self._is_valid(virtual_size)
            if expected_valid:
                image.validate_virtual_size(virtual_size)
            else:
                with self.assertRaises(ValueError):
                    image.validate_virtual_size(virtual_size)


class TestValidateBatCapacity(unittest.TestCase):
    """Unit tests for validate_bat_capacity(max_table_entries,
    virtual_size, block_size).

    Validates: Requirements 2.5, 2.6
    """

    def test_exact_value_accepted(self):
        """virtual_size / block_size divides evenly; the exact quotient
        is accepted."""
        block_size = 2 * 1024 * 1024
        virtual_size = block_size * 10
        exact = virtual_size // block_size
        image.validate_bat_capacity(exact, virtual_size, block_size)

    def test_one_less_than_exact_rejected(self):
        """One less than the exact ceil(virtual_size/block_size) value is
        rejected (not treated as ">=" acceptable)."""
        block_size = 2 * 1024 * 1024
        virtual_size = block_size * 10
        exact = -(-virtual_size // block_size)
        with self.assertRaises(ValueError):
            image.validate_bat_capacity(exact - 1, virtual_size, block_size)

    def test_one_more_than_exact_rejected(self):
        """One more than the exact ceil(virtual_size/block_size) value is
        rejected (a header claiming extra unused BAT entries is not
        tolerated)."""
        block_size = 2 * 1024 * 1024
        virtual_size = block_size * 10
        exact = -(-virtual_size // block_size)
        with self.assertRaises(ValueError):
            image.validate_bat_capacity(exact + 1, virtual_size, block_size)

    def test_non_evenly_dividing_size_requires_ceil(self):
        """When virtual_size does not divide evenly by block_size, only
        the ceiling value is accepted; floor is rejected."""
        block_size = 1024 * 1024
        virtual_size = block_size * 10 + 512  # one extra sector's worth
        floor = virtual_size // block_size
        ceil = -(-virtual_size // block_size)
        self.assertEqual(ceil, floor + 1)
        with self.assertRaises(ValueError):
            image.validate_bat_capacity(floor, virtual_size, block_size)
        image.validate_bat_capacity(ceil, virtual_size, block_size)

    def test_above_max_bat_entries_rejected_even_if_matching_formula(self):
        """A max_table_entries value above MAX_BAT_ENTRIES is rejected
        even when passed directly as a value that would (hypothetically)
        match the ceil formula for the given virtual_size/block_size --
        here we pick virtual_size/block_size so the exact ceil equals
        MAX_BAT_ENTRIES + 1, which is itself unreachable through a valid
        (capped) virtual_size/block_size combination within the 256 MiB
        cap and the documented block size range, so this test directly
        exercises validate_bat_capacity with max_table_entries passed as
        a value exceeding the cap, independent of whether that quotient
        is itself achievable end-to-end through the full pipeline."""
        # Smallest allowed block size (512) with the maximum allowed
        # virtual size (256 MiB) gives ceil(virtual_size/block_size) =
        # 268435456 / 512 = 524288 = MAX_BAT_ENTRIES exactly, which is
        # the largest reachable quotient under the documented caps. To
        # exercise a quotient of MAX_BAT_ENTRIES + 1 we construct a
        # virtual_size/block_size pair purely for the arithmetic (not
        # itself required to be independently valid under
        # validate_virtual_size/validate_block_size), confirming
        # validate_bat_capacity enforces the MAX_BAT_ENTRIES cap on its
        # own terms rather than relying on the caller to have already
        # bounded virtual_size/block_size.
        block_size = 512
        virtual_size = (image.MAX_BAT_ENTRIES + 1) * block_size
        exact = -(-virtual_size // block_size)
        self.assertEqual(exact, image.MAX_BAT_ENTRIES + 1)
        with self.assertRaises(ValueError):
            image.validate_bat_capacity(exact, virtual_size, block_size)

    def test_max_bat_entries_itself_accepted(self):
        """MAX_BAT_ENTRIES exactly (achievable with the largest cap
        combination: 256 MiB virtual size / 512-byte block size) is
        accepted."""
        block_size = image.MIN_BLOCK_SIZE
        virtual_size = image.MAX_VIRTUAL_SIZE
        exact = -(-virtual_size // block_size)
        self.assertEqual(exact, image.MAX_BAT_ENTRIES)
        image.validate_bat_capacity(exact, virtual_size, block_size)


class TestReadRecordingFile(unittest.TestCase):
    """Self-test for the read-recording file-like wrapper (tests/fixtures.py).

    This wrapper is reserved only for the pre-read-rejection proofs (tasks
    16.1 and 16.2); this test exists only to confirm the wrapper itself
    correctly records seek/read calls against a real temporary file.

    Validates: Requirements 2.5, 2.6
    """

    def test_records_offset_and_length_for_each_read(self):
        data = bytes(range(256)) * 8  # 2048 real bytes on disk
        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, data)
        os.close(fd)
        try:
            with open(path, "rb") as real_f:
                wrapper = ReadRecordingFile(real_f)
                wrapper.seek(0)
                first = wrapper.read(512)
                wrapper.seek(1024)
                second = wrapper.read(100)

            self.assertEqual(first, data[0:512])
            self.assertEqual(second, data[1024:1124])
            self.assertEqual(wrapper.reads, [(0, 512), (1024, 100)])
        finally:
            os.remove(path)

    def test_context_manager_closes_underlying_file(self):
        data = b"\x00" * 512
        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, data)
        os.close(fd)
        try:
            real_f = open(path, "rb")
            with ReadRecordingFile(real_f) as wrapper:
                wrapper.seek(0)
                wrapper.read(512)
            self.assertTrue(real_f.closed)
        finally:
            os.remove(path)


class TestValidateBatSpanOversizedClaim(unittest.TestCase):
    """Unit test for the oversized-BAT-claim pre-read-rejection proof.

    Uses the read-recording file wrapper to confirm that a
    maxTableEntries value whose implied bat_bytes_len exceeds the file's
    actual size is rejected by validate_bat_span via pure arithmetic
    against file_size, strictly before any attempt is made to read bytes
    from the claimed BAT region.

    Validates: Requirements 2.5
    """

    def test_oversized_bat_claim_rejected_before_any_bat_region_read(self):
        # Build a small, valid-dynamic-VHD-shaped file: leading footer,
        # header, but the file is cut short overall so it is far too
        # short to actually contain the BAT implied by a large
        # maxTableEntries claim.
        header_offset = 512
        raw_header = _build_header_full(table_offset=1536, max_table_entries=4,
                                          block_size=2 * 1024 * 1024)
        leading_footer = _build_footer_full()
        # File on disk: 512 (leading footer) + 1024 (header) = 1536 bytes
        # total. There is no room at all for a BAT at table_offset=1536.
        file_bytes = leading_footer + raw_header
        file_size = len(file_bytes)
        self.assertEqual(file_size, 1536)

        # A maxTableEntries claim large enough that its implied
        # bat_bytes_len vastly exceeds the actual file size.
        huge_max_table_entries = 100000
        bat_bytes_len = image.compute_bat_bytes_len(huge_max_table_entries)
        table_offset = 1536
        self.assertGreater(table_offset + bat_bytes_len, file_size)

        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, file_bytes)
        os.close(fd)
        try:
            with open(path, "rb") as real_f:
                wrapper = ReadRecordingFile(real_f)

                with self.assertRaises(ValueError):
                    image.validate_bat_span(table_offset, bat_bytes_len, file_size, [])

                # Pre-read-rejection proof: no read the test itself (or
                # validate_bat_span, which takes no file object at all)
                # ever performed targets any offset within the claimed
                # BAT region [table_offset, table_offset + bat_bytes_len).
                bat_region_start, bat_region_end = table_offset, table_offset + bat_bytes_len
                for (offset, length) in wrapper.reads:
                    read_end = offset if length is None or length < 0 else offset + length
                    self.assertFalse(
                        offset < bat_region_end and read_end > bat_region_start,
                        "a read at offset=%d length=%r fell within the claimed BAT "
                        "region [%d, %d)" % (offset, length, bat_region_start, bat_region_end),
                    )
                # In fact, no read was ever attempted via the wrapper at
                # all in this test: validate_bat_span rejects the claim
                # using only arithmetic against file_size, with no file
                # object involved.
                self.assertEqual(wrapper.reads, [])

                # Demonstrate concretely that the file genuinely lacks
                # that much data: attempting to read the claimed BAT
                # region directly would return far fewer bytes than
                # claimed (or nothing at all), confirming the rejection
                # was correct, not merely convenient.
                wrapper.seek(table_offset)
                actual_available = wrapper.read(bat_bytes_len)
                self.assertLess(len(actual_available), bat_bytes_len)
        finally:
            os.remove(path)


class TestBatCapacityAndSpanProperty(unittest.TestCase):
    """Property test for BAT capacity exactness and pre-read bounding.

    Feature: dynamic-vhd-reader, Property 9: BAT capacity is exact and
    bounded before any BAT read

    Validates: Requirements 2.5, 2.6
    """

    def _assert_no_read_in_region(self, wrapper, region_start, region_end):
        for (offset, length) in wrapper.reads:
            read_end = offset if length is None or length < 0 else offset + length
            self.assertFalse(
                offset < region_end and read_end > region_start,
                "a read at offset=%d length=%r fell within region [%d, %d)"
                % (offset, length, region_start, region_end),
            )

    def test_invalid_capacity_and_span_combinations_rejected_before_bat_read(self):
        """Generate cases (fixed-seed random.Random) combining
        validate_bat_capacity and validate_bat_span rejections for
        invalid maxTableEntries/table_offset combinations, using the
        read-recording file wrapper where relevant to confirm no
        BAT-region read occurs for invalid cases."""
        rng = random.Random(20240901)

        for _ in range(10):
            block_size = rng.choice([512, 1024 * 1024, 2 * 1024 * 1024])
            virtual_size = block_size * rng.randrange(1, 20)
            exact_entries = -(-virtual_size // block_size)

            # --- validate_bat_capacity rejection cases ---
            for bad_entries in (exact_entries - 1, exact_entries + 1):
                if bad_entries < 0:
                    continue
                with self.assertRaises(ValueError):
                    image.validate_bat_capacity(bad_entries, virtual_size, block_size)

            over_cap_entries = image.MAX_BAT_ENTRIES + rng.randrange(1, 1000)
            with self.assertRaises(ValueError):
                image.validate_bat_capacity(
                    over_cap_entries, over_cap_entries * block_size, block_size
                )

            # --- validate_bat_span rejection cases, proven via the
            # read-recording wrapper that no BAT-region read occurs ---
            small_file_size = 1536  # leading footer + header only
            table_offset = 1536
            bat_bytes_len = image.compute_bat_bytes_len(exact_entries)
            # Deliberately make the claimed BAT region exceed the file.
            oversized_bat_bytes_len = bat_bytes_len + small_file_size + rng.randrange(512, 4096)

            fd, path = tempfile.mkstemp(suffix=".img")
            os.write(fd, bytes(small_file_size))
            os.close(fd)
            try:
                with open(path, "rb") as real_f:
                    wrapper = ReadRecordingFile(real_f)
                    with self.assertRaises(ValueError):
                        image.validate_bat_span(
                            table_offset, oversized_bat_bytes_len, small_file_size, []
                        )
                    region_end = table_offset + oversized_bat_bytes_len
                    self._assert_no_read_in_region(wrapper, table_offset, region_end)
                    self.assertEqual(wrapper.reads, [])
            finally:
                os.remove(path)

            # --- validate_bat_span overlap-with-prior-interval rejection ---
            prior_intervals = [(0, 512), (512, 1536)]  # leading footer, header
            with self.assertRaises(ValueError):
                image.validate_bat_span(512, 1024, 4096, prior_intervals)


class TestParseBat(unittest.TestCase):
    """Unit test for parse_bat(raw, max_table_entries).

    Confirms parse_bat correctly parses max_table_entries big-endian
    uint32 values, correctly identifies 0xFFFFFFFF as unallocated, and
    correctly returns raw sector-offset integers for allocated entries.

    Note: alignment/overlap validation of the returned sector-offset
    values (e.g. whether an allocated entry's implied block interval
    overlaps the header) is intentionally NOT covered here -- that is
    the responsibility of validate_disjoint_sorted / build_block_intervals,
    implemented in later tasks. This test is narrower than the eventual
    task 21.1 test, which will perform the real overlap check once those
    functions exist.

    Validates: Requirements 2.5
    """

    def test_parses_exact_count_and_identifies_unallocated_and_allocated(self):
        max_table_entries = 5
        sector_offsets = [100, 0xFFFFFFFF, 200, 0xFFFFFFFF, 300]
        raw = b"".join(struct.pack(">I", v) for v in sector_offsets)

        result = image.parse_bat(raw, max_table_entries)

        self.assertEqual(len(result), max_table_entries)
        self.assertEqual(result, sector_offsets)
        self.assertEqual(result[1], 0xFFFFFFFF)
        self.assertEqual(result[3], 0xFFFFFFFF)
        self.assertEqual(result[0], 100)
        self.assertEqual(result[2], 200)
        self.assertEqual(result[4], 300)

    def test_parses_only_max_table_entries_even_with_extra_trailing_bytes(self):
        """raw may be longer than strictly needed (e.g. sector-rounded
        BAT region); parse_bat must stop after exactly max_table_entries
        entries."""
        max_table_entries = 3
        sector_offsets = [1, 2, 3]
        raw = b"".join(struct.pack(">I", v) for v in sector_offsets)
        raw += b"\xff" * 512  # extra padding beyond the sector-rounded BAT

        result = image.parse_bat(raw, max_table_entries)

        self.assertEqual(result, sector_offsets)

    def test_all_unallocated(self):
        max_table_entries = 4
        raw = struct.pack(">I", 0xFFFFFFFF) * max_table_entries

        result = image.parse_bat(raw, max_table_entries)

        self.assertEqual(result, [0xFFFFFFFF] * max_table_entries)

    def test_all_allocated(self):
        max_table_entries = 4
        sector_offsets = [5, 10, 15, 20]
        raw = b"".join(struct.pack(">I", v) for v in sector_offsets)

        result = image.parse_bat(raw, max_table_entries)

        self.assertEqual(result, sector_offsets)


class TestIntervalConstructionAndDisjointness(unittest.TestCase):
    """Unit tests for build_metadata_intervals, build_block_intervals,
    validate_disjoint_sorted, and validate_blocks_do_not_reach_footer.

    Validates: Requirements 2.5
    """

    def test_block_interval_overlapping_header_rejected_by_disjoint_sorted(self):
        """A block interval that overlaps the header interval is rejected
        by validate_disjoint_sorted."""
        file_size = 1024 * 1024
        header_offset = 1536
        header_interval = (header_offset, header_offset + image.HEADER_SIZE)
        # Block interval deliberately overlaps the header span.
        block_interval = (header_offset + 100, header_offset + 100 + 4096)
        intervals = [
            (file_size - 512, file_size),
            (0, 512),
            header_interval,
            block_interval,
        ]
        with self.assertRaises(ValueError):
            image.validate_disjoint_sorted(intervals, file_size)

    def test_block_interval_ending_exactly_at_file_size_minus_512_passes(self):
        """A block interval ending exactly at file_size - 512 passes
        validate_blocks_do_not_reach_footer (the boundary case is
        allowed, not rejected)."""
        file_size = 1024 * 1024
        block_interval = (file_size - 512 - 4096, file_size - 512)
        image.validate_blocks_do_not_reach_footer([block_interval], file_size)

    def test_block_interval_ending_at_file_size_minus_511_rejected(self):
        """A block interval ending at file_size - 511 is rejected by
        validate_blocks_do_not_reach_footer, even though it would pass a
        plain `end <= file_size` check. This is the key distinguishing
        test between validate_disjoint_sorted (bound: file_size) and
        validate_blocks_do_not_reach_footer (bound: file_size - 512)."""
        file_size = 1024 * 1024
        block_interval = (file_size - 511 - 4096, file_size - 511)
        # Would pass the plain end <= file_size check used by
        # validate_disjoint_sorted...
        self.assertLessEqual(block_interval[1], file_size)
        # ...but must still be rejected by the stricter footer-distance
        # rule.
        with self.assertRaises(ValueError):
            image.validate_blocks_do_not_reach_footer([block_interval], file_size)

    def test_build_metadata_intervals_with_leading_footer(self):
        """build_metadata_intervals returns the trailing footer, leading
        footer, header, and BAT intervals, all as (start, end) tuples."""
        file_size = 1024 * 1024
        header_offset = 512
        table_offset = 1536
        bat_bytes_len = 512
        intervals = image.build_metadata_intervals(
            True, file_size, header_offset, table_offset, bat_bytes_len
        )
        self.assertIn((file_size - 512, file_size), intervals)
        self.assertIn((0, 512), intervals)
        self.assertIn((header_offset, header_offset + image.HEADER_SIZE), intervals)
        self.assertIn((table_offset, table_offset + bat_bytes_len), intervals)
        self.assertEqual(len(intervals), 4)

    def test_build_metadata_intervals_without_leading_footer(self):
        """When leading_footer_present is False, only three intervals are
        returned (no leading footer interval)."""
        file_size = 1024 * 1024
        header_offset = 512
        table_offset = 1536
        bat_bytes_len = 512
        intervals = image.build_metadata_intervals(
            False, file_size, header_offset, table_offset, bat_bytes_len
        )
        self.assertNotIn((0, 512), intervals)
        self.assertEqual(len(intervals), 3)

    def test_build_block_intervals_skips_unallocated_entries(self):
        """Unallocated entries (0xFFFFFFFF) contribute no interval, and
        allocated entries produce one (start, end) tuple each, in BAT
        order."""
        block_size = 2 * 1024 * 1024  # 2 MiB
        bat = [100, 0xFFFFFFFF, 200]
        intervals = image.build_block_intervals(bat, block_size)
        self.assertEqual(len(intervals), 2)
        # bitmap for a 2 MiB block: 4096 sectors / 8 = 512 bytes = 1 sector.
        expected_bitmap_sectors = 1
        start0 = 100 * image.SECTOR_SIZE
        end0 = start0 + expected_bitmap_sectors * image.SECTOR_SIZE + block_size
        self.assertEqual(intervals[0], (start0, end0))
        start1 = 200 * image.SECTOR_SIZE
        end1 = start1 + expected_bitmap_sectors * image.SECTOR_SIZE + block_size
        self.assertEqual(intervals[1], (start1, end1))

    def test_valid_disjoint_intervals_pass(self):
        """A full set of non-overlapping, in-bounds intervals passes
        validate_disjoint_sorted with no exception."""
        file_size = 1024 * 1024
        intervals = [
            (0, 512),
            (512, 1536),
            (1536, 2048),
            (file_size - 512, file_size),
        ]
        image.validate_disjoint_sorted(intervals, file_size)


class TestIntervalDisjointnessProperty(unittest.TestCase):
    """Property test for interval disjointness and the block-vs-footer bound.

    Feature: dynamic-vhd-reader, Property 12: All intervals are disjoint
    and in-bounds; blocks additionally never reach the trailing footer

    Validates: Requirements 2.5
    """

    def _build_valid_layout(self, rng, num_blocks, block_size, extra_gap):
        """Build a self-consistent, disjoint layout: leading footer,
        header, BAT, and num_blocks allocated blocks placed back-to-back
        after the BAT with an optional gap, followed by trailing footer.
        Returns (file_size, metadata_intervals, block_intervals, bat)."""
        header_offset = image.SECTOR_SIZE
        table_offset = header_offset + image.HEADER_SIZE
        max_table_entries = max(num_blocks, 1)
        bat_bytes_len = image.compute_bat_bytes_len(max_table_entries)

        sectors_per_block = -(-block_size // image.SECTOR_SIZE)
        bitmap_bytes = -(-sectors_per_block // 8)
        bitmap_sectors = max(1, -(-bitmap_bytes // image.SECTOR_SIZE))
        block_total_len = bitmap_sectors * image.SECTOR_SIZE + block_size

        cursor = table_offset + bat_bytes_len + extra_gap
        bat = []
        for _ in range(num_blocks):
            if cursor % image.SECTOR_SIZE != 0:
                cursor += image.SECTOR_SIZE - (cursor % image.SECTOR_SIZE)
            bat.append(cursor // image.SECTOR_SIZE)
            cursor += block_total_len

        file_size = cursor + image.FOOTER_SIZE
        # round file_size up to a sector boundary for realism
        if file_size % image.SECTOR_SIZE != 0:
            file_size += image.SECTOR_SIZE - (file_size % image.SECTOR_SIZE)

        metadata_intervals = image.build_metadata_intervals(
            True, file_size, header_offset, table_offset, bat_bytes_len
        )
        block_intervals = image.build_block_intervals(bat, block_size)
        return file_size, metadata_intervals, block_intervals, bat

    def test_valid_layouts_pass_both_checks(self):
        """Generated valid, disjoint layouts pass both
        validate_disjoint_sorted and validate_blocks_do_not_reach_footer."""
        rng = random.Random(20241001)
        for _ in range(15):
            num_blocks = rng.randrange(0, 5)
            block_size = rng.choice([512, 4096, 65536])
            extra_gap = rng.randrange(0, 3) * image.SECTOR_SIZE
            file_size, metadata_intervals, block_intervals, _bat = self._build_valid_layout(
                rng, num_blocks, block_size, extra_gap
            )
            all_intervals = metadata_intervals + block_intervals
            image.validate_disjoint_sorted(all_intervals, file_size)
            image.validate_blocks_do_not_reach_footer(block_intervals, file_size)

    def test_block_interval_overlapping_metadata_rejected(self):
        """Generated cases where a block interval is deliberately placed
        to overlap the header or BAT interval are rejected by
        validate_disjoint_sorted."""
        rng = random.Random(20241002)
        for _ in range(10):
            file_size = 1024 * 1024
            header_offset = image.SECTOR_SIZE
            table_offset = header_offset + image.HEADER_SIZE
            bat_bytes_len = image.compute_bat_bytes_len(1)
            metadata_intervals = image.build_metadata_intervals(
                True, file_size, header_offset, table_offset, bat_bytes_len
            )
            # Place a block interval overlapping a randomly chosen
            # metadata interval.
            target = rng.choice(metadata_intervals)
            overlapping_block = (target[0], target[0] + max(1, (target[1] - target[0]) // 2))
            all_intervals = metadata_intervals + [overlapping_block]
            with self.assertRaises(ValueError):
                image.validate_disjoint_sorted(all_intervals, file_size)

    def test_block_boundary_at_file_size_minus_512_passes_and_minus_511_fails(self):
        """For each generated scenario, directly exercise the boundary:
        a block interval ending exactly at file_size - 512 must pass,
        and a block interval ending at file_size - 511 must fail."""
        rng = random.Random(20241003)
        for _ in range(10):
            file_size = rng.randrange(1, 50) * image.SECTOR_SIZE * 1024
            block_len = rng.randrange(1, 10) * image.SECTOR_SIZE

            passing_block = (file_size - 512 - block_len, file_size - 512)
            image.validate_blocks_do_not_reach_footer([passing_block], file_size)

            failing_block = (file_size - 511 - block_len, file_size - 511)
            with self.assertRaises(ValueError):
                image.validate_blocks_do_not_reach_footer([failing_block], file_size)


if __name__ == "__main__":
    unittest.main()


class TestReadBitmapAndBlock(unittest.TestCase):
    """Unit tests for read_bitmap_and_block(f, bat_entry_sectors, block_size).

    Validates: Requirements 2.5, 3.1, 3.2
    """

    def _bitmap_len_for(self, block_size):
        sectors_per_block = -(-block_size // image.SECTOR_SIZE)
        bitmap_bytes = -(-sectors_per_block // 8)
        bitmap_sectors = max(1, -(-bitmap_bytes // image.SECTOR_SIZE))
        return bitmap_sectors * image.SECTOR_SIZE

    def test_full_bitmap_and_block_round_trip(self):
        """A file containing a complete, correctly-sized bitmap followed
        by a complete block reads back exactly bitmap_len and block_size
        bytes with no error (happy-path regression check)."""
        block_size = 4096
        bitmap_len = self._bitmap_len_for(block_size)
        bitmap_content = bytes([0xFF]) * bitmap_len
        block_content = bytes((i % 256) for i in range(block_size))

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bitmap_content + block_content)
            path = tmp.name
        try:
            with open(path, "rb") as f:
                bitmap, block = image.read_bitmap_and_block(f, 0, block_size)
            self.assertEqual(len(bitmap), bitmap_len)
            self.assertEqual(len(block), block_size)
            self.assertEqual(bitmap, bitmap_content)
            self.assertEqual(block, block_content)
        finally:
            os.remove(path)

    def test_truncated_bitmap_region_raises_value_error(self):
        """When the file ends partway through the bitmap region itself
        (fewer bytes available than bitmap_len), read_bitmap_and_block
        must raise ValueError rather than returning a short bitmap."""
        block_size = 4096
        bitmap_len = self._bitmap_len_for(block_size)
        # Only provide half of the bitmap region, and nothing else.
        truncated_bitmap = bytes([0xAA]) * (bitmap_len // 2)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(truncated_bitmap)
            path = tmp.name
        try:
            with open(path, "rb") as f:
                with self.assertRaises(ValueError):
                    image.read_bitmap_and_block(f, 0, block_size)
        finally:
            os.remove(path)

    def test_truncated_block_region_raises_value_error(self):
        """When the bitmap reads fully but the block region is truncated
        (file ends partway through the block), read_bitmap_and_block
        must raise ValueError rather than returning a short block."""
        block_size = 4096
        bitmap_len = self._bitmap_len_for(block_size)
        bitmap_content = bytes([0xFF]) * bitmap_len
        # Only provide half of the block region.
        truncated_block = bytes((i % 256) for i in range(block_size // 2))

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bitmap_content + truncated_block)
            path = tmp.name
        try:
            with open(path, "rb") as f:
                with self.assertRaises(ValueError):
                    image.read_bitmap_and_block(f, 0, block_size)
        finally:
            os.remove(path)


class TestReconstructFlatBytesSparseAndUsed(unittest.TestCase):
    """Unit tests for reconstruct_flat_bytes(virtual_size, block_size, bat, f).

    Validates: Requirements 1.1, 1.4, 3.1, 3.2, 3.3
    """

    def _bitmap_len_for(self, block_size):
        sectors_per_block = -(-block_size // image.SECTOR_SIZE)
        bitmap_bytes = -(-sectors_per_block // 8)
        bitmap_sectors = max(1, -(-bitmap_bytes // image.SECTOR_SIZE))
        return bitmap_sectors * image.SECTOR_SIZE

    def test_fully_sparse_block_reconstructs_to_zeros(self):
        """A BAT with no allocated entries reconstructs to all-zero flat
        bytes of exactly virtual_size length."""
        virtual_size = 4096
        block_size = 4096
        bat = [0xFFFFFFFF]
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            with open(path, "rb") as f:
                result = image.reconstruct_flat_bytes(virtual_size, block_size, bat, f)
            self.assertEqual(len(result), virtual_size)
            self.assertEqual(result, b"\x00" * virtual_size)
        finally:
            os.remove(path)

    def test_mixed_used_and_sparse_sectors(self):
        """A block with a mix of used/sparse sectors copies used sectors
        verbatim and leaves sparse sectors zero-filled, on a full,
        correctly-sized bitmap+block (happy-path regression check after
        the read_bitmap_and_block length-validation fix)."""
        block_size = 4096  # 8 sectors
        virtual_size = block_size
        bitmap_len = self._bitmap_len_for(block_size)

        # bit 7 (sector 0) and bit 5 (sector 2) set; rest clear.
        bitmap = bytearray(bitmap_len)
        bitmap[0] = 0b10100000
        sector0 = bytes([0x11]) * image.SECTOR_SIZE
        sector2 = bytes([0x22]) * image.SECTOR_SIZE
        block = bytearray(block_size)
        block[0:image.SECTOR_SIZE] = sector0
        block[2 * image.SECTOR_SIZE:3 * image.SECTOR_SIZE] = sector2

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bytes(bitmap) + bytes(block))
            path = tmp.name
        try:
            with open(path, "rb") as f:
                result = image.reconstruct_flat_bytes(virtual_size, block_size, [0], f)
            self.assertEqual(result[0:image.SECTOR_SIZE], sector0)
            self.assertEqual(result[image.SECTOR_SIZE:2 * image.SECTOR_SIZE], b"\x00" * image.SECTOR_SIZE)
            self.assertEqual(result[2 * image.SECTOR_SIZE:3 * image.SECTOR_SIZE], sector2)
            self.assertEqual(result[3 * image.SECTOR_SIZE:], b"\x00" * (block_size - 3 * image.SECTOR_SIZE))
        finally:
            os.remove(path)

    def test_truncated_block_propagates_value_error(self):
        """When one of reconstruct_flat_bytes's allocated BAT entries
        points at a truncated region (bitmap or block region short),
        the ValueError raised by read_bitmap_and_block surfaces through
        reconstruct_flat_bytes rather than being swallowed or producing
        silently-wrong output."""
        block_size = 4096
        virtual_size = block_size
        bitmap_len = self._bitmap_len_for(block_size)
        bitmap_content = bytes([0xFF]) * bitmap_len
        # Truncate the block region so the file ends partway through it.
        truncated_block = bytes((i % 256) for i in range(block_size // 2))

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bitmap_content + truncated_block)
            path = tmp.name
        try:
            with open(path, "rb") as f:
                with self.assertRaises(ValueError):
                    image.reconstruct_flat_bytes(virtual_size, block_size, [0], f)
        finally:
            os.remove(path)


def _checksum(buf, checksum_offset):
    """Compute the VHD ones'-complement checksum of buf with the 4 bytes
    at checksum_offset treated as zero, per design.md."""
    tmp = bytearray(buf)
    tmp[checksum_offset:checksum_offset + 4] = b"\x00\x00\x00\x00"
    return (~sum(tmp)) & 0xFFFFFFFF


def _build_vhd_footer(disk_type, data_offset, current_size=0, original_size=0, unique_id=None):
    """Build a complete, correctly-checksummed 512-byte VHD footer."""
    if unique_id is None:
        unique_id = bytes(range(16))
    buf = bytearray(512)
    buf[0:8] = b"conectix"
    struct.pack_into(">I", buf, 8, 2)                 # features
    struct.pack_into(">I", buf, 12, 0x00010000)       # fileFormatVersion
    struct.pack_into(">Q", buf, 16, data_offset)       # dataOffset
    struct.pack_into(">I", buf, 24, 0)                 # timestamp
    buf[28:32] = b"wa  "                                # creatorApplication
    struct.pack_into(">I", buf, 32, 0x00010000)        # creatorVersion
    buf[36:40] = b"Wi2k"                                # creatorHostOS
    struct.pack_into(">Q", buf, 40, original_size)     # originalSize
    struct.pack_into(">Q", buf, 48, current_size)      # currentSize
    buf[56:60] = bytes([0, 16, 63, 63])                 # diskGeometry
    struct.pack_into(">I", buf, 60, disk_type)         # diskType
    struct.pack_into(">I", buf, 64, 0)                 # checksum placeholder
    buf[68:84] = unique_id
    buf[84] = 0
    buf[85:512] = b"\x00" * (512 - 85)
    checksum = _checksum(buf, 64)
    struct.pack_into(">I", buf, 64, checksum)
    return bytes(buf)


def _build_vhd_header(table_offset, max_table_entries, block_size):
    """Build a complete, correctly-checksummed 1024-byte VHD header."""
    buf = bytearray(1024)
    buf[0:8] = b"cxsparse"
    struct.pack_into(">Q", buf, 8, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into(">Q", buf, 16, table_offset)
    struct.pack_into(">I", buf, 24, 0x00010000)
    struct.pack_into(">I", buf, 28, max_table_entries)
    struct.pack_into(">I", buf, 32, block_size)
    struct.pack_into(">I", buf, 36, 0)
    buf[40:56] = bytes(16)
    struct.pack_into(">I", buf, 56, 0)
    buf[60:64] = bytes(4)
    buf[64:576] = bytes(512)
    buf[576:768] = bytes(192)
    buf[768:1024] = bytes(256)
    checksum = _checksum(buf, 36)
    struct.pack_into(">I", buf, 36, checksum)
    return bytes(buf)


def _build_minimal_dynamic_vhd(block_size=512, num_blocks=1, allocated=None, sector_fill=None):
    """Build a complete, minimal, valid dynamic VHD as bytes.

    Layout: [leading footer 512][header 1024][BAT, rounded to sector][blocks...][trailing footer 512].
    `allocated` is a set of block indices that are allocated (default: all).
    `sector_fill` is a callable(block_index, sector_index) -> bytes(512) for
    used sectors; defaults to a deterministic repeating pattern.
    """
    if allocated is None:
        allocated = set(range(num_blocks))
    if sector_fill is None:
        def sector_fill(b, s):
            return bytes([((b + 1) * 16 + s) & 0xFF]) * 512

    virtual_size = block_size * num_blocks
    header_offset = 512
    table_offset = header_offset + 1024
    max_table_entries = num_blocks
    bat_bytes_len = -(-max_table_entries * 4 // 512) * 512

    sectors_per_block = -(-block_size // 512)
    bitmap_bytes = -(-sectors_per_block // 8)
    bitmap_sectors = max(1, -(-bitmap_bytes // 512))
    bitmap_len = bitmap_sectors * 512

    blocks_start = table_offset + bat_bytes_len
    bat_entries = []
    block_region = bytearray()
    next_sector = blocks_start // 512
    for b in range(num_blocks):
        if b not in allocated:
            bat_entries.append(0xFFFFFFFF)
            continue
        bat_entries.append(next_sector)
        bitmap = bytearray(bitmap_len)
        for s in range(sectors_per_block):
            byte_index = s // 8
            bit_index = 7 - (s % 8)
            bitmap[byte_index] |= (1 << bit_index)
        block_data = bytearray(block_size)
        for s in range(sectors_per_block):
            block_data[s * 512:(s + 1) * 512] = sector_fill(b, s)
        block_region += bitmap
        block_region += block_data
        next_sector += (bitmap_len + block_size) // 512

    blocks_end = blocks_start + len(block_region)
    file_size = blocks_end + 512

    trailing_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
    leading_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
    header = _build_vhd_header(table_offset, max_table_entries, block_size)
    bat_raw = bytearray(bat_bytes_len)
    for i, entry in enumerate(bat_entries):
        struct.pack_into(">I", bat_raw, i * 4, entry)

    out = bytearray()
    out += leading_footer
    out += header
    out += bat_raw
    out += block_region
    out += trailing_footer
    assert len(out) == file_size
    return bytes(out), virtual_size


class TestLoadDispatchBackwardCompatibility(unittest.TestCase):
    """Unit tests for dispatch precedence and backward compatibility in load().

    Feature: dynamic-vhd-reader

    Validates: Requirements 1.2, 1.3, 2.1, 4.1, 4.2
    """

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def test_raw_image_loads_unchanged(self):
        """A raw image with no recognizable signature loads unchanged."""
        from tests.fixtures import fat12_image
        data = fat12_image()
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, data)
            self.assertEqual(info, {"path": path, "size": len(data), "container": "raw"})
        finally:
            os.remove(path)

    def test_fixed_vhd_loads_with_footer_stripped(self):
        """A fixed VHD (trailing type 2 footer) loads with the footer
        stripped, unchanged from historical behavior."""
        from tests.fixtures import fat12_image
        payload = fat12_image()
        footer = _build_vhd_footer(2, 0xFFFFFFFFFFFFFFFF, current_size=len(payload), original_size=len(payload))
        data = payload + footer
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, payload)
            self.assertEqual(info, {"path": path, "size": len(data), "container": "fixed VHD"})
        finally:
            os.remove(path)

    def test_512_to_1023_byte_aliasing_regression(self):
        """A file sized in [512, 1023] with a trailing conectix cookie
        (type 2) and a non-conectix leading region is classified as a
        fixed VHD, exercising the historical tail=head aliasing bug
        range."""
        payload = b"X" * 100
        footer = _build_vhd_footer(2, 0xFFFFFFFFFFFFFFFF, current_size=len(payload), original_size=len(payload))
        data = payload + footer
        self.assertTrue(512 <= len(data) < 1024)
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, payload)
            self.assertEqual(info["container"], "fixed VHD")
        finally:
            os.remove(path)

    def test_both_copies_type_2_fixed_disambiguation(self):
        """Both footer copies (leading and trailing) individually valid
        and reporting diskType == 2 must NOT enter the dynamic path; it
        is classified via the historical trailing-footer-strip rule."""
        from tests.fixtures import fat12_image
        payload = fat12_image()
        footer = _build_vhd_footer(2, 0xFFFFFFFFFFFFFFFF, current_size=len(payload), original_size=len(payload))
        # Prepend a leading type-2 footer-shaped region too.
        data = footer + payload[512:] + footer
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(info["container"], "fixed VHD")
            self.assertEqual(out_data, data[:-512])
        finally:
            os.remove(path)

    def test_leading_type_4_rejected_with_differencing_error(self):
        """A leading conectix cookie with disk type 4 is rejected with
        the differencing-VHD error."""
        from tests.fixtures import fat12_image
        payload = fat12_image()
        footer = _build_vhd_footer(4, 0xFFFFFFFFFFFFFFFF, current_size=len(payload), original_size=len(payload))
        data = footer + payload[512:]
        path = self._write_temp(data)
        try:
            with self.assertRaises(ValueError) as cm:
                image.load(path)
            self.assertIn("Differencing", str(cm.exception))
        finally:
            os.remove(path)

    def test_leading_non_dynamic_valid_cookie_rejected_historical_message(self):
        """A leading conectix cookie that is not a valid type-3/4
        candidate (e.g. malformed/garbage disk type byte region) is
        rejected with the historical rejection message."""
        data = b"conectix" + b"\x00" * 1000
        path = self._write_temp(data)
        try:
            with self.assertRaises(ValueError) as cm:
                image.load(path)
            self.assertIn("dynamic VHD is not supported yet", str(cm.exception))
        finally:
            os.remove(path)

    def test_vmdk_signature_rejected(self):
        data = b"KDMV" + b"\x00" * 1000
        path = self._write_temp(data)
        try:
            with self.assertRaises(ValueError) as cm:
                image.load(path)
            self.assertIn("not supported yet", str(cm.exception))
        finally:
            os.remove(path)

    def test_qcow2_signature_rejected(self):
        data = b"QFI\xfb" + b"\x00" * 1000
        path = self._write_temp(data)
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)

    def test_vdi_signature_rejected(self):
        data = b"<<< Orac" + b"\x00" * 1000
        path = self._write_temp(data)
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)


class TestLoadRawFixedPreservationProperty(unittest.TestCase):
    """Property test for raw/fixed output preservation.

    Feature: dynamic-vhd-reader, Property 2: Raw and fixed-VHD output
    preserved for all non-type-3/non-type-4 inputs

    Validates: Requirements 1.2, 1.3, 4.1, 4.2
    """

    def _old_load(self, path):
        """Reference implementation: the pre-dynamic-VHD-feature load()
        algorithm, inlined verbatim for comparison purposes."""
        data = open(path, "rb").read()
        info = {"path": path, "size": len(data), "container": "raw"}
        if len(data) >= 512 and data[-512:-504] == b"conectix":
            info["container"] = "fixed VHD"
            data = data[:-512]
        elif data[:8] == b"conectix":
            raise ValueError("dynamic VHD is not supported yet; convert to raw first")
        elif data[:4] in (b"KDMV", b"QFI\xfb") or data[:8] == b"<<< Orac":
            raise ValueError("VMDK/QCOW2/VDI images are not supported yet; convert to raw first")
        return data, info

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def test_generated_cases_match_old_algorithm(self):
        rng = random.Random(991122)
        from tests.fixtures import fat12_image, unknown_sector

        cases = []
        # Raw images of varying sizes, no cookie.
        for _ in range(5):
            size = rng.randrange(0, 2000)
            cases.append(bytes((rng.randrange(256) for _ in range(size))))
        cases.append(fat12_image())
        cases.append(unknown_sector())

        # Fixed VHD (trailing type 2), varying payload size.
        for _ in range(5):
            payload_size = rng.randrange(0, 2000)
            payload = bytes((rng.randrange(256) for _ in range(payload_size)))
            footer = _build_vhd_footer(2, 0xFFFFFFFFFFFFFFFF, current_size=payload_size, original_size=payload_size)
            cases.append(payload + footer)

        # Leading conectix, non-type-3/4 (malformed) -> historical rejection.
        cases.append(b"conectix" + b"\x00" * rng.randrange(0, 1000))

        # Signature rejections.
        cases.append(b"KDMV" + b"\x00" * 500)
        cases.append(b"QFI\xfb" + b"\x00" * 500)
        cases.append(b"<<< Orac" + b"\x00" * 500)

        for data in cases:
            path = self._write_temp(data)
            try:
                try:
                    expected = self._old_load(path)
                except ValueError as e:
                    with self.assertRaises(ValueError):
                        image.load(path)
                    continue
                actual = image.load(path)
                self.assertEqual(actual, expected)
            finally:
                os.remove(path)


class TestLoadDispatchPrecedenceProperty(unittest.TestCase):
    """Property test for total ordered dispatch precedence.

    Feature: dynamic-vhd-reader, Property 5: Dispatch precedence is
    total and ordered

    Validates: Requirements 1.2, 1.3, 2.1
    """

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def _footer_window(self, disk_type):
        buf = bytearray(512)
        buf[0:8] = b"conectix"
        struct.pack_into(">I", buf, 60, disk_type)
        return bytes(buf)

    def _malformed_cookie_window(self):
        return b"conectiX" + b"\x00" * 504

    def _no_cookie_window(self):
        return b"\x00" * 512

    def test_every_combination_fires_exactly_one_branch(self):
        rng = random.Random(442211)
        kinds = {
            "type2": lambda: self._footer_window(2),
            "type3": lambda: self._footer_window(3),
            "type4": lambda: self._footer_window(4),
            "malformed": self._malformed_cookie_window,
            "nocookie": self._no_cookie_window,
        }
        payload_mid = bytes((rng.randrange(256) for _ in range(200)))

        for leading_kind, leading_fn in kinds.items():
            for trailing_kind, trailing_fn in kinds.items():
                leading = leading_fn()
                trailing = trailing_fn()
                data = leading + payload_mid + trailing
                path = self._write_temp(data)
                try:
                    if leading_kind == "type4" or trailing_kind == "type4":
                        with self.assertRaises(ValueError) as cm:
                            image.load(path)
                        self.assertIn("Differencing", str(cm.exception))
                    elif leading_kind == "type3" or trailing_kind == "type3":
                        # Dynamic path entered; a minimal non-valid synthetic
                        # candidate will fail deeper validation, but it must
                        # raise ValueError, not take any fallback branch.
                        with self.assertRaises(ValueError):
                            image.load(path)
                    elif trailing_kind == "type2":
                        # Historical fixed-footer-strip branch. Only an
                        # exact b"conectix" trailing cookie (type2) takes
                        # this branch; "malformed" (b"conectiX", capital X)
                        # never satisfies the exact-match cookie check used
                        # by either the historical code
                        # (data[-512:-504] == b"conectix") or the new
                        # dispatch's classify_footer_candidate, so it must
                        # NOT be grouped here.
                        out_data, info = image.load(path)
                        self.assertEqual(info["container"], "fixed VHD")
                        self.assertEqual(out_data, data[:-512])
                    elif leading_kind == "type2":
                        # Historical leading-cookie rejection. Only an
                        # exact b"conectix" leading cookie (type2) takes
                        # this branch, for the same exact-match reason as
                        # above; "malformed" must NOT be grouped here.
                        with self.assertRaises(ValueError) as cm:
                            image.load(path)
                        self.assertIn("dynamic VHD is not supported yet", str(cm.exception))
                    else:
                        # Falls through to raw, covering "nocookie" AND
                        # "malformed" (at either position): a "malformed"
                        # window (b"conectiX") never matches the exact
                        # b"conectix" cookie check anywhere in either the
                        # historical algorithm or the new dispatch, so it
                        # is indistinguishable from "nocookie" for dispatch
                        # purposes and must land in the same raw branch.
                        out_data, info = image.load(path)
                        self.assertEqual(info["container"], "raw")
                        self.assertEqual(out_data, data)
                finally:
                    os.remove(path)


class TestLoadDynamicVhdEndToEnd(unittest.TestCase):
    """End-to-end round-trip test for the full dynamic VHD pipeline
    through the public load() API.

    Validates: Requirements 1.1, 1.4, 4.3
    """

    def test_round_trip_with_allocated_and_sparse_blocks(self):
        """A complete, valid, minimal synthetic dynamic VHD with at least
        one allocated block and at least one sparse block round-trips
        through load(), producing the expected flat bytes, container
        label, and virtual_size."""
        block_size = 512
        num_blocks = 4
        allocated = {0, 2}  # blocks 1 and 3 are sparse/unallocated

        def sector_fill(b, s):
            return bytes([(b * 10 + 1) & 0xFF]) * 512

        data, virtual_size = _build_minimal_dynamic_vhd(
            block_size=block_size, num_blocks=num_blocks, allocated=allocated, sector_fill=sector_fill
        )

        expected = bytearray(virtual_size)
        for b in allocated:
            expected[b * block_size:(b + 1) * block_size] = sector_fill(b, 0)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, bytes(expected))
            self.assertEqual(info["container"], "dynamic VHD")
            self.assertEqual(info["virtual_size"], virtual_size)
            self.assertEqual(info["path"], path)
            self.assertEqual(info["size"], len(data))
        finally:
            os.remove(path)

    def test_fully_sparse_vhd_round_trips_to_all_zero_flat_bytes(self):
        """A dynamic VHD with no allocated blocks at all (every BAT entry
        unallocated) round-trips through load() to an all-zero flat
        bytes buffer of exactly the correct virtual size."""
        block_size = 512
        num_blocks = 4

        data, virtual_size = _build_minimal_dynamic_vhd(
            block_size=block_size, num_blocks=num_blocks, allocated=set()
        )

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, b"\x00" * virtual_size)
            self.assertEqual(len(out_data), virtual_size)
            self.assertEqual(info["container"], "dynamic VHD")
            self.assertEqual(info["virtual_size"], virtual_size)
        finally:
            os.remove(path)


class TestLoadDynamicVhdPreReadRejection(unittest.TestCase):
    """Pre-read-rejection proof at the load() level: a truncated dynamic
    VHD whose claimed structures exceed the file size is rejected with
    ValueError rather than an unhandled exception from an out-of-bounds
    read.

    Validates: Requirements 2.5, 2.6
    """

    def test_truncated_file_with_oversized_claims_raises_value_error(self):
        """Build a valid-looking dynamic VHD then truncate it so the
        BAT/block region claimed by the header exceeds the actual file
        size; load() must raise ValueError, not an unhandled exception."""
        data, _ = _build_minimal_dynamic_vhd(block_size=512, num_blocks=4, allocated={0, 1, 2, 3})
        # Truncate well past the header/BAT but before all block data,
        # while keeping a valid-looking trailing footer appended so the
        # trailing footer window still classifies as type 3.
        trailing_footer = data[-512:]
        truncated = data[:600] + trailing_footer
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(truncated)
            path = tmp.name
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)

    def test_truncated_file_is_validation_rejection_not_crash(self):
        """A dynamic VHD candidate truncated immediately after the header
        (before any BAT bytes exist) must raise ValueError specifically,
        confirming the rejection is a validation failure rather than an
        unhandled OSError/struct.error from attempting an out-of-bounds
        read."""
        data, _ = _build_minimal_dynamic_vhd(block_size=512, num_blocks=1, allocated={0})
        # Keep leading footer + header only, then append a trailing
        # footer so classification still recognizes a type-3 candidate.
        trailing_footer = data[-512:]
        truncated = data[:512 + 1024] + trailing_footer
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(truncated)
            path = tmp.name
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)

    def test_corrupted_footer_checksum_raises_value_error(self):
        """A complete, otherwise-valid dynamic VHD whose trailing footer
        checksum is corrupted (stale after a byte flip) is rejected by
        load() with ValueError, at the public API level."""
        data, _ = _build_minimal_dynamic_vhd(block_size=512, num_blocks=1, allocated={0})
        corrupted = bytearray(data)
        trailing_footer_start = len(corrupted) - 512
        # Corrupt a byte within the trailing footer's checksum-covered
        # region (originalSize, offset 40 relative to the footer start)
        # without recomputing the checksum, so it goes stale.
        corrupted[trailing_footer_start + 40] ^= 0xFF
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bytes(corrupted))
            path = tmp.name
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)

    def test_corrupted_header_checksum_raises_value_error(self):
        """A complete, otherwise-valid dynamic VHD whose header checksum
        is corrupted (stale after a byte flip) is rejected by load()
        with ValueError, at the public API level."""
        data, _ = _build_minimal_dynamic_vhd(block_size=512, num_blocks=1, allocated={0})
        corrupted = bytearray(data)
        header_offset = 512
        # Corrupt a byte within the header's checksum-covered region
        # (tableOffset, offset 16 relative to the header start) without
        # recomputing the checksum, so it goes stale.
        corrupted[header_offset + 16] ^= 0xFF
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bytes(corrupted))
            path = tmp.name
        try:
            with self.assertRaises(ValueError):
                image.load(path)
        finally:
            os.remove(path)


class TestLoadPipelineRealInstrumentedPreReadRejectionProof(unittest.TestCase):
    """Pre-read-rejection proofs anchored to the REAL end-to-end load()
    pipeline (task 24), observing the actual file I/O load() performs by
    monkeypatching legacyrescue.image.open with a factory that wraps the
    real builtin open() in a ReadRecordingFile only for the fixture path
    under test, and delegates to the real open() unchanged for any other
    path. This replaces an earlier version of this proof that called
    load() once with no instrumentation and separately hand-replayed the
    validation sequence against a different wrapped handle -- that
    replay never observed what the real load() call actually read. Every
    assertion here is about the .reads recorded on the SAME real open()
    call(s) load() itself performed.

    Per design.md's Testing Strategy, the read-recording wrapper (tests.
    fixtures.ReadRecordingFile) is used only for these pre-read-rejection
    proofs, never as a substitute for exercising the real load(path)
    entry point.

    Validates: Requirements 2.5, 2.6
    """

    def _instrumented_open(self, fixture_path, wrappers):
        """Return a factory for legacyrescue.image.open that wraps the
        real builtin open() in a ReadRecordingFile only when the
        requested path resolves to fixture_path; all other paths are
        delegated to the real builtin open() unchanged. Each wrapper
        created for fixture_path is appended to `wrappers` so the test
        can inspect its .reads after the real load() call returns (or
        raises).
        """
        real_open = open
        fixture_real_path = os.path.realpath(fixture_path)

        def factory(path, *args, **kwargs):
            try:
                is_fixture = os.path.realpath(path) == fixture_real_path
            except TypeError:
                is_fixture = False
            real_handle = real_open(path, *args, **kwargs)
            if not is_fixture:
                return real_handle
            wrapper = ReadRecordingFile(real_handle)
            wrappers.append(wrapper)
            return wrapper

        return factory

    def _load_with_instrumentation(self, path):
        """Call the real image.load(path) with image.open patched to
        record every read performed against `path`. Returns (exception,
        all_reads) where all_reads is the concatenation of .reads from
        every wrapper created during the call (load() opens the file
        more than once: once for head/tail classification and again for
        the dynamic-VHD pipeline itself).

        Every recorded (offset, length) pair is validated unconditionally
        here, before returning to the caller -- not only when a given
        test happens to run an overlap check against it. This guarantees
        every test in this class is proof against a malformed recorded
        read (None, negative, or non-integer length), regardless of
        which assertions that particular test goes on to make.
        """
        import unittest.mock as mock
        wrappers = []
        factory = self._instrumented_open(path, wrappers)
        raised = None
        with mock.patch.object(image, "open", factory):
            try:
                image.load(path)
            except ValueError as exc:
                raised = exc
        all_reads = []
        for w in wrappers:
            all_reads.extend(w.reads)
        for (offset, length) in all_reads:
            assert isinstance(length, int) and not isinstance(length, bool) and length >= 0, (
                "unexpected read length %r at offset %r -- a recorded read must "
                "always have a concrete non-negative integer length"
                % (length, offset)
            )
        return raised, all_reads

    def test_load_rejects_oversized_bat_claim_before_reading_bat_region(self):
        """Main case: maxTableEntries/blockSize claim a BAT region far
        larger than the file; load() must raise before any BAT-region
        read, and the only reads observed are the footer and header
        reads legitimately required to reach that rejection."""
        header_offset = 512
        table_offset = header_offset + 1024  # 1536
        huge_max_table_entries = 100000
        block_size = 2 * 1024 * 1024

        virtual_size = block_size * huge_max_table_entries
        # Keep virtual_size within validate_virtual_size's range so that
        # dispatch reaches as deep as possible (BAT capacity/span) before
        # failing. validate_virtual_size caps at 256 MiB; a product this
        # large would itself be rejected, so instead report a
        # virtual_size at the 256 MiB cap in the footers/header while
        # keeping maxTableEntries huge -- this isolates the rejection to
        # the BAT capacity stage specifically (maxTableEntries no longer
        # matches ceil(virtual_size/block_size)).
        virtual_size = 256 * 1024 * 1024

        leading_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        trailing_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        header = _build_vhd_header(table_offset, huge_max_table_entries, block_size)

        # The file on disk contains only the leading footer, the header,
        # and the trailing footer -- no BAT bytes and no block bytes at
        # all. table_offset (1536) plus the implied bat_bytes_len is far
        # beyond this file's actual size.
        file_bytes = leading_footer + header + trailing_footer
        file_size = len(file_bytes)

        bat_bytes_len = image.compute_bat_bytes_len(huge_max_table_entries)
        bat_region_start, bat_region_end = table_offset, table_offset + bat_bytes_len
        self.assertGreater(bat_region_end, file_size)

        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, file_bytes)
        os.close(fd)
        try:
            raised, reads = self._load_with_instrumentation(path)

            self.assertIsNotNone(raised, "load() did not raise ValueError")
            # validate_bat_capacity fires before validate_bat_span in
            # _load_dynamic_vhd's real order (confirmed by reading
            # image.py), so the BAT capacity rejection is what actually
            # fires first for this fixture: maxTableEntries no longer
            # exactly matches ceil(virtual_size / block_size). Pin the
            # exact wording from validate_bat_capacity in image.py.
            self.assertIn(
                "does not exactly match ceil(virtual_size / block_size)",
                str(raised),
            )

            # Exact multiset of reads performed by the real load() ->
            # read_head_tail_windows -> _load_dynamic_vhd pipeline for
            # this fixture, determined by running the real code path:
            #   - (0, 512): leading footer, read by read_head_tail_windows
            #     for head/tail classification (load()'s dispatch step).
            #   - (file_size - 512, 512) == (1536, 512): trailing footer,
            #     read by read_head_tail_windows for the same
            #     classification step (tail window).
            #   - (0, 512) again: leading footer re-read inside
            #     _load_dynamic_vhd (f.seek(0); f.read(FOOTER_SIZE)) for
            #     full validation.
            #   - (1536, 512) again: trailing footer re-read inside
            #     _load_dynamic_vhd (f.seek(file_size - FOOTER_SIZE)) for
            #     full validation.
            #   - (512, 1024): header read inside _load_dynamic_vhd
            #     (f.seek(header_offset); f.read(HEADER_SIZE)), performed
            #     because header_offset/file_size pass validate_header_span.
            # No BAT-region read occurs: validate_bat_capacity raises
            # before compute_bat_bytes_len/validate_bat_span/the BAT read.
            expected_reads = collections.Counter({
                (0, 512): 2,
                (file_size - 512, 512): 2,
                (header_offset, 1024): 1,
            })
            self.assertEqual(
                collections.Counter(reads), expected_reads,
                "unexpected multiset of reads observed: %r" % (collections.Counter(reads),),
            )
        finally:
            os.remove(path)

    def test_load_rejects_invalid_header_span_before_reading_header(self):
        """Header span rejection: the trailing footer's dataOffset points
        somewhere that overlaps the trailing footer itself. load() must
        raise before ever reading at that invalid header offset, while
        footer reads do occur."""
        virtual_size = 512 * 4

        # Build a short file where the only possible footer span is
        # [0,512) and [file_size-512, file_size). Point header_offset at
        # the trailing footer's own interval so validate_header_span
        # rejects it as overlapping.
        payload_len = 512 * 8
        file_size = 512 + payload_len + 512  # leading footer + payload + trailing footer
        invalid_header_offset = file_size - 512  # overlaps the trailing footer exactly

        leading_footer = _build_vhd_footer(3, invalid_header_offset, current_size=virtual_size, original_size=virtual_size)
        trailing_footer = _build_vhd_footer(3, invalid_header_offset, current_size=virtual_size, original_size=virtual_size)
        payload = b"\x00" * payload_len

        file_bytes = leading_footer + payload + trailing_footer
        self.assertEqual(len(file_bytes), file_size)

        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, file_bytes)
        os.close(fd)
        try:
            raised, reads = self._load_with_instrumentation(path)

            self.assertIsNotNone(raised, "load() did not raise ValueError")

            # Pin the exact wording from validate_header_span in image.py.
            self.assertIn(
                "exceeds file size",
                str(raised),
            )
            self.assertIn(
                "VHD header span [%d, %d)" % (invalid_header_offset, invalid_header_offset + 1024),
                str(raised),
            )

            # Exact multiset of reads performed by the real pipeline:
            #   - (0, 512) x2: leading footer, once via
            #     read_head_tail_windows (head/tail classification in
            #     load()) and once via _load_dynamic_vhd's own
            #     f.seek(0); f.read(FOOTER_SIZE).
            #   - (file_size - 512, 512) x2: trailing footer, once via
            #     read_head_tail_windows's tail window and once via
            #     _load_dynamic_vhd's f.seek(file_size - FOOTER_SIZE).
            # No header read (offset invalid_header_offset, length 1024)
            # occurs: validate_header_span raises before the bounded
            # f.seek(header_offset); f.read(HEADER_SIZE) call. Note
            # invalid_header_offset here deliberately equals
            # file_size - 512, i.e. the same offset as the trailing
            # footer's own 512-byte read -- but no length-1024 read at
            # that offset is ever observed, which is exactly what this
            # Counter proves (it would appear as a distinct entry
            # (invalid_header_offset, 1024) if it had happened).
            expected_reads = collections.Counter({
                (0, 512): 2,
                (file_size - 512, 512): 2,
            })
            self.assertEqual(
                collections.Counter(reads), expected_reads,
                "unexpected multiset of reads observed: %r" % (collections.Counter(reads),),
            )
        finally:
            os.remove(path)

    def test_load_rejects_invalid_bat_span_before_reading_bat_region(self):
        """BAT span rejection (distinct from the capacity case): a VALID
        maxTableEntries/blockSize/virtualSize combination (passes
        validate_bat_capacity) but a tableOffset that, combined with the
        valid bat_bytes_len, exceeds the file size. load() must raise
        before any read in the claimed BAT region, while footer and
        header reads do occur (header must be read first to discover
        tableOffset and maxTableEntries)."""
        header_offset = 512
        block_size = 512
        num_blocks = 4
        virtual_size = block_size * num_blocks
        max_table_entries = num_blocks  # exact match -> passes validate_bat_capacity

        bat_bytes_len = image.compute_bat_bytes_len(max_table_entries)  # 512 (1 sector)

        # Place the BAT near the very end of a short file so that
        # table_offset + bat_bytes_len exceeds file_size, even though
        # bat_bytes_len itself is small/valid.
        table_offset = header_offset + 1024 + 512  # one sector gap after header
        file_size = table_offset + bat_bytes_len - 256  # file is 256 bytes short of the claimed BAT end
        # Keep file_size sector-ish for realism but must stay short of
        # table_offset + bat_bytes_len; pad to be >= header end at least.
        self.assertGreater(table_offset + bat_bytes_len, file_size)
        self.assertGreaterEqual(file_size, header_offset + 1024)

        leading_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        trailing_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        header = _build_vhd_header(table_offset, max_table_entries, block_size)

        # Build a file: leading footer, header, padding up to file_size-512,
        # trailing footer. No real BAT bytes are present.
        middle_len = file_size - 512 - len(header) - 512
        self.assertGreaterEqual(middle_len, 0)
        middle = b"\x00" * middle_len
        file_bytes = leading_footer + header + middle + trailing_footer
        self.assertEqual(len(file_bytes), file_size)

        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, file_bytes)
        os.close(fd)
        try:
            raised, reads = self._load_with_instrumentation(path)

            self.assertIsNotNone(raised, "load() did not raise ValueError")

            # Pin the exact wording from validate_bat_span in image.py.
            self.assertIn(
                "VHD BAT span [%d, %d) exceeds file size %d"
                % (table_offset, table_offset + bat_bytes_len, file_size),
                str(raised),
            )

            # Exact multiset of reads performed by the real pipeline:
            #   - (0, 512) x2: leading footer, once via
            #     read_head_tail_windows (load()'s classification) and
            #     once via _load_dynamic_vhd's own f.seek(0);
            #     f.read(FOOTER_SIZE).
            #   - (file_size - 512, 512) x2: trailing footer, once via
            #     read_head_tail_windows's tail window and once via
            #     _load_dynamic_vhd's f.seek(file_size - FOOTER_SIZE).
            #   - (header_offset, 1024) x1: header read inside
            #     _load_dynamic_vhd, performed because header_offset
            #     passes validate_header_span; needed to discover
            #     tableOffset/maxTableEntries before BAT span can even
            #     be checked.
            # No BAT-region read (table_offset, bat_bytes_len) occurs:
            # validate_bat_span raises before the bounded
            # f.seek(table_offset); f.read(bat_bytes_len) call.
            expected_reads = collections.Counter({
                (0, 512): 2,
                (file_size - 512, 512): 2,
                (header_offset, 1024): 1,
            })
            self.assertEqual(
                collections.Counter(reads), expected_reads,
                "unexpected multiset of reads observed: %r" % (collections.Counter(reads),),
            )
        finally:
            os.remove(path)

    def test_load_rejects_block_overlap_before_reading_block_region(self):
        """Block/bitmap overlap rejection: footer, header, block size,
        virtual size, BAT capacity, AND BAT span all pass (so the BAT
        itself IS read), but an allocated BAT entry's implied block
        interval overlaps the header. load() must raise, the BAT read
        must have occurred (span passed), but no bitmap/block-region read
        may occur for the invalid allocated entry, since interval
        validation runs before reconstruct_flat_bytes per
        _load_dynamic_vhd's real code order."""
        header_offset = 512
        block_size = 512
        num_blocks = 1
        virtual_size = block_size * num_blocks
        max_table_entries = num_blocks

        table_offset = header_offset + 1024  # 1536
        bat_bytes_len = image.compute_bat_bytes_len(max_table_entries)  # 512

        # Sector-aligned bitmap for a 512-byte block is 1 sector (512 bytes).
        bitmap_sectors = 1
        block_region_len = bitmap_sectors * 512 + block_size  # 1024

        # File layout: leading footer(512) + header(1024) + BAT(512) +
        # block region(1024) + trailing footer(512). Normally the BAT
        # entry would point at the sector right after the BAT
        # (table_offset + bat_bytes_len). To force an overlap with the
        # header, point the BAT entry's sector at the HEADER's own
        # sector offset instead (header_offset // 512), which is
        # entirely within [0, table_offset) and therefore overlaps the
        # header interval.
        overlapping_entry_sector = header_offset // 512  # sector 1

        file_size = table_offset + bat_bytes_len + block_region_len + 512

        leading_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        trailing_footer = _build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
        header = _build_vhd_header(table_offset, max_table_entries, block_size)

        bat_raw = bytearray(bat_bytes_len)
        struct.pack_into(">I", bat_raw, 0, overlapping_entry_sector)

        block_region_padding = b"\x00" * block_region_len

        file_bytes = leading_footer + header + bytes(bat_raw) + block_region_padding + trailing_footer
        self.assertEqual(len(file_bytes), file_size)

        fd, path = tempfile.mkstemp(suffix=".img")
        os.write(fd, file_bytes)
        os.close(fd)
        try:
            raised, reads = self._load_with_instrumentation(path)

            self.assertIsNotNone(raised, "load() did not raise ValueError")

            # Pin the exact wording from validate_disjoint_sorted in
            # image.py: the overlapping entry's claimed interval is
            # [header_offset, header_offset + block_region_len) ==
            # [512, 1536), which exactly equals the header's own
            # interval [header_offset, header_offset + 1024) ==
            # [512, 1536) for this fixture (block_region_len == 1024),
            # so both intervals in the message are identical tuples.
            block_claim_start = overlapping_entry_sector * 512
            block_claim_end = block_claim_start + block_region_len
            self.assertIn(
                "VHD intervals overlap: %r and %r"
                % ((header_offset, header_offset + 1024), (block_claim_start, block_claim_end)),
                str(raised),
            )

            # Exact multiset of reads performed by the real pipeline:
            #   - (0, 512) x2: leading footer, once via
            #     read_head_tail_windows (load()'s classification) and
            #     once via _load_dynamic_vhd's f.seek(0);
            #     f.read(FOOTER_SIZE).
            #   - (file_size - 512, 512) x2: trailing footer, once via
            #     read_head_tail_windows's tail window and once via
            #     _load_dynamic_vhd's f.seek(file_size - FOOTER_SIZE).
            #   - (header_offset, 1024) x1: header read inside
            #     _load_dynamic_vhd, since header_offset passes
            #     validate_header_span.
            #   - (table_offset, bat_bytes_len) x1: the BAT region DID
            #     get read, since [table_offset, table_offset +
            #     bat_bytes_len) is fully in-bounds and passes
            #     validate_bat_span.
            # No bitmap/block read occurs for the invalid allocated
            # entry: validate_disjoint_sorted raises (block interval
            # overlaps the header interval) before reconstruct_flat_bytes
            # -- and therefore before read_bitmap_and_block -- ever runs.
            expected_reads = collections.Counter({
                (0, 512): 2,
                (file_size - 512, 512): 2,
                (header_offset, 1024): 1,
                (table_offset, bat_bytes_len): 1,
            })
            self.assertEqual(
                collections.Counter(reads), expected_reads,
                "unexpected multiset of reads observed: %r" % (collections.Counter(reads),),
            )
        finally:
            os.remove(path)


class TestFixturesModuleDynamicVhdHelpers(unittest.TestCase):
    """Unit tests exercising the tests/fixtures.py Synthetic_Builder
    (dynamic_vhd_image) and the independently hand-encoded dynamic VHD
    fixture (hand_encoded_dynamic_vhd_sample) directly.

    Feature: dynamic-vhd-reader

    Validates: Requirements 5.1, 5.2
    """

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def test_dynamic_vhd_image_round_trips_to_fat12_image_bytes(self):
        """dynamic_vhd_image() with default args wraps fat12_image()'s own
        output; loading it back through image.load() must reconstruct
        flat bytes identical to fixtures.fat12_image()'s own output, and
        report container == "dynamic VHD"."""
        from tests.fixtures import fat12_image, dynamic_vhd_image
        expected = fat12_image()
        data = dynamic_vhd_image()
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(out_data, expected)
            self.assertEqual(info["container"], "dynamic VHD")
        finally:
            os.remove(path)

    def test_hand_encoded_dynamic_vhd_sample_loads_successfully(self):
        """The independently hand-encoded fixture (built via direct
        struct.pack at literal offsets, without calling dynamic_vhd_image
        or its helpers) must itself be a valid dynamic VHD per the real
        implementation: image.load() must succeed without raising."""
        from tests.fixtures import hand_encoded_dynamic_vhd_sample
        data = hand_encoded_dynamic_vhd_sample()
        path = self._write_temp(data)
        try:
            out_data, info = image.load(path)
            self.assertEqual(info["container"], "dynamic VHD")
            self.assertEqual(len(out_data), 512)
        finally:
            os.remove(path)


class TestMutationGenerator(unittest.TestCase):
    """Unit tests for the deterministic mutation generator
    (tests/fixtures.py generate_mutation_iterations), scoped to the
    generator's own metadata -- not to image.load()'s actual behavior on
    the generated iterations (that end-to-end assertion is task 31).

    Feature: dynamic-vhd-reader

    Validates: Requirements 5.3, 5.4, 6.2
    """

    # Footer targets are classified by field name, not by a bare "footer("
    # prefix: both "valid" (footer(N).reserved[k]) and "invalid"
    # (footer(N).cookie[k], footer(N).diskType, footer(N).checksum) targets
    # share the "footer(" prefix, so a bare-prefix match would wrongly flag
    # valid-category footer-reserved targets as checked. A footer target is
    # "checked" iff its field name is cookie/diskType/checksum, and
    # "reserved" iff its field name is "reserved".
    CHECKED_FOOTER_FIELD_SUBSTRINGS = (".cookie[", ".diskType", ".checksum")
    RESERVED_FOOTER_FIELD_SUBSTRING = ".reserved["

    # Header-side prefixes are already field-specific and non-overlapping
    # with "header.reserved_region" (confirmed: cookie, checksum,
    # headerVersion, tableOffset, maxTableEntries, blockSize are each
    # distinct field names, none of which is a prefix of "reserved_region"
    # or vice versa), so they are left as plain prefixes.
    CHECKED_FIELD_TARGET_PREFIXES = (
        "header.cookie", "header.checksum", "header.headerVersion",
        "header.tableOffset", "header.maxTableEntries", "header.blockSize",
    )

    def _is_checked_target(self, target):
        """True iff `target` names a field at least one validate_* function
        inspects. Footer targets are matched on their specific field-name
        substring (never the bare "footer(" prefix); header targets are
        matched on the already-distinct header prefixes."""
        if target.startswith("footer("):
            return any(sub in target for sub in self.CHECKED_FOOTER_FIELD_SUBSTRINGS)
        return any(target.startswith(prefix) for prefix in self.CHECKED_FIELD_TARGET_PREFIXES)

    def test_produces_exactly_100_iterations(self):
        from tests.fixtures import generate_mutation_iterations
        iterations = generate_mutation_iterations()
        self.assertEqual(len(iterations), 100)

    def test_every_iteration_has_determinate_classification(self):
        """Every iteration's "expected" is exactly "valid" or "invalid",
        and "valid" iterations carry non-None expected flat bytes while
        "invalid" iterations carry None (no "either outcome" ambiguity)."""
        from tests.fixtures import generate_mutation_iterations
        iterations = generate_mutation_iterations()
        for it in iterations:
            self.assertIn(it["expected"], ("valid", "invalid"))
            if it["expected"] == "valid":
                self.assertIsNotNone(it["expected_flat_bytes"])
            else:
                self.assertIsNone(it["expected_flat_bytes"])

    def test_same_seed_is_deterministic(self):
        """Calling the generator twice with the same seed produces
        byte-identical iterations in the same order."""
        from tests.fixtures import generate_mutation_iterations
        first = generate_mutation_iterations()
        second = generate_mutation_iterations()
        self.assertEqual([it["data"] for it in first], [it["data"] for it in second])
        self.assertEqual([it["expected"] for it in first], [it["expected"] for it in second])

    def test_valid_iterations_have_self_consistent_footer_and_header_checksums(self):
        """For a sample of "valid" iterations, confirm the mutated VHD's
        leading footer, trailing footer, and header checksums are each
        internally valid (self-consistent) after mutation -- i.e. the
        generator repaired any checksum it was required to repair."""
        from tests.fixtures import generate_mutation_iterations
        iterations = generate_mutation_iterations()
        valid_iterations = [it for it in iterations if it["expected"] == "valid"]
        self.assertGreater(len(valid_iterations), 0)

        for it in valid_iterations:
            data = it["data"]
            leading_raw = data[0:512]
            trailing_raw = data[len(data) - 512:len(data)]
            header_offset = 512
            header_raw = data[header_offset:header_offset + 1024]

            leading_parsed = image.parse_footer(leading_raw)
            trailing_parsed = image.parse_footer(trailing_raw)
            header_parsed = image.parse_header(header_raw)

            # Must not raise: each structure's own checksum must still
            # match its own (possibly mutated) bytes.
            image.validate_footer_checksum(leading_raw, leading_parsed)
            image.validate_footer_checksum(trailing_raw, trailing_parsed)
            image.validate_header_checksum(header_raw, header_parsed)

    def test_invalid_iterations_target_a_field_a_validator_checks(self):
        """For every "invalid" iteration, confirm (by construction/
        metadata, not by calling image.load()) that its recorded target
        name corresponds to a field at least one validate_* function in
        legacyrescue/image.py actually inspects."""
        from tests.fixtures import generate_mutation_iterations
        iterations = generate_mutation_iterations()
        invalid_iterations = [it for it in iterations if it["expected"] == "invalid"]
        self.assertGreater(len(invalid_iterations), 0)

        for it in invalid_iterations:
            target = it["target"]
            self.assertTrue(
                self._is_checked_target(target),
                "invalid-iteration target %r does not match any known checked field" % (target,),
            )
            # The specific footer(...) targets used for "invalid" iterations
            # must be one of cookie/diskType/checksum -- never a "reserved"
            # target, which would belong to the "valid" category.
            if target.startswith("footer("):
                self.assertTrue(
                    any(sub in target for sub in self.CHECKED_FOOTER_FIELD_SUBSTRINGS),
                    "footer-targeted invalid iteration %r is not a checked footer field" % (target,),
                )
                self.assertNotIn(
                    self.RESERVED_FOOTER_FIELD_SUBSTRING, target,
                    "footer-targeted invalid iteration %r unexpectedly targets reserved" % (target,),
                )

    def test_valid_iterations_never_target_reserved_free_fields(self):
        """"valid" iterations must only ever target a reserved/padding
        byte or bit, never a checked field."""
        from tests.fixtures import generate_mutation_iterations
        iterations = generate_mutation_iterations()
        valid_iterations = [it for it in iterations if it["expected"] == "valid"]
        self.assertGreater(len(valid_iterations), 0)
        for it in valid_iterations:
            target = it["target"]
            self.assertFalse(
                self._is_checked_target(target),
                "valid-iteration target %r unexpectedly matches a checked-field prefix" % (target,),
            )
            # A footer-targeted "valid" iteration must specifically be the
            # reserved field -- never cookie/diskType/checksum.
            if target.startswith("footer("):
                self.assertIn(
                    self.RESERVED_FOOTER_FIELD_SUBSTRING, target,
                    "footer-targeted valid iteration %r is not the reserved field" % (target,),
                )
            self.assertTrue(
                ".reserved" in target or "reserved_region" in target
                or target.startswith("bat_padding") or target.startswith("bitmap_padding"),
                "valid-iteration target %r is not a recognized reserved/padding target" % (target,),
            )

    def test_every_iteration_actually_differs_from_baseline(self):
        """Every one of the 100 default-seed iterations must differ from
        the baseline dynamic_vhd_image() bytes in at least one byte
        position. This confirms no iteration (valid or invalid) is an
        accidental no-op mutation -- i.e. that the mutation operations in
        mutate_valid/mutate_invalid always produce a real, observable
        change to the buffer, never a byte/value assignment that happens
        to match what was already there."""
        from tests.fixtures import generate_mutation_iterations, dynamic_vhd_image
        baseline = dynamic_vhd_image()
        iterations = generate_mutation_iterations()
        self.assertEqual(len(iterations), 100)
        for i, it in enumerate(iterations):
            self.assertNotEqual(
                it["data"], baseline,
                "iteration %d (target=%r, expected=%r) is a no-op: "
                "identical to the baseline bytes" % (i, it["target"], it["expected"]),
            )


class TestDynamicVhdRoundTripProperty(unittest.TestCase):
    """Property test for dynamic VHD round-trip reconstruction.

    Feature: dynamic-vhd-reader, Property 1: Dynamic VHD round-trip
    reconstruction

    Validates: Requirements 1.1, 1.4, 5.2
    """

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def _bat_region_layout(self, payload, block_size):
        """Replicate tests.fixtures.dynamic_vhd_image's own layout
        arithmetic to locate the BAT region and compute, for the given
        wrapped payload bytes, which block indices are genuinely
        all-zero in the expected flat bytes (whether because they lie
        entirely past len(payload), or because fat12_image's own
        zero-padding/unused floppy-geometry tail sectors happen to make
        an earlier block all zero too). Returns (table_offset,
        bat_bytes_len, num_blocks, virtual_size, fully_zero_block_indices)."""
        payload_len = len(payload)
        num_blocks = -(-payload_len // block_size)  # ceil
        virtual_size = block_size * num_blocks

        header_offset = 512
        table_offset = header_offset + 1024
        max_table_entries = num_blocks
        bat_bytes_len = -(-max_table_entries * 4 // 512) * 512

        # A block is "fully zero" when its entire content in the expected
        # flat bytes (payload, zero-padded out to virtual_size) is all
        # zero bytes -- not merely when its start offset is past
        # payload_len. This also correctly captures blocks whose content
        # falls within payload_len but happens to be naturally all-zero
        # (e.g. unused tail sectors of the FAT12 floppy geometry).
        expected = payload + b"\x00" * (virtual_size - payload_len)
        zero_block = b"\x00" * block_size
        fully_zero_block_indices = [
            b for b in range(num_blocks)
            if expected[b * block_size:(b + 1) * block_size] == zero_block
        ]
        return table_offset, bat_bytes_len, num_blocks, virtual_size, fully_zero_block_indices

    def _mark_block_sparse_in_bat(self, data, table_offset, block_index):
        """Return a copy of `data` with the BAT entry for `block_index`
        set to 0xFFFFFFFF (unallocated). This is content-preserving only
        when the block's bytes are already all-zero padding, since the
        reconstructed flat bytes for a sparse block are zero-filled."""
        mutated = bytearray(data)
        struct.pack_into(">I", mutated, table_offset + block_index * 4, 0xFFFFFFFF)
        return bytes(mutated)

    def test_round_trip_across_varied_block_size_and_allocation_patterns(self):
        """Generate a modest number of synthetic dynamic VHDs built with
        tests.fixtures.dynamic_vhd_image(), which wraps real
        tests.fixtures.fat12_image() content, varying block_size and the
        underlying FAT12 floppy geometry (total). For a subset of cases,
        additionally mark trailing zero-padding-only blocks as sparse in
        the BAT (content-preserving since their expected bytes are
        already zero) to exercise sparse-block reconstruction without
        fabricating filesystem content. Confirm load() reconstructs flat
        bytes identical to the real fat12_image() output (zero-padded to
        virtual_size), with the correct container and virtual_size."""
        from tests.fixtures import dynamic_vhd_image, fat12_image

        rng = random.Random(20240901)
        block_sizes = [512, 1024, 2048, 4096]
        totals = [720, 1440, 2880]

        sparse_branch_exercised_count = 0

        for i in range(10):
            block_size = rng.choice(block_sizes)
            total = rng.choice(totals)

            payload = fat12_image(total=total)
            data = dynamic_vhd_image(total=total, block_size=block_size)

            virtual_size = block_size * (-(-len(payload) // block_size))
            expected = payload + b"\x00" * (virtual_size - len(payload))

            table_offset, bat_bytes_len, num_blocks, computed_virtual_size, \
                fully_zero_block_indices = self._bat_region_layout(payload, block_size)
            self.assertEqual(computed_virtual_size, virtual_size)

            # For roughly half the cases, if there are any fully-zero
            # blocks available, mark one or more of them as sparse in
            # the BAT. The expected flat bytes are unchanged since those
            # blocks' content is already all zero.
            if fully_zero_block_indices and i % 2 == 0:
                num_to_mark = rng.randrange(1, len(fully_zero_block_indices) + 1)
                targets = rng.sample(fully_zero_block_indices, num_to_mark)
                for block_index in targets:
                    data = self._mark_block_sparse_in_bat(data, table_offset, block_index)
                sparse_branch_exercised_count += 1

            path = self._write_temp(data)
            try:
                out_data, info = image.load(path)
                self.assertEqual(out_data, expected)
                self.assertEqual(info["container"], "dynamic VHD")
                self.assertEqual(info["virtual_size"], virtual_size)
            finally:
                os.remove(path)

        self.assertGreater(
            sparse_branch_exercised_count, 0,
            "none of the 10 iterations found a non-empty fully_zero_block_indices "
            "list and marked a block sparse in the BAT -- the sparse-marking "
            "branch coverage gap has regressed to zero",
        )


class TestMutationRobustnessProperty(unittest.TestCase):
    """Mutation robustness test using the deterministic mutation
    generator (tests.fixtures.generate_mutation_iterations). Runs all
    100 pre-classified iterations against the real public load() API.

    Feature: dynamic-vhd-reader, Property 11: Mutation robustness with
    pre-classified expected outcomes

    Validates: Requirements 5.3, 5.4, 6.2
    """

    def test_all_100_mutation_iterations_match_pre_classified_outcome(self):
        """For each of the 100 pre-classified mutation iterations, write
        the mutated bytes to a real temporary file and call the real
        legacyrescue.image.load(path):
          - "valid" iterations must succeed, returning flat bytes
            exactly equal to the iteration's expected_flat_bytes, with
            info["container"] == "dynamic VHD".
          - "invalid" iterations must raise ValueError specifically; any
            other exception type is a test failure (assertRaises(ValueError)
            lets any other exception type propagate, failing the test).
        No iteration is expected to hang, given the small, fixed-size
        synthetic fixtures involved."""
        from tests.fixtures import generate_mutation_iterations

        iterations = generate_mutation_iterations()
        self.assertEqual(len(iterations), 100)

        for index, it in enumerate(iterations):
            fd, path = tempfile.mkstemp(suffix=".img")
            os.close(fd)
            try:
                with open(path, "wb") as f:
                    f.write(it["data"])

                if it["expected"] == "valid":
                    out_data, info = image.load(path)
                    self.assertEqual(
                        out_data, it["expected_flat_bytes"],
                        "iteration %d (target=%r) did not reconstruct the "
                        "expected flat bytes" % (index, it["target"]),
                    )
                    self.assertEqual(info["container"], "dynamic VHD")
                else:
                    with self.assertRaises(ValueError, msg=(
                        "iteration %d (target=%r) was expected to raise "
                        "ValueError" % (index, it["target"])
                    )):
                        image.load(path)
            finally:
                os.remove(path)


class TestDownstreamModuleEquivalence(unittest.TestCase):
    """Integration-style checks confirming inventory.py, fat.py (via
    image.volumes()'s FatVolume), runbook.py, and report.py each produce
    identical output for the same real FAT12 content whether loaded as a
    raw image or wrapped in a synthetic dynamic VHD -- excluding
    container metadata that legitimately differs.

    Uses only real module functions (legacyrescue.inventory.inspect,
    legacyrescue.image.partitions/volumes, legacyrescue.runbook.build,
    legacyrescue.report.build); never mocks.

    Feature: dynamic-vhd-reader, task 33.

    Validates: Requirements 3.4, 4.4
    """

    def _write_temp(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    def _representative_files_dicts(self):
        """A couple of representative files= payloads for fat12_image(),
        including the default-like set and a smaller alternate set."""
        return [
            None,  # use fat12_image's own default files dict
            {"AUTOEXEC.BAT": b"@ECHO OFF\r\nPATH=C:\\DOS\r\n", "HASPDOS.SYS": b"\x00\x01\x02dongle driver\x03\x04"},
        ]

    def _load_raw_and_dynamic(self, files, total):
        from tests.fixtures import fat12_image, dynamic_vhd_image

        raw_bytes = fat12_image(files, total) if files is not None else fat12_image(total=total)
        dynamic_bytes = dynamic_vhd_image(files=files, total=total) if files is not None else dynamic_vhd_image(total=total)

        raw_path = self._write_temp(raw_bytes)
        dynamic_path = self._write_temp(dynamic_bytes)
        return raw_path, dynamic_path

    def _volume_signatures(self, data):
        """Reduce image.volumes(data)'s (description, FatVolume) pairs to
        a plain, directly-comparable structure: description, kind, label,
        oem, geometry, and the full file listing with contents. This
        exercises the fat.py-backed FatVolume class's real read()/walk()
        behavior directly, independent of inventory.inspect()."""
        sigs = []
        for desc, v in image.volumes(data):
            files = sorted(v.walk(), key=lambda pe: pe[0])
            file_sigs = [
                (path, entry["dir"], entry["size"], None if entry["dir"] else v.read(entry))
                for path, entry in files
            ]
            sigs.append({
                "where": desc, "kind": v.kind, "label": v.label, "oem": v.oem,
                "total": v.total, "bps": v.bps, "files": file_sigs,
            })
        return sigs

    def test_inventory_inspect_equal_excluding_image_key(self):
        """inventory.inspect() on the raw-image path and the dynamic-VHD
        path produce equal results for every top-level key except
        "image" (which legitimately differs: container, possibly
        virtual_size)."""
        for files in self._representative_files_dicts():
            total = 2880
            raw_path, dynamic_path = self._load_raw_and_dynamic(files, total)
            try:
                raw_rep = inventory.inspect(raw_path)
                dynamic_rep = inventory.inspect(dynamic_path)

                self.assertEqual(set(raw_rep.keys()), set(dynamic_rep.keys()))
                for key in raw_rep:
                    if key == "image":
                        continue
                    self.assertEqual(
                        raw_rep[key], dynamic_rep[key],
                        "inventory.inspect() key %r differs between raw and dynamic VHD" % (key,),
                    )
                # The "image" key legitimately differs in container (and
                # possibly virtual_size); confirm that difference exists
                # and is of the expected shape rather than silently
                # matching by accident.
                self.assertEqual(raw_rep["image"]["container"], "raw")
                self.assertEqual(dynamic_rep["image"]["container"], "dynamic VHD")
            finally:
                os.remove(raw_path)
                os.remove(dynamic_path)

    def test_partitions_and_volumes_equal_between_raw_and_dynamic(self):
        """legacyrescue.image.partitions(data) and legacyrescue.image.volumes(data)
        (the fat.py-backed FatVolume path) on the data returned by the two
        image.load() calls are equal between the raw and dynamic-VHD
        paths. There is no standalone tests/test_fat.py, so this is the
        direct way to exercise FatVolume for this equivalence check."""
        for files in self._representative_files_dicts():
            total = 1440
            raw_path, dynamic_path = self._load_raw_and_dynamic(files, total)
            try:
                raw_data, _ = image.load(raw_path)
                dynamic_data, _ = image.load(dynamic_path)

                self.assertEqual(image.partitions(raw_data), image.partitions(dynamic_data))
                self.assertEqual(
                    self._volume_signatures(raw_data),
                    self._volume_signatures(dynamic_data),
                )
            finally:
                os.remove(raw_path)
                os.remove(dynamic_path)

    def test_runbook_build_equal_between_raw_and_dynamic(self):
        """legacyrescue.runbook.build() purely transforms an
        inventory.inspect() report dict (plus an optional emulator boot
        result) into runbook text; it needs no emulator/QEMU when boot is
        None, so it is practical to compare directly. The runbook text
        embeds info["size"] and info["container"], and the image file
        name/path, which legitimately differ between the raw and dynamic
        VHD files (different bytes, different temp paths) -- so those two
        lines are excluded from the comparison, and every other line is
        compared for exact equality."""
        total = 2880
        raw_path, dynamic_path = self._load_raw_and_dynamic(None, total)
        try:
            raw_rep = inventory.inspect(raw_path)
            dynamic_rep = inventory.inspect(dynamic_path)

            raw_runbook = runbook.build(raw_rep)
            dynamic_runbook = runbook.build(dynamic_rep)

            def strip_image_identity_lines(text):
                return [
                    line for line in text.splitlines()
                    if not line.startswith("Image: ") and not line.startswith("Size: ")
                ]

            self.assertEqual(
                strip_image_identity_lines(raw_runbook),
                strip_image_identity_lines(dynamic_runbook),
            )
        finally:
            os.remove(raw_path)
            os.remove(dynamic_path)

    def test_report_build_equal_between_raw_and_dynamic(self):
        """legacyrescue.report.build() purely transforms an
        inventory.inspect() report dict, an optional emulator boot result,
        and a runbook markdown string into a single static HTML file; it
        needs no emulator/QEMU when boot is None (the emulator-specific
        branches of build() are only exercised when boot is truthy), so
        it is practical to compare directly. The emitted HTML embeds the
        image file name, which legitimately differs between the raw and
        dynamic VHD temp files -- that one substring is excluded from the
        comparison, and the rest of the HTML is compared for exact
        equality."""
        total = 2880
        raw_path, dynamic_path = self._load_raw_and_dynamic(None, total)
        raw_out = raw_path + ".report.html"
        dynamic_out = dynamic_path + ".report.html"
        try:
            raw_rep = inventory.inspect(raw_path)
            dynamic_rep = inventory.inspect(dynamic_path)
            raw_runbook_md = runbook.build(raw_rep)
            dynamic_runbook_md = runbook.build(dynamic_rep)

            report.build(raw_rep, None, raw_runbook_md, raw_out)
            report.build(dynamic_rep, None, dynamic_runbook_md, dynamic_out)

            with open(raw_out, "r", encoding="utf-8") as f:
                raw_html = f.read()
            with open(dynamic_out, "r", encoding="utf-8") as f:
                dynamic_html = f.read()

            def normalize(html_text, path, size, container):
                # The image file name, the embedded runbook's "Size: N
                # bytes (container)" line, and the SHA-256 line all
                # legitimately differ between the raw and dynamic VHD
                # files (different bytes, different temp paths), so they
                # are excluded from the comparison; everything else
                # (badges, filesystem text, warnings, inventory sections)
                # must match exactly.
                text = html_text.replace(os.path.basename(path), "")
                text = text.replace("Size: %d bytes (%s)" % (size, container), "")
                return re.sub(r"SHA-256: `[0-9a-f]+`", "SHA-256: ``", text)

            raw_html_norm = normalize(raw_html, raw_path, raw_rep["image"]["size"], raw_rep["image"]["container"])
            dynamic_html_norm = normalize(dynamic_html, dynamic_path, dynamic_rep["image"]["size"], dynamic_rep["image"]["container"])
            self.assertEqual(raw_html_norm, dynamic_html_norm)
        finally:
            for p in (raw_path, dynamic_path, raw_out, dynamic_out):
                if os.path.exists(p):
                    os.remove(p)
