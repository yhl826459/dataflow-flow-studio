#!/usr/bin/env bash
# Install the current checkout into the signed-in user's DataFlow workspace.
set -Eeuo pipefail
umask 077

project=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
home=$(realpath -e -- "$HOME")
target="$home/DataFlow"
revision=316322e22db0556260b7f15bd84c39eaa8727a35
python=${FLOW_STUDIO_INSTALL_PYTHON:-python3.10}

if [[ $(id -u) == 0 || "$(stat -c %u "$home")" != "$(id -u)" ]]; then
    echo "Run this installer directly as your own amax user."
    exit 1
fi
if [[ "$(realpath -m -- "$target")" != "$target" || "$project" == "$target" ]]; then
    echo 'Use a separate, private DataFlow checkout at $HOME/DataFlow.'
    exit 1
fi

if [[ ! -e "$target" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/OpenDCAI/DataFlow.git "$target"
    git -C "$target" checkout --detach "$revision"
fi
if [[ ! -f "$target/pyproject.toml" || ! -d "$target/dataflow" || "$(stat -c %u "$target")" != "$(id -u)" ]]; then
    echo "The DataFlow checkout is missing or belongs to a different user."
    exit 1
fi

# Copy the Git source files only. Account state, secrets and outputs stay in place.
while IFS= read -r -d '' file; do
    case "$file" in
        */.local/*|*/.venv/*|*/dataflow_output/*) continue ;;
        flow-studio/*.py|flow-studio/*.service|flow-studio/dist/*.js|flow-studio/dist/*.css|flow-studio/dist/*.html|extensions/qwen_gencep/*.py|extensions/qwen_gencep/*.html) ;;
        *) continue ;;
    esac
    if [[ ! -f "$project/$file" || -L "$project/$file" || "$(realpath -m -- "$target/$file")" != "$target/$file" ]]; then
        echo "Cannot install $file as a regular file in your DataFlow checkout."
        exit 1
    fi
    install -D -m 644 -- "$project/$file" "$target/$file"
done < <(git -C "$project" ls-files -z -- flow-studio extensions/qwen_gencep)

if [[ -L "$target/.venv" || ( -e "$target/.venv" && "$(stat -c %u "$target/.venv")" != "$(id -u)" ) ]]; then
    echo "Use a virtual environment owned by the current account."
    exit 1
fi
if [[ ! -x "$target/.venv/bin/python" ]]; then
    "$python" -m venv "$target/.venv"
fi
if ! "$target/.venv/bin/python" -c 'from dataflow.core import OperatorABC; from scipy.optimize import linear_sum_assignment' >/dev/null 2>&1; then
    "$target/.venv/bin/python" -m pip install -e "$target" 'scipy==1.15.3'
fi
if [[ ! -x "$target/.venv/bin/uv" ]]; then
    "$target/.venv/bin/python" -m pip install uv
fi

units="$home/.config/systemd/user"
install -d -m 700 -- "$target/flow-studio/.local"
if [[ ! -e "$units/flow-studio.service" && ! -L "$units/flow-studio.service" ]]; then
    install -D -m 644 -- "$target/flow-studio/flow-studio.service" "$units/flow-studio.service"
fi
systemctl --user daemon-reload
systemctl --user enable flow-studio.service
systemctl --user restart flow-studio.service
echo "Workspace: $target/flow-studio"
echo "See WORKBENCH.md for model prerequisites and your own SSH login."
