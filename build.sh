#!/usr/bin/env bash
# exit on error
set -o errexit

# 安裝系統依賴
apt-get update
apt-get install -y libopus0 libopus-dev
