#!/usr/bin/env bash
# Create the offline CA and the hotspot server certificate.
# An existing CA is kept so phones do not have to trust a new root.
# A leaf certificate is renewed in place when it is near expiry or has the wrong address.
set -euo pipefail

DIR=/var/lib/pideck-local
IP=10.42.0.1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      DIR="${2:-}"
      shift 2
      ;;
    --ip)
      IP="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$DIR" ]]; then
  echo "Certificate directory must be an absolute path." >&2
  exit 1
fi
if [[ "$DIR" != /* && ! "$DIR" =~ ^[A-Za-z]:[\\/] ]]; then
  echo "Certificate directory must be an absolute path." >&2
  exit 1
fi

valid_ip() {
  local ip="$1" octet
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  if [[ "$ip" == "0.0.0.0" ]]; then
    return 1
  fi
  IFS=. read -r o1 o2 o3 o4 <<<"$ip"
  for octet in "$o1" "$o2" "$o3" "$o4"; do
    if [[ ${#octet} -gt 1 && "$octet" == 0* ]]; then
      return 1
    fi
    if [[ "$octet" -gt 255 ]]; then
      return 1
    fi
  done
}

if ! valid_ip "$IP"; then
  echo "Refusing certificate address: $IP" >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to create the local certificate." >&2
  exit 1
fi

# Git Bash rewrites arguments that start with a slash. Linux ignores these.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

mkdir -p "$DIR"
chmod 755 "$DIR"

cert_is_current() {
  [[ -s "$DIR/ca.crt" && -s "$DIR/ca.key" && -s "$DIR/cert.pem" && -s "$DIR/key.pem" ]] || return 1
  openssl x509 -in "$DIR/cert.pem" -noout -checkend 2592000 >/dev/null 2>&1 || return 1
  local san
  san="$(openssl x509 -in "$DIR/cert.pem" -noout -ext subjectAltName 2>/dev/null || true)"
  grep -q "IP Address:${IP}" <<<"$san"
}

if cert_is_current; then
  echo "certificate status: kept-existing"
  exit 0
fi

cleanup() {
  rm -f "$DIR/cert.csr" "$DIR/cert.ext"
}
trap cleanup EXIT

if [[ ! -s "$DIR/ca.crt" || ! -s "$DIR/ca.key" ]]; then
  umask 077
  openssl genrsa -out "$DIR/ca.key" 2048
  openssl req -new -x509 -key "$DIR/ca.key" -sha256 -days 3650 \
    -subj "/CN=PiDeck CYBERDECK Local CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -out "$DIR/ca.crt"
  umask 022
  echo "certificate status: created"
else
  echo "certificate status: renewed-leaf"
fi

chmod 600 "$DIR/ca.key"
chmod 644 "$DIR/ca.crt"

umask 077
openssl genrsa -out "$DIR/key.pem" 2048
umask 022
openssl req -new -key "$DIR/key.pem" -subj "/CN=${IP}" -out "$DIR/cert.csr"
cat >"$DIR/cert.ext" <<EOF
basicConstraints=critical,CA:FALSE
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:${IP}
EOF
openssl x509 -req -in "$DIR/cert.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" \
  -CAserial "$DIR/ca.srl" -CAcreateserial \
  -out "$DIR/cert.pem" -days 825 -sha256 -extfile "$DIR/cert.ext"
chmod 640 "$DIR/key.pem"
chmod 644 "$DIR/cert.pem"
openssl verify -CAfile "$DIR/ca.crt" "$DIR/cert.pem" >/dev/null
