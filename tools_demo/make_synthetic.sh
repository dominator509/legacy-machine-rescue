#!/bin/sh
# Builds a SYNTHETIC "CNC shop" disk from the FreeDOS Lite image. Not real shop data.
set -e
ROOT=${LEGACYRESCUE_QEMU_ROOT:?}
export LD_LIBRARY_PATH=$ROOT/usr/lib/x86_64-linux-gnu
M=$ROOT/usr/bin
SRC=${1:-images/FD13LITE.img}; OUT=${2:-images/synthetic-cnc-shop.img}
cp "$SRC" "$OUT"
I="$OUT@@32256"
T=$(mktemp -d)
printf 'G21 G90\nG0 X0 Y0\nG1 Z-1.5 F200\nG1 X50 Y0\nM30\n' > $T/PART1.NC
printf '@ECHO OFF\r\nMODE COM1:9600,E,7,1\r\nECHO SYNTHETIC DNC LINK\r\n' > $T/SEND.BAT
printf 'SYNTHETIC PLACEHOLDER, NOT A REAL DRIVER\r\n' > $T/HASPDOS.SYS
printf 'SYNTHETIC PLACEHOLDER, NOT A REAL DRIVER\r\n' > $T/PMACDRV.SYS
$M/mmd -i "$I" ::CNC ::DNC
$M/mcopy -i "$I" $T/PART1.NC ::CNC/PART1.NC
$M/mcopy -i "$I" $T/HASPDOS.SYS ::CNC/HASPDOS.SYS
$M/mcopy -i "$I" $T/PMACDRV.SYS ::CNC/PMACDRV.SYS
$M/mcopy -i "$I" $T/SEND.BAT ::DNC/SEND.BAT
rm -rf $T
