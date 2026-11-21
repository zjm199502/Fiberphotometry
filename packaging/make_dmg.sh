#!/bin/bash
# Wraps dist/fiberphoto.app into a drag-to-Applications disk image.
#   bash packaging/make_dmg.sh <version> <arch>
set -euo pipefail
ver="$1"; arch="$2"
stage=$(mktemp -d)
cp -R dist/fiberphoto.app "$stage/FiberLume.app"
ln -s /Applications "$stage/Applications"
cp src/fiberphoto/gui/manual/manual_zh.pdf "$stage/用户手册.pdf"
cp src/fiberphoto/gui/manual/manual_en.pdf "$stage/User Manual.pdf"
mkdir -p installer
hdiutil create -volname "FiberLume $ver" -srcfolder "$stage" -fs HFS+ -format UDZO -ov \
  "installer/FiberLume-$ver-macOS-$arch.dmg"
