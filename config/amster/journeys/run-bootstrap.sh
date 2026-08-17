#!/usr/bin/env bash
set -euo pipefail

# Imports the MFA / TOTP / Passkeys / Passwordless authentication journeys into
# the alpha realm. Journeys are defined as self-contained artifacts under
# trees/*.json (tree document + every node body), so a fresh `git pull` plus
# bootstrap recreates them identically on both the jrsz.org and jrsz.net stacks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREES_DIR="${SCRIPT_DIR}/trees"
AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
REALM="${JOURNEYS_REALM:-alpha}"

: "${AM_BASE_URL:?AM_SERVER_URL or AM_URL is required}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }
}
require_bin curl
require_bin python3

REALM_BASE="${AM_BASE_URL}/json/realms/root/realms/${REALM}/realm-config/authentication/authenticationtrees"

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

curl_common=(--silent --show-error --insecure)

authenticate() {
  curl "${curl_common[@]}" \
    -H "X-OpenAM-Username: amadmin" \
    -H "X-OpenAM-Password: ${AM_ADMIN_PWD}" \
    -H "Accept-API-Version: resource=2.1, protocol=1.0" \
    -H "Content-Type: application/json" \
    -X POST \
    "${AM_BASE_URL}/json/realms/root/authenticate" \
    | sed -n 's/.*"tokenId":"\([^"]*\)".*/\1/p'
}

token_id="$(authenticate)"
if [[ -z "${token_id}" ]]; then
  echo "Failed to obtain AM admin session token" >&2
  exit 1
fi

resource_exists() {
  local status
  status="$(curl "${curl_common[@]}" -o /dev/null -w '%{http_code}' \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    "$1")"
  [[ "${status}" == "200" ]]
}

upsert_resource() {
  local url="$1" body_file="$2" match_header="If-None-Match: *"
  if resource_exists "${url}"; then
    match_header="If-Match: *"
  fi
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "${match_header}" \
    -X PUT \
    --data-binary "@${body_file}" \
    "${url}" >/dev/null
}

shopt -s nullglob
for tree_file in "${TREES_DIR}"/*.json; do
  name="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["name"])' "${tree_file}")"
  echo "Importing journey '${name}' into realm '${REALM}'"

  # Emit a manifest of "type id" (PageNodes ordered last so child nodes exist
  # first) plus the per-node and tree bodies into the work dir.
  manifest="$(python3 - "${tree_file}" "${work_dir}" <<'PY'
import json, sys
from pathlib import Path
data = json.load(open(sys.argv[1]))
work = Path(sys.argv[2])
nodes = data["nodes"]
nodes.sort(key=lambda n: 1 if n["type"] == "PageNode" else 0)
lines = []
for i, n in enumerate(nodes):
    f = work / f"node_{i}.json"
    f.write_text(json.dumps(n["body"]))
    lines.append(f"{n['type']} {n['id']} {f}")
tf = work / "tree.json"
tf.write_text(json.dumps(data["tree"]))
lines.append(f"__TREE__ {data['name']} {tf}")
print("\n".join(lines))
PY
)"

  while read -r ntype nid nbody; do
    [[ -z "${ntype}" ]] && continue
    if [[ "${ntype}" == "__TREE__" ]]; then
      upsert_resource "${REALM_BASE}/trees/${nid}" "${nbody}"
    else
      upsert_resource "${REALM_BASE}/nodes/${ntype}/${nid}" "${nbody}"
    fi
  done <<< "${manifest}"
done

echo "Verifying imported journeys"
for tree_file in "${TREES_DIR}"/*.json; do
  name="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["name"])' "${tree_file}")"
  if resource_exists "${REALM_BASE}/trees/${name}"; then
    echo "  ok: ${name}"
  else
    echo "  FAILED: ${name}" >&2
    exit 1
  fi
done

echo "Journey bootstrap complete (realm '${REALM}')"
