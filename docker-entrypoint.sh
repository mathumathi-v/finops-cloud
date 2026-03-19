#!/bin/sh
# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

# Ensure the data directory is writable by the finops user.
# When Docker creates a volume mount for a non-existent host path,
# the directory may be owned by root, preventing writes.
DATA_DIR="/home/finops/.finops-agent"
if [ ! -w "$DATA_DIR" ]; then
    echo "Warning: $DATA_DIR is not writable. Database will use /tmp fallback." >&2
    export FINOPS_DB_PATH="/tmp/finops.db"
fi

exec finops "$@"
