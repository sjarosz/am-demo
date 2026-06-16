#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${ROOT_DIR}/secrets"
TLS_DIR="${SECRETS_DIR}/tls"
TRUST_DIR="${SECRETS_DIR}/truststores"
PASSWORD_DIR="${SECRETS_DIR}/passwords/gateway"

CA_DIR="${TLS_DIR}/ca"
CA_KEY="${CA_DIR}/jrsz-root-ca.key.pem"
CA_CERT="${CA_DIR}/jrsz-root-ca.cert.pem"
CA_SERIAL="${CA_DIR}/jrsz-root-ca.srl"

DEFAULT_PASSWORD="${DEFAULT_PASSWORD:-changeit}"
DEFAULT_IG_AGENT_PASSWORD="${IG_AGENT_PASSWORD:-igagent-changeit-01}"
CA_DAYS="${CA_DAYS:-3650}"
LEAF_DAYS="${LEAF_DAYS:-825}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required binary: $1" >&2
    exit 1
  }
}

require_bin openssl
require_bin keytool

mkdir -p \
  "${CA_DIR}" \
  "${TLS_DIR}/am" \
  "${TLS_DIR}/gateway" \
  "${TLS_DIR}/ds" \
  "${TLS_DIR}/app1" \
  "${TLS_DIR}/app2" \
  "${TLS_DIR}/app3" \
  "${TLS_DIR}/am-com" \
  "${TLS_DIR}/gateway-com" \
  "${TLS_DIR}/ds-com" \
  "${TLS_DIR}/app1-com" \
  "${TLS_DIR}/app2-com" \
  "${TLS_DIR}/app3-com" \
  "${TRUST_DIR}" \
  "${PASSWORD_DIR}"

write_password_file() {
  local file="$1"
  local value="$2"
  printf "%s" "${value}" > "${file}"
}

generate_ca() {
  if [[ -f "${CA_KEY}" && -f "${CA_CERT}" ]]; then
    echo "Reusing existing CA: ${CA_CERT}"
    return 0
  fi
  openssl req \
    -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -nodes \
    -days "${CA_DAYS}" \
    -subj "/CN=JRSZ Local Root CA/O=JRSZ/OU=Development/C=US" \
    -keyout "${CA_KEY}" \
    -out "${CA_CERT}"
}

generate_leaf_material() {
  local name="$1"
  local common_name="$2"
  local alias="$3"
  shift 3
  local san_entries=("$@")

  local svc_dir="${TLS_DIR}/${name}"
  local key_file="${svc_dir}/${name}.key.pem"
  local csr_file="${svc_dir}/${name}.csr.pem"
  local cert_file="${svc_dir}/${name}.cert.pem"
  local chain_file="${svc_dir}/${name}.chain.pem"
  local p12_file="${svc_dir}/${name}.p12"
  local ext_file="${svc_dir}/${name}.ext"
  local pkcs12_file="${svc_dir}/${name}.fullchain.p12"

  if [[ -f "${p12_file}" ]]; then
    echo "Reusing existing leaf certificate: ${name}"
    return 0
  fi

  {
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=serverAuth"
    printf "subjectAltName="
    local first=1
    local san
    for san in "${san_entries[@]}"; do
      if [[ ${first} -eq 1 ]]; then
        printf "%s" "${san}"
        first=0
      else
        printf ",%s" "${san}"
      fi
    done
    printf "\n"
  } > "${ext_file}"

  openssl req \
    -newkey rsa:2048 \
    -nodes \
    -keyout "${key_file}" \
    -out "${csr_file}" \
    -subj "/CN=${common_name}/O=JRSZ/OU=Development/C=US"

  openssl x509 \
    -req \
    -in "${csr_file}" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -days "${LEAF_DAYS}" \
    -sha256 \
    -extfile "${ext_file}" \
    -out "${cert_file}"

  cat "${cert_file}" "${CA_CERT}" > "${chain_file}"

  openssl pkcs12 \
    -export \
    -name "${alias}" \
    -inkey "${key_file}" \
    -in "${cert_file}" \
    -certfile "${CA_CERT}" \
    -out "${pkcs12_file}" \
    -passout "pass:${DEFAULT_PASSWORD}"

  cp "${pkcs12_file}" "${p12_file}"

  rm -f "${csr_file}" "${ext_file}"
}

generate_gateway_pem_bundle() {
  local dir="${1:-gateway}"
  local name="${2:-gateway}"
  if [[ -f "${TLS_DIR}/${dir}/gateway.server.keypair.pem" ]]; then
    echo "Reusing existing gateway PEM bundle: ${dir}"
    return 0
  fi
  cat \
    "${TLS_DIR}/${dir}/${name}.key.pem" \
    "${TLS_DIR}/${dir}/${name}.cert.pem" \
    "${CA_CERT}" \
    > "${TLS_DIR}/${dir}/gateway.server.keypair.pem"
}

generate_ds_keystore() {
  local dir="${1:-ds}"
  local name="${2:-ds}"
  local ds_dir="${TLS_DIR}/${dir}"
  local ds_store="${ds_dir}/keystore"
  local ds_tmp="${ds_dir}/ds-import.p12"

  if [[ -f "${ds_store}" ]]; then
    echo "Reusing existing DS keystore: ${dir}"
    return 0
  fi

  cp "${ds_dir}/${name}.fullchain.p12" "${ds_tmp}"

  keytool -importcert \
    -noprompt \
    -storetype PKCS12 \
    -keystore "${ds_tmp}" \
    -storepass "${DEFAULT_PASSWORD}" \
    -alias ca-cert \
    -file "${CA_CERT}"

  keytool -genkeypair \
    -alias master-key \
    -keyalg RSA \
    -keysize 2048 \
    -dname "CN=DS Master Key,O=JRSZ,OU=Development,C=US" \
    -storetype PKCS12 \
    -keystore "${ds_tmp}" \
    -storepass "${DEFAULT_PASSWORD}" \
    -keypass "${DEFAULT_PASSWORD}" \
    -validity "${CA_DAYS}" >/dev/null 2>&1

  mv "${ds_tmp}" "${ds_store}"
  write_password_file "${ds_dir}/keystore.pin" "${DEFAULT_PASSWORD}"
}

generate_truststore() {
  local truststore="${TRUST_DIR}/truststore.p12"
  if [[ -f "${truststore}" ]]; then
    echo "Reusing existing truststore: ${truststore}"
    return 0
  fi
  rm -f "${truststore}"

  keytool -importcert \
    -noprompt \
    -storetype PKCS12 \
    -keystore "${truststore}" \
    -storepass "${DEFAULT_PASSWORD}" \
    -alias jrsz-root-ca \
    -file "${CA_CERT}"
}

generate_env_stub() {
  local env_file="${ROOT_DIR}/.env"
  if [[ ! -f "${env_file}" ]]; then
    cp "${ROOT_DIR}/.env.example" "${env_file}"
  fi
}

generate_password_files() {
  write_password_file "${PASSWORD_DIR}/truststore.pass" "${DEFAULT_PASSWORD}"
  write_password_file "${PASSWORD_DIR}/ig.agent.alpha.pass" "${DEFAULT_IG_AGENT_PASSWORD}"
}

generate_oidc_signing_key() {
  # Dedicated /bravo OIDC ID-token signing keypair (registered in horizon AIC).
  OIDC_SIGNING_PASSWORD="${DEFAULT_PASSWORD}" "${ROOT_DIR}/scripts/generate-oidc-signing-key.sh"
}

main() {
  echo "Generating local PKI under ${SECRETS_DIR}"
  generate_ca

  # --- jrsz.org leaves ---
  generate_leaf_material am am.jrsz.org am \
    DNS:am.jrsz.org
  generate_leaf_material gateway ig.jrsz.org gateway \
    DNS:ig.jrsz.org DNS:app1.jrsz.org DNS:app2.jrsz.org DNS:app3.jrsz.org \
    DNS:app4.jrsz.org DNS:app5.jrsz.org DNS:app6.jrsz.org DNS:app7.jrsz.org \
    DNS:app8.jrsz.org DNS:app9.jrsz.org
  generate_leaf_material ds ds.jrsz.org ssl-key-pair \
    DNS:ds.jrsz.org
  generate_leaf_material app1 app1-backend.jrsz.org app1 \
    DNS:app1-backend.jrsz.org
  generate_leaf_material app2 app2-backend.jrsz.org app2 \
    DNS:app2-backend.jrsz.org
  generate_leaf_material app3 app3-backend.jrsz.org app3 \
    DNS:app3-backend.jrsz.org

  generate_gateway_pem_bundle gateway gateway
  generate_ds_keystore ds ds

  # --- jrsz.com leaves (same CA, parallel stack) ---
  generate_leaf_material am-com am.jrsz.com am \
    DNS:am.jrsz.com
  generate_leaf_material gateway-com ig.jrsz.com gateway \
    DNS:ig.jrsz.com DNS:app1.jrsz.com DNS:app2.jrsz.com DNS:app3.jrsz.com \
    DNS:app4.jrsz.com DNS:app5.jrsz.com DNS:app6.jrsz.com DNS:app7.jrsz.com \
    DNS:app9.jrsz.com
  generate_leaf_material ds-com ds.jrsz.com ssl-key-pair \
    DNS:ds.jrsz.com
  generate_leaf_material app1-com app1-backend.jrsz.com app1 \
    DNS:app1-backend.jrsz.com
  generate_leaf_material app2-com app2-backend.jrsz.com app2 \
    DNS:app2-backend.jrsz.com
  generate_leaf_material app3-com app3-backend.jrsz.com app3 \
    DNS:app3-backend.jrsz.com

  generate_gateway_pem_bundle gateway-com gateway-com
  generate_ds_keystore ds-com ds-com

  generate_truststore
  generate_password_files
  generate_oidc_signing_key
  generate_env_stub

  echo "Created:"
  echo "  CA cert: ${CA_CERT}"
  echo "  Gateway PEM bundle (org): ${TLS_DIR}/gateway/gateway.server.keypair.pem"
  echo "  Gateway PEM bundle (com): ${TLS_DIR}/gateway-com/gateway.server.keypair.pem"
  echo "  Shared truststore: ${TRUST_DIR}/truststore.p12"
  echo "  DS keystore (org): ${TLS_DIR}/ds/keystore"
  echo "  DS keystore (com): ${TLS_DIR}/ds-com/keystore"
  echo "  .env file: ${ROOT_DIR}/.env"
}

main "$@"
