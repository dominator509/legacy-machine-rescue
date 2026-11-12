"""Deterministic tests for filesystem detection edge cases."""
import os
import tempfile
import struct
import unittest
from legacyrescue import image, inventory, runbook, report
from tests import fixtures


class TestDetection(unittest.TestCase):
    
    def test_bare_fat_early_return(self):
        """Test that bare valid FAT returns empty list (not detected as unreadable)."""
        fat_data = fixtures.fat12_image()
        result = image.unreadable(fat_data)
        self.assertEqual(result, [], "Bare FAT should return empty list")
    
    def test_fat_with_partition_like_bytes(self):
        """Test FAT with actual partition table entries preserves early return."""
        # Create FAT image
        fat_data = bytearray(fixtures.fat12_image())
        # Insert a nonzero partition table entry at offset 446 to make it look like MBR
        # But keep valid FAT boot sector structure
        # FAT already has 0x55AA at 510:512
        # Add partition entry starting at 446
        fat_data[446] = 0x80  # active flag
        fat_data[450] = 0x01  # FAT12 type
        struct.pack_into('<II', fat_data, 454, 63, 2880)  # LBA=63, sectors=2880
        
        result = image.unreadable(bytes(fat_data))
        # Should still return empty (FAT is readable, early return happens)
        self.assertEqual(result, [], "FAT with partition-like bytes should still be readable")
    
    def test_malformed_ntfs_signature(self):
        """Test malformed NTFS OEM string -> Unknown."""
        malformed_ntfs = bytearray(fixtures.ntfs_boot_sector())
        malformed_ntfs[3:11] = b'NFFS    '  # Changed NTFS to NFFS
        result = image.unreadable(bytes(malformed_ntfs))
        
        self.assertEqual(len(result), 1, "Should detect 1 volume")
        expected = {
            "where": "whole image",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": "NFFS"  # Spaces stripped by .strip()
        }
        self.assertEqual(result[0], expected)
    
    def test_malformed_exfat_signature(self):
        """Test malformed exFAT OEM string -> Unknown."""
        malformed_exfat = bytearray(fixtures.exfat_boot_sector())
        malformed_exfat[3:11] = b'EXFIT   '  # Changed EXFAT to EXFIT
        result = image.unreadable(bytes(malformed_exfat))
        
        self.assertEqual(len(result), 1, "Should detect 1 volume")
        expected = {
            "where": "whole image",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": "EXFIT"  # Spaces stripped by .strip()
        }
        self.assertEqual(result[0], expected)
    
    def test_truncated_unknown_input(self):
        """Test truncated input (< 512 bytes) creates Unknown entry."""
        # Test with 100 bytes
        truncated = b'\x00' * 100
        result = image.unreadable(truncated)
        
        # Should create Unknown entry (not empty list)
        expected = {
            "where": "whole image",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": ""  # Empty string (NUL bytes ignored, empty after strip)
        }
        self.assertEqual(result, [expected])
        
        # Test with 511 bytes
        truncated = b'\x00' * 511
        result = image.unreadable(truncated)
        
        expected = {
            "where": "whole image",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": ""  # Empty string
        }
        self.assertEqual(result, [expected])
    
    def test_exact_unknown_raw(self):
        """Test exact Unknown detection for raw data (no MBR)."""
        # Create synthetic unknown data with all zeros for consistent OEM
        unknown_data = b'\x00' * 512 * 10
        # Add boot signature
        unknown_data = bytearray(unknown_data)
        unknown_data[510:512] = b'\x55\xaa'
        result = image.unreadable(bytes(unknown_data))
        
        # Should detect as Unknown at whole image
        expected = [{
            "where": "whole image",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": "\x00\x00\x00\x00\x00\x00\x00\x00"  # 8 NUL characters (bytes 3:11 are all NUL)
        }]
        self.assertEqual(result, expected)
    
    def test_exact_unknown_partition(self):
        """Test exact Unknown detection in MBR partition."""
        mbr = bytearray(512)
        mbr[510:512] = b'\x55\xaa'
        # Create partition entry for type 0x07 (NTFS/HPFS/exFAT) but with unknown OEM
        mbr[446] = 0x80  # active
        mbr[450] = 0x07  # partition type
        struct.pack_into('<II', mbr, 454, 63, 100)  # LBA=63, sectors=100
        
        # Partition data with boot signature but no recognizable OEM
        partition_data = b'\x00' * 512 * 100
        partition_data = bytearray(partition_data)
        partition_data[510:512] = b'\x55\xaa'  # Valid boot signature
        
        disk = bytes(mbr) + b'\x00' * 512 * 62 + bytes(partition_data)
        
        result = image.unreadable(disk)
        
        expected = [{
            "where": "partition 1 (NTFS/HPFS/exFAT)",
            "fs": "Unknown",
            "status": "detected, not readable",
            "oem": "\x00\x00\x00\x00\x00\x00\x00\x00"  # 8 NUL characters (not stripped)
        }]
        self.assertEqual(result, expected)
    
    def test_mixed_fat_unreadable_inventory(self):
        """Test inventory with mixed FAT and unreadable volumes."""
        # Create FAT12 partition
        fat_partition = fixtures.fat12_image(total=2880)
        
        # Create MBR with two partitions: FAT12 and NTFS
        mbr = bytearray(512)
        mbr[510:512] = b'\x55\xaa'
        
        # Partition 1: FAT12
        mbr[446] = 0x80  # active
        mbr[450] = 0x01  # FAT12 type
        struct.pack_into('<II', mbr, 454, 63, 2880)  # LBA=63, sectors=2880
        
        # Partition 2: NTFS
        mbr[462] = 0x00  # not active
        mbr[466] = 0x07  # NTFS type
        # Need to pad NTFS partition to declared size
        ntfs_partition = fixtures.ntfs_boot_sector()
        # ntfs_boot_sector returns 64 sectors, but we declared 100
        # Pad to 100 sectors
        ntfs_data = bytearray(ntfs_partition)
        if len(ntfs_data) < 100 * 512:
            ntfs_data.extend(b'\x00' * (100 * 512 - len(ntfs_data)))
        struct.pack_into('<II', mbr, 470, 63 + 2880, 100)  # LBA after FAT partition
        
        # Build disk
        disk = (bytes(mbr) + 
                b'\x00' * 512 * 62 +  # gap before first partition
                fat_partition + 
                bytes(ntfs_data))
        
        # Test inventory
        fd, path = tempfile.mkstemp(suffix='.img')
        os.write(fd, disk)
        os.close(fd)
        try:
            rep = inventory.inspect(path)
            # Should have 1 readable FAT volume
            self.assertEqual(len(rep["volumes"]), 1)
            self.assertEqual(rep["volumes"][0]["fs"], "FAT12")
            # Should have 1 unreadable volume
            self.assertEqual(len(rep["unreadable_volumes"]), 1)
            self.assertEqual(rep["unreadable_volumes"][0]["fs"], "NTFS")
            self.assertIn("partition 2", rep["unreadable_volumes"][0]["where"])
        finally:
            os.remove(path)
    
    def test_runbook_truthfulness_unreadable(self):
        """Test runbook doesn't guess OS for unreadable volumes."""
        ntfs_data = fixtures.ntfs_boot_sector()
        fd, path = tempfile.mkstemp(suffix='.img')
        os.write(fd, ntfs_data)
        os.close(fd)
        try:
            rep = inventory.inspect(path)
            md = runbook.build(rep)
            
            # Check that runbook doesn't claim OS for unreadable volumes
            # "OS guess:" should not appear in the markdown
            self.assertNotIn("OS guess:", md, "Runbook should not guess OS for unreadable volumes")
            # Check that it says "NOT READABLE"
            self.assertIn("NOT READABLE", md, "Runbook should indicate NOT READABLE")
        finally:
            os.remove(path)
    
    def test_unreadable_only_no_dos_warning(self):
        """Regression: unreadable-only inventory should not warn about missing DOS boot files."""
        ntfs_data = fixtures.ntfs_boot_sector()
        fd, path = tempfile.mkstemp(suffix='.img')
        os.write(fd, ntfs_data)
        os.close(fd)
        try:
            rep = inventory.inspect(path)
            # Should have warning about NTFS
            ntfs_warnings = [w for w in rep["warnings"] if "NTFS" in w]
            self.assertTrue(ntfs_warnings, "Should warn about NTFS")
            # Should NOT have DOS boot file warning
            dos_warnings = [w for w in rep["warnings"] if "DOS" in w or "Windows 9x" in w]
            self.assertFalse(dos_warnings, f"Should not warn about DOS boot files for NTFS, got: {dos_warnings}")
        finally:
            os.remove(path)
    
    def test_report_html_escaping(self):
        """Test report HTML escapes special characters."""
        # Create test data
        ntfs_data = fixtures.ntfs_boot_sector()
        fd, path = tempfile.mkstemp(suffix='.img')
        os.write(fd, ntfs_data)
        os.close(fd)
        
        # Use normal tempfile path (Windows doesn't allow < > in filenames)
        html_fd, html_path = tempfile.mkstemp(suffix='.html')
        os.close(html_fd)
        
        try:
            rep = inventory.inspect(path)
            # Inject script-like text into report data (basename without slash)
            # Use image<tag>&quote.img as basename (no slash)
            rep["image"]["path"] = 'C:\\test\\image<tag>&quot;.img'
            # Also inject into unreadable volume description
            if rep["unreadable_volumes"]:
                rep["unreadable_volumes"][0]["where"] = 'partition <script>alert("xss")</script>'
            
            md = runbook.build(rep)
            # Build report with boot=None (no emulator test)
            report.build(rep, None, md, html_path)
            
            # Read HTML and check escaping
            with open(html_path, 'r', encoding='utf-8') as f:
                html = f.read()
            
            # Check that special characters are escaped in path basename
            # Path gets split on / and takes basename, so 'image<tag>&quot;.img' should be escaped
            self.assertIn('image&lt;tag&gt;&amp;quot;.img', html,
                         "Special characters in path basename should be HTML-escaped")
            
            # Check that partition where field is escaped in runbook
            # The runbook markdown contains the where field, which gets HTML-escaped
            self.assertIn('partition &lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;', html,
                         "Special characters in where field should be HTML-escaped")
            
            # Check that raw script tags are NOT present
            self.assertNotIn('<script>alert("xss")</script>', html,
                           "Raw script tags should not appear in HTML")
            self.assertNotIn('<tag>', html,
                           "Raw tag should not appear in HTML")
            
            # Check basic structure
            self.assertIn('<!doctype html>', html)
            self.assertIn('LEGACY MACHINE RESCUE', html)
            
        finally:
            os.remove(path)
            if os.path.exists(html_path):
                os.remove(html_path)


if __name__ == '__main__':
    unittest.main()