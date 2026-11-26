"""Provision a loopback-only signaling endpoint and root-readable SFU credentials."""
import argparse
import ipaddress
from pathlib import Path
import secrets
import os


def provision(address):
    ip = str(ipaddress.IPv4Address(address))
    directory = Path("/etc/audimate")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.umask(0o077)
    env = directory / "livekit.env"
    key, secret = "room" + secrets.token_hex(10), secrets.token_hex(32)
    if env.exists():
        values = dict(line.split("=", 1) for line in env.read_text().splitlines() if "=" in line)
        key, secret = values["LIVEKIT_API_KEY"], values["LIVEKIT_API_SECRET"]
    env.write_text(f"LIVEKIT_URL=wss://{ip}\nLIVEKIT_INTERNAL_URL=http://127.0.0.1:7880\n"
                   f"LIVEKIT_STATS_URL=http://127.0.0.1:6789/metrics\nLIVEKIT_API_KEY={key}\nLIVEKIT_API_SECRET={secret}\n")
    config = directory / "livekit.yaml"
    config.write_text(f"""port: 7880
bind_addresses: ["127.0.0.1"]
rtc:
  node_ip: {ip}
  use_external_ip: false
  advertise_internal_ip: true
  tcp_port: 7881
  udp_port: 7882
keys:
  {key}: {secret}
prometheus:
  port: 6789
logging:
  level: warn
""")
    env.chmod(0o600)
    config.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("public_ipv4")
    provision(parser.parse_args().public_ipv4)
