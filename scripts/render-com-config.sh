#!/usr/bin/env bash
# Render the jrsz.net gateway config (config/gateway-com) from the canonical
# jrsz.org config (config/gateway). Keeping this generated avoids hand-maintaining
# two divergent copies.
#
# Substitution rules (order matters):
#   1. AM external URL+port:  am.jrsz.org:8443 -> am.jrsz.net:9443
#      (the jrsz.net AM listens on 9443 internally AND is published on 9443, so
#       this single URL is correct for both IG server-to-server and the browser).
#   2. Browser-facing gateway URLs in the launchpad HTML get the gateway's
#      published port:  https://{appN,ig}.jrsz.org/ -> https://{appN,ig}.jrsz.net:8444/
#   3. Everything else:  jrsz.org -> jrsz.net
#      (route host conditions become portless .com hosts; backend baseURIs keep
#       their internal :8443/:8080/:3000 ports; cookie Domain=jrsz.org -> jrsz.net).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT_DIR}/config/gateway"
DST="${ROOT_DIR}/config/gateway-com"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }
}
require_bin perl

rm -rf "${DST}"
cp -R "${SRC}" "${DST}"

# 1. AM external URL + port (all files).
find "${DST}" -type f -print0 | xargs -0 perl -pi -e 's{am\.jrsz\.org:8443}{am.jrsz.net:9443}g'

# 2. Browser-facing gateway/app URLs in the launchpad need the published port.
perl -pi -e 's{https://(app\d+|ig)\.jrsz\.org/}{https://$1.jrsz.net:8444/}g' \
  "${DST}/scripts/groovy/launchpad.groovy"

# 2b. Browser-facing app URLs inside route files (e.g. app6 logout landing page)
#     also need the gateway's published port. Host conditions stay portless and
#     backend baseURIs (app*-backend, http) do not match this pattern.
find "${DST}/routes" -type f -name '*.json' -print0 \
  | xargs -0 perl -pi -e 's{https://(app\d+|ig)\.jrsz\.org/}{https://$1.jrsz.net:8444/}g'

# 3. Remaining hostnames -> jrsz.net (conditions, backend baseURIs, cookie domain).
find "${DST}" -type f -print0 | xargs -0 perl -pi -e 's{jrsz\.org}{jrsz.net}g'

# 4. Distinct launchpad color scheme for jrsz.net (violet-on-lavender) so the two
#    stacks are obvious at a glance. Swaps only the :root palette block; all
#    launchpad colors are driven from these variables.
PALETTE=':root {
      --bg: #ecebf7;
      --panel: #ffffff;
      --ink: #1c1733;
      --accent: #6d28d9;
      --accent-soft: #e7ddfb;
      --border: #d8d3ec;
      --muted: #5d5680;
      --grad-spot: #efe6fb;
      --grad-top: #f6f3fd;
      --lead: #2c2640;
      --card-ink: #3a3357;
    }'
PALETTE="${PALETTE}" perl -0777 -pi -e 's/:root \{.*?\}/$ENV{PALETTE}/s' \
  "${DST}/scripts/groovy/launchpad.groovy"

echo "Rendered jrsz.net gateway config into ${DST}"
