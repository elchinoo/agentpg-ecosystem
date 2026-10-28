#!/bin/sh
# Record the offline terminal segments of docs/VIDEO-SCRIPT.md into docs/video/out/ (cast + transcripts). The video itself: docs/video/build.py.
set -e
cd "$(dirname "$0")/../.."
exec python3 docs/video/record.py "$@"
