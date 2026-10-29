#!/bin/bash
#
# Helpers for the snmp_exporter tests (sourced, not run): an SNMP agent from
# the distribution (snmpd) on localhost, a small MIB for the generator,
# starting and stopping the exporter through its init script, and scraping
# it. They work with the distributions' versions as well as with ours.

# MIB, generator.yml, snmpd's configuration and state; kept between upgrade
# test steps.
SNMP_DIR=/var/tmp/freshdeb-snmp
TARGET=127.0.0.1:16161
SYS_NAME=freshdeb-snmp

# curl and snmpd come from the distribution; the test containers don't have them.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null
command -v snmpd >/dev/null \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends snmpd >/dev/null

show_log() {
    tail -n 40 /var/log/prometheus/prometheus-snmp-exporter.log >&2 || true
}

# The users running a snmp exporter process, one per line. From /proc: the
# images have no procps.
exporter_users() {
    local proc
    for proc in /proc/[0-9]*; do
        [[ "$(cat "$proc/comm" 2>/dev/null)" == prometheus-snmp* ]] && stat -c %U "$proc"
    done
    return 0
}

# The standard MIBs (SNMPv2-MIB, IF-MIB, ...) aren't in Debian or Ubuntu
# (snmp-mibs-downloader is non-free), and the generator needs MIBs. This one
# names two objects of the system group without imports: net-snmp's parser
# knows the SMI types and the iso root by itself.
write_mib() {
    mkdir -p "$SNMP_DIR/mibs"
    cat > "$SNMP_DIR/mibs/FRESHDEB-SMOKE-MIB.txt" <<'EOF'
FRESHDEB-SMOKE-MIB DEFINITIONS ::= BEGIN

fdSystem OBJECT IDENTIFIER ::= { iso 3 6 1 2 1 1 }

fdSysUpTime OBJECT-TYPE
    SYNTAX      TimeTicks
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Time since the agent started."
    ::= { fdSystem 3 }

fdSysName OBJECT-TYPE
    SYNTAX      OCTET STRING (SIZE (0..255))
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "The agent's name."
    ::= { fdSystem 5 }

END
EOF
}

# generate <old|new> <file>: write snmp.yml for module and auth "freshdeb"
# with the installed generator. "old" is the generator.yml format before
# 0.23 (connection settings in each module), "new" the one with an auths
# section.
generate() {
    local format="$1" out="$2" dir
    dir="$(mktemp -d)"
    write_mib
    if [[ "$format" == old ]]; then
        cat > "$dir/generator.yml" <<'EOF'
modules:
  freshdeb:
    walk: [fdSysUpTime, fdSysName]
    version: 2
    auth:
      community: freshdeb
    overrides:
      fdSysName:
        type: DisplayString
EOF
    else
        cat > "$dir/generator.yml" <<'EOF'
auths:
  freshdeb:
    community: freshdeb
    version: 2
modules:
  freshdeb:
    walk: [fdSysUpTime, fdSysName]
    overrides:
      fdSysName:
        type: DisplayString
EOF
    fi
    # generator.yml and snmp.yml in the working directory, MIBs from MIBDIRS:
    # what every version understands.
    (cd "$dir" && MIBDIRS="$SNMP_DIR/mibs" prometheus-snmp-generator generate) >"$dir/generate.log" 2>&1 \
        || { cat "$dir/generate.log" >&2; return 1; }
    install -m 0644 "$dir/snmp.yml" "$out"
    rm -rf "$dir"
}

# An SNMP v2c agent on $TARGET with the communities "freshdeb" and "public"
# (the one of upstream's snmp.yml).
start_snmpd() {
    mkdir -p "$SNMP_DIR"
    printf 'rocommunity freshdeb 127.0.0.1\nrocommunity public 127.0.0.1\nsysName %s\n' "$SYS_NAME" > "$SNMP_DIR/snmpd.conf"
    if ! snmpd -C -c "$SNMP_DIR/snmpd.conf" -Lf "$SNMP_DIR/snmpd.log" -p "$SNMP_DIR/snmpd.pid" "udp:$TARGET"; then
        echo "snmpd does not start" >&2
        tail -n 20 "$SNMP_DIR/snmpd.log" >&2 || true
        return 1
    fi
}

# Stop it and wait until it's gone, so that the next one can bind the port.
stop_snmpd() {
    local pid
    pid="$(cat "$SNMP_DIR/snmpd.pid")"
    kill "$pid"
    for _ in $(seq 1 50); do
        kill -0 "$pid" 2>/dev/null || { rm -f "$SNMP_DIR/snmpd.pid"; return 0; }
        sleep 0.2
    done
    echo "snmpd does not stop" >&2
    return 1
}

# Start the service, wait until it serves its own metrics.
start_exporter() {
    service prometheus-snmp-exporter start
    for _ in $(seq 1 100); do
        curl -fsS http://127.0.0.1:9116/metrics >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    echo "snmp exporter does not answer on port 9116" >&2
    show_log
    return 1
}

stop_exporter() {
    service prometheus-snmp-exporter stop
    for _ in $(seq 1 100); do
        [[ -z "$(exporter_users)" ]] && return 0
        sleep 0.2
    done
    echo "snmp exporter does not stop" >&2
    return 1
}

# expect_scrape <line>...: every line (a metric with its labels and value,
# or a prefix) must be in one scrape of the agent through the exporter.
expect_scrape() {
    local scrape line
    scrape="$(curl -fsS "http://127.0.0.1:9116/snmp?target=$TARGET&module=freshdeb&auth=freshdeb")"
    for line in "$@"; do
        [[ $'\n'"$scrape" == *$'\n'"$line"* ]] || { echo "missing from the scrape: $line" >&2; echo "$scrape" >&2; return 1; }
    done
}

# What every version returns for the freshdeb module.
expect_agent_metrics() {
    expect_scrape 'fdSysUpTime ' "fdSysName{fdSysName=\"$SYS_NAME\"} 1" 'snmp_scrape_pdus_returned'
}
