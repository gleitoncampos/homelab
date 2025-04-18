#!/bin/bash
LOGFILE="$1"

if [ -z "$LOGFILE" ]; then
    echo "Usage: $0 /path/to/logfile"
    exit 1
fi

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$LOGFILE.0"
fi