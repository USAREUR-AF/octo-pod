#!/usr/bin/env bash
# prove-sapient: a sapient:// egress destination end to end, as `apply` deploys it.
#
#   sudo tools/prove-sapient/run.sh
#
# Linux with systemd, Podman 4.4 or later (for Quadlet), python3 with venv, and openssl,
# run as root because the unit is installed into /etc/containers/systemd. It builds the
# sapient image from this checkout, unless OCTO_PROVE_SAPIENT_IMAGE names one already
# built. What it proves is listed at the top of drive.py.
#
# Safe beside a real pod, but best on a disposable one: the router listens on
# 127.0.0.1:17447 and the controller on 127.0.0.1:48100, the units are named
# octo-egress-controller and octo-egress-refused, and the router container, the units
# and the work directory are removed on exit.
#
# License: Apache-2.0
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
readonly HERE REPO
readonly ROUTER=octo-prove-sapient-zenoh
readonly IMAGE=localhost/octo-sapient:prove
readonly UNITS=/etc/containers/systemd
WORK="$(mktemp -d)"
readonly WORK
export OCTO_PROVE_WORK="${WORK}" OCTO_PROVE_PORT=17447 OCTO_PROVE_PEER_PORT=48100

[[ "$(id -u)" == 0 ]] || {
  echo "run as root: the unit goes into ${UNITS}" >&2
  exit 2
}
for unit in octo-egress-controller octo-egress-refused; do
  if [[ -e "${UNITS}/${unit}.container" ]]; then
    echo "${UNITS}/${unit}.container already exists; refusing to replace it" >&2
    exit 2
  fi
done

cleanup() {
  local unit
  for unit in octo-egress-controller octo-egress-refused; do
    systemctl stop "${unit}.service" >/dev/null 2>&1 || true
    rm -f "${UNITS}/${unit}.container"
  done
  systemctl daemon-reload || true
  for unit in octo-egress-controller octo-egress-refused; do
    systemctl reset-failed "${unit}.service" >/dev/null 2>&1 || true
  done
  podman rm -f "${ROUTER}" >/dev/null 2>&1 || true
  rm -rf "${WORK}"
}
trap cleanup EXIT

# One leaf from the pen CA, with the same openssl commands `apply` uses.
leaf() {
  local name="$1" cn="$2" san="$3" eku="$4"
  local out="${WORK}/tls/${name}"
  openssl ecparam -name prime256v1 -genkey -noout -out "${out}-key.pem"
  openssl req -new -key "${out}-key.pem" -subj "/CN=${cn}" -out "${WORK}/ca/${name}.csr"
  openssl x509 -req -in "${WORK}/ca/${name}.csr" -CA "${WORK}/tls/ca-roots.pem" \
    -CAkey "${WORK}/ca/ca-key.pem" -CAcreateserial -days 825 -sha256 \
    -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=%s\nbasicConstraints=CA:FALSE" \
      "${san}" "${eku}") \
    -out "${out}-cert.pem" 2>/dev/null
}

# A destination's TLS and audit directories, owned and moded as `apply` leaves them.
place() {
  local dest="$1" dir="${WORK}/tls-$1"
  install -d -m 0755 "${dir}"
  install -m 0644 -o root -g root "${WORK}/tls/${dest}-egress-cert.pem" "${dir}/egress-cert.pem"
  install -m 0640 -o root -g 65532 "${WORK}/tls/${dest}-egress-key.pem" "${dir}/egress-key.pem"
  install -m 0644 -o root -g root "${WORK}/tls/ca-roots.pem" "${dir}/ca-root.pem"
  install -d -m 0750 -o 65532 -g 65532 "${WORK}/audit/${dest}"
}

if [[ -n "${OCTO_PROVE_SAPIENT_IMAGE:-}" ]]; then
  podman tag "${OCTO_PROVE_SAPIENT_IMAGE}" "${IMAGE}"
else
  echo "build the sapient image from this checkout"
  podman build -q -f "${REPO}/arms/sapient/Dockerfile" -t "${IMAGE}" "${REPO}/arms" >/dev/null
fi

zenoh_image="$(sed -n 's/^zenoh_image_upstream: *"\([^"]*\)".*/\1/p' \
  "${REPO}/platform/group_vars/all/10-infrastructure.yml")"
[[ -n "${zenoh_image}" ]] || {
  echo "no zenoh_image_upstream in group_vars" >&2
  exit 2
}
export OCTO_PROVE_ZENOH_IMAGE="${zenoh_image}"
podman pull -q "${zenoh_image}" >/dev/null

echo "python environment, and the controller's protobuf modules from the vendored ICD"
python3 -m venv "${WORK}/venv"
"${WORK}/venv/bin/pip" install -q eclipse-zenoh==1.9.0 pyyaml jinja2 grpcio-tools
wkt="$("${WORK}/venv/bin/python" -c \
  'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')"
mkdir -p "${WORK}/gen"
(
  cd "${REPO}/arms/sapient-proto/proto"
  mapfile -t protos < <(find sapient_msg -name '*.proto')
  "${WORK}/venv/bin/python" -m grpc_tools.protoc -I . -I "${wkt}" \
    --python_out="${WORK}/gen" "${protos[@]}"
)

echo "pen CA and leaves"
mkdir -p "${WORK}/ca" "${WORK}/tls"
openssl ecparam -name prime256v1 -genkey -noout -out "${WORK}/ca/ca-key.pem"
openssl req -x509 -new -key "${WORK}/ca/ca-key.pem" -sha256 -days 3650 \
  -subj "/CN=octo-pen-ca" -out "${WORK}/tls/ca-roots.pem" 2>/dev/null
leaf pod octo-pen-router "IP:127.0.0.1,DNS:localhost,DNS:octo-pen-router" "serverAuth,clientAuth"
leaf uplink octo-uplink-bridge "DNS:octo-uplink-bridge" "clientAuth,serverAuth"
leaf bridge octo-downlink-bridge "DNS:octo-downlink-bridge" "clientAuth"
leaf controller-egress octo-egress-controller "DNS:octo-egress-controller" "clientAuth"
leaf refused-egress octo-egress-refused "DNS:octo-egress-refused" "clientAuth"
place controller
place refused
# The router container reads its own directory; the proof deletes it on exit.
chmod 0644 "${WORK}"/tls/*.pem

echo "router configs and units from the templates"
"${WORK}/venv/bin/python" "${HERE}/render.py" "${REPO}" "${WORK}" \
  "${OCTO_PROVE_PORT}" "${OCTO_PROVE_PEER_PORT}" "${IMAGE}"

"${WORK}/venv/bin/python" "${HERE}/drive.py"
