#!/usr/bin/env bash
set -euo pipefail
# Run inside WSL2. This prepares upstream files but never installs system software.
state_root="${ICP_PLATFORM_HOME:-$HOME/icp-platforms}"
mkdir -p "$state_root"
prepare_repo() {
  local repo="$1" version="$2" destination="$3"
  if [[ ! -d "$destination/.git" ]]; then
    git clone --depth 1 --branch "$version" "$repo" "$destination"
  fi
  if [[ "$(git -C "$destination" describe --tags --exact-match)" != "$version" ]]; then
    echo "Unexpected platform version; original checkout retained." >&2
    exit 1
  fi
}
prepare_repo https://github.com/langgenius/dify.git 1.17.1 "$state_root/dify"
prepare_repo https://github.com/infiniflow/ragflow.git v0.27.2 "$state_root/ragflow"
echo "Pinned platform sources prepared. Configure private settings before starting; see deployment manual."

