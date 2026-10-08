#!/usr/bin/env bash
# Download the open MIMIC-IV demo (v2.2) and build its DuckDB file with mimic-code.
#
# Usage: scripts/build_mimic_demo.sh [destination]     (default: ~/physionet/mimic-iv-demo)
#
# Needs wget, git and the DuckDB CLI on PATH (conda env `mimic_build`, see
# environment-mimic-build.yml; CI installs the same version). The demo is open access
# under the ODbL licence, so no PhysioNet account is needed. The full MIMIC-IV database
# is built the same way by hand; see docs/data_governance.md.
#
# Every step is skipped when its output already exists, so the script is safe to re-run.
set -euo pipefail

readonly MIMIC_CODE_COMMIT="303d26c"   # mimic-code, 2026-09-01
readonly DUCKDB_VERSION="v1.4.4"       # the 1.4 LTS line mimic-code builds with
readonly DEMO_URL="https://physionet.org/files/mimic-iv-demo/2.2/"

destination="${1:-$HOME/physionet/mimic-iv-demo}"
mkdir -p "$destination"
destination=$(cd "$destination" && pwd)

found_version=$(duckdb --version 2>/dev/null | awk '{print $1}' || true)
if [ "$found_version" != "$DUCKDB_VERSION" ]; then
    echo "Need the DuckDB CLI $DUCKDB_VERSION on PATH (found: ${found_version:-none})." >&2
    echo "Create it with: mamba env create -f environment-mimic-build.yml" >&2
    exit 1
fi

# 1. Data. -nH and --cut-dirs=3 drop "physionet.org/files/mimic-iv-demo/2.2/", which
#    leaves hosp/ and icu/ directly under raw/ (the layout build_mimic.sh expects).
if [ ! -f "$destination/raw/hosp/patients.csv.gz" ]; then
    echo "Downloading the MIMIC-IV demo from PhysioNet"
    wget -r -N -c -np -nH --cut-dirs=3 -q --reject "index.html*" \
        -4 --tries=5 --retry-connrefused --waitretry=10 --timeout=60 \
        -P "$destination/raw" "$DEMO_URL"
fi

# 2. mimic-code at the pinned commit; only the build scripts and the DuckDB concepts.
if [ ! -d "$destination/mimic-code/.git" ]; then
    git clone --quiet --filter=blob:none --sparse \
        https://github.com/MIT-LCP/mimic-code.git "$destination/mimic-code"
    git -C "$destination/mimic-code" sparse-checkout set mimic-iv/buildmimic mimic-iv/concepts_duckdb
fi
git -C "$destination/mimic-code" checkout --quiet "$MIMIC_CODE_COMMIT"

# 3. Build: schema, data, concepts, then row counts checked against the demo's
#    expected values. build_mimic.sh resumes where it stopped if interrupted.
"$destination/mimic-code/mimic-iv/buildmimic/duckdb/build_mimic.sh" \
    "$destination/raw" "$destination/mimic4_demo.db" 2>&1 | tee "$destination/build_log.txt"

if grep -q "FAILED" "$destination/build_log.txt"; then
    echo "Some tables do not have the expected number of rows; see build_log.txt" >&2
    exit 1
fi
echo "Demo database: $destination/mimic4_demo.db"
