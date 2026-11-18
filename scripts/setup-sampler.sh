#!/usr/bin/env bash
set -euo pipefail

# systemd expands %h for each user; no machine-specific paths are published.
plugin_dir="${HOME}/.config/omarchy/plugins/maks.sysstats-graph"
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
if [[ ! -f "${plugin_dir}/sampler.py" ]]; then
  echo "Install the maks.sysstats-graph plugin before setting up its sampler." >&2
  exit 1
fi

mkdir -p "${unit_dir}"
if [[ -f "${unit_dir}/maks-sysstats-graph.service" ]]; then
  cp -p "${unit_dir}/maks-sysstats-graph.service" \
    "${unit_dir}/maks-sysstats-graph.service.bak.$(date +%Y%m%d-%H%M%S)"
fi
install -m 644 "${plugin_dir}/systemd/maks-sysstats-graph.service" "${unit_dir}/maks-sysstats-graph.service"
systemctl --user daemon-reload
systemctl --user enable --now maks-sysstats-graph.service
systemctl --user restart maks-sysstats-graph.service
echo "Sampler installed and started. Check: systemctl --user status maks-sysstats-graph.service"
